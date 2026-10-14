"""资金费率套利模拟器(方向中性·吃资金费) 模拟机构最爱的结构性carry.
做法: 现货等额买入 + 合约同额做空, 每8h吃一次资金费(short收, rate>0时), 忽略方向涨跌.
数据: 真实历史资金费率(fapi), 现价腿用1h镜像K线, 合约腿用funding自带的markPrice.
窗口: 与CUR_DAYS内(~42天) fundingTime 有交集的部分。"""
import os
import json
import time
from datetime import datetime, timezone, timedelta

import requests

from config import PROXIES
from fetch_data import fetch_klines_any, _parse_kline_data

FAPI = "https://fapi.binance.com"
FUND_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "funding_trades.jsonl")
START_CAPITAL = 10000.0
SPOT_FEE = 0.001
FUT_FEE = 0.0005
PERIODS_PER_DAY = 3
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

_state = {
    "initialized": False,
    "equity": [],            # [(ts, equity)]
    "per_coin": {},          # sym -> {...}
    "rates": {},             # sym -> [(fundingTime_ms, rate)]
    "trades": [],
    "cache": {},
}


def _fapi_get(path, params=None):
    r = requests.get(FAPI + path, params=params, timeout=12, proxies=PROXIES)
    r.raise_for_status()
    return r.json()


def _spot_df(sym):
    c = _state["cache"].get(sym)
    if c and time.time() - c["ts"] < 3600:
        return c["df"]
    df = _parse_kline_data(fetch_klines_any(sym, "1h", 1000, None))
    _state["cache"][sym] = {"ts": time.time(), "df": df}
    return df


def _spot_at(df, fund_ms):
    """funding时间对应的最近1h收盘价(资金时间为00/08/16整点, 1h对齐)。"""
    t = fund_ms / 1000.0
    tss = (df["open_time"].astype("int64") // 1000).tolist()
    d = df["close"].tolist()
    for ts, v in zip(tss, d):
        if ts <= t <= ts + 3600:
            return float(v)
    return None


def _fetch_funding(sym):
    data = _fapi_get("/fapi/v1/fundingRate", {"symbol": sym, "limit": 600})
    return [(int(x["fundingTime"]), float(x["fundingRate"]),
             float(x["markPrice"])) for x in data]


def init():
    if _state["initialized"]:
        return
    _state["initialized"] = True
    _state["equity"] = []
    _state["per_coin"] = {}
    _state["rates"] = {}
    if os.path.exists(FUND_FILE):
        os.remove(FUND_FILE)
    for sym in COINS:
        try:
            rates = _fetch_funding(sym)
            df = _spot_df(sym)
            window_start = max(float((df["open_time"].astype("int64") // 1000).iloc[0]),
                               rates[0][0] / 1000.0)
            rows = [(t, r, m) for t, r, m in rates if t / 1000.0 >= window_start]
            if not rows:
                continue
            _state["rates"][sym] = rows
        except Exception as e:
            print(f"simfunding {sym} init fail: {e}")
            continue
        time.sleep(0.3)
    _recompute()
    _snapshot()


def _recompute(full=True):
    for sym, rows in _state["rates"].items():
        df = _spot_df(sym)
        notional = START_CAPITAL / len(COINS)
        spot0 = _spot_at(df, rows[0][0])
        if not spot0:
            continue
        qty = notional / spot0
        fee = notional * (SPOT_FEE + FUT_FEE)
        perp0 = rows[0][2]
        funding_total = 0.0
        last = rows[-1]
        series = []
        for t, rate, mark in rows:
            funding_total += rate * notional
            spot = _spot_at(df, t) or spot0
            fl = (spot - spot0) * qty + (perp0 - mark) * qty
            series.append((t, funding_total, fl))
        spot_now = _spot_at(df, last[0]) or spot0
        avg_rate = sum(r for _, r, _ in rows) / len(rows)
        carry = avg_rate * PERIODS_PER_DAY * 365 * 100
        _state["per_coin"][sym] = {
            "sym": sym,
            "periods": len(rows),
            "spot0": spot0,
            "spot_now": spot_now,
            "qty": qty,
            "fees": fee,
            "funding": funding_total,
            "avg_rate": avg_rate,
            "carry_pct": carry,
            "cur_rate": rows[-1][1],
            "last_mark": last[2],
            "floating": round((spot_now - spot0) * qty + (perp0 - last[2]) * qty, 2),
            "series": series,
            "window_start_day": datetime.fromtimestamp(rows[0][0] / 1000, timezone.utc).strftime("%m-%d"),
            "window_end_day": datetime.fromtimestamp(rows[-1][0] / 1000, timezone.utc).strftime("%m-%d"),
        }


def _snapshot():
    for sym, info in _state["per_coin"].items():
        last_t = info["series"][-1][0]
        _state["equity"].append({"sym": sym, "t": last_t,
                                 "funding": info["funding"],
                                 "floating": info["floating"],
                                 "fees": info["fees"]})
    if len(_state["equity"]) > 500:
        _state["equity"] = _state["equity"][-500:]


def update():
    if not _state["initialized"]:
        init()
    for sym in COINS:
        try:
            nr = _fetch_funding(sym)
            if sym not in _state["rates"] or not nr:
                continue
            new_t = nr[-1][0]
            if _state["rates"][sym][-1][0] < new_t:
                _state["rates"][sym].append(nr[-1])
                t = time.strftime("%Y-%m-%d %H:%M:%S")
                _state["trades"].append(
                    {"sym": sym, "type": "FUND", "rate": nr[-1][1],
                     "funding": round(nr[-1][1] * (START_CAPITAL / len(COINS)), 4),
                     "ts": t})
                with open(FUND_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"sym": sym, "rate": nr[-1][1], "ts": t},
                                       ensure_ascii=False) + "\n")
        except Exception:
            continue
        time.sleep(0.25)
    _recompute()
    _snapshot()


def next_settlement():
    now = time.time()
    # 结算点在每8h整点(00/08/16 UTC)
    ts = datetime.fromtimestamp(now, timezone.utc).replace(minute=0, second=0, microsecond=0)
    ts = ts.replace(hour=(ts.hour // 8) * 8)
    nxt = int(ts.timestamp())
    while nxt <= now:
        nxt += 8 * 3600
    left = nxt - now
    h = int(left // 3600); m = int((left % 3600) // 60); s = int(left % 60)
    cn = datetime.fromtimestamp(nxt, timezone.utc) + timedelta(hours=8)
    return {"left": left, "hms": f"{h:02d}:{m:02d}:{s:02d}",
            "cn_time": cn.strftime("%H:%M"), "ts": nxt}


def summary():
    total_funding = sum(i["funding"] for i in _state["per_coin"].values())
    total_fees = sum(i["fees"] for i in _state["per_coin"].values())
    total_floating = sum(i["floating"] for i in _state["per_coin"].values())
    equity_now = START_CAPITAL - total_fees + total_funding + total_floating
    rows = []
    for sym, i in _state["per_coin"].items():
        rows.append({
            "sym": sym,
            "window": f"{i['window_start_day']}~{i['window_end_day']}",
            "periods": i["periods"],
            "funding": round(i["funding"], 2),
            "cur_rate": i["cur_rate"],
            "carry_pct": round(i["carry_pct"], 1),
            "fees": round(i["fees"], 2),
            "floating": round(i["floating"], 2),
            "net": round(i["funding"] - i["fees"] + i["floating"], 2),
            "price_now": i["spot_now"],
        })
    return {
        "rows": rows,
        "capital": START_CAPITAL,
        "funding": round(total_funding, 2),
        "fees": round(total_fees, 2),
        "floating": round(total_floating, 2),
        "equity": round(equity_now, 2),
        "pnl": round(equity_now - START_CAPITAL, 2),
        "pnl_pct": round((equity_now / START_CAPITAL - 1) * 100, 2),
        "n_new": len(_state["trades"]),
    }