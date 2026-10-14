"""维纳斯通道引擎(Vegas Tunnel + 斐波那契 + MACD)。

每5分钟刷新一次, 对9个交易对(含U本位永续 龙虾USDT/AKEUSDT/AINUSDT/LABUSDT/BRUSDT)算4小时指标与共振分。
数据: BTC/ETH/SOL/XRP走现货K线, 龙虾/AKE/AIN/LAB/BR走fapi永续K线。
禁止在本模块内调用 simfunding.init() —— 引擎/回测只借用只读数据。
"""
import json
import os
import time

import pandas as pd

from fetch_data import fetch_klines_many, fetch_futures_klines_many
import vegas_strategy as vs

COINS = [("BTCUSDT", 0.3888, "spot"), ("ETHUSDT", 0.2130, "spot"),
         ("SOLUSDT", 0.1204, "spot"), ("XRPUSDT", 0.0833, "spot"),
         ("龙虾USDT", 0.0463, "fut"), ("AKEUSDT", 0.0371, "fut"),
         ("AINUSDT", 0.0371, "fut"), ("LABUSDT", 0.0371, "fut"),
         ("BRUSDT", 0.0371, "fut")]
N4H = 1000
CHART_N = 90


def _load(sym, kind):
    if kind == "fut":
        return fetch_futures_klines_many(sym, "4h", N4H)
    try:
        return fetch_klines_many(sym, "4h", N4H)
    except Exception:
        return fetch_futures_klines_many(sym, "4h", N4H)


def _load_daily(sym, kind):
    if kind == "fut":
        return fetch_futures_klines_many(sym, "1d", 300)
    try:
        return fetch_klines_many(sym, "1d", 300)
    except Exception:
        return fetch_futures_klines_many(sym, "1d", 300)


def _s1_daily_bull(daily_df):
    closes = pd.Series(daily_df["close"].values.astype(float))
    e20 = closes.ewm(span=20, adjust=False).mean().values
    e60 = closes.ewm(span=60, adjust=False).mean().values
    if len(e20) < 2:
        return False
    return bool(e20[-1] > e60[-1] and float(closes.iloc[-1]) > e20[-1])


def _norm(vals):
    b = vals[0]
    if not b or b != b:
        return [0.0] * len(vals)
    return [(v / b - 1) * 100 for v in vals]


def _atr_levels(df, px):
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1])
    if atr14 <= 0 or atr14 / px > 0.50:
        return None
    return {
        "atr_pct": round(atr14 / px * 100, 2),
        "stop_l": round(float(px - 2 * atr14), 10),
        "tp_l": round(float(px + 4 * atr14), 10),
        "stop_s": round(float(px + 2 * atr14), 10),
        "tp_s": round(float(px - 4 * atr14), 10),
    }


def refresh():
    out = []
    for sym, w, kind in COINS:
        try:
            df = _load(sym, kind)
        except Exception as e:
            out.append({"sym": "龙虾" if sym == "龙虾USDT" else sym.replace("USDT", ""),
                        "price": None, "long": False, "action": "数据失败",
                        "status": str(e)[:60], "color": "#f6465d", "weight": w,
                        "score": 0, "e12": None, "e144": None, "e169": None,
                        "e576": None, "e676": None, "dif": None, "dea": None,
                        "retr": None, "parts": {}, "x": [], "close_norm": []})
            continue
        ind = vs.indicators(df)
        last = len(df) - 1
        parts, score_v = vs.score(ind, last)
        act, st, color = vs.state_text(ind, last)
        long = (act == "持仓做多")
        regime_ok = bool(ind["e576"].iloc[last] > ind["e676"].iloc[last])
        try:
            s1_daily_bull = _s1_daily_bull(_load_daily(sym, kind))
        except Exception:
            s1_daily_bull = False
        # 执行门(回测已验证): S1日线EMA20/60主区 + Vegas共振分折算仓位
        exp = round(w * (score_v / 100.0) if s1_daily_bull else 0.0, 3)
        closes = df["close"].values.astype(float)
        tail = df["open_time"].iloc[-CHART_N:]
        atr = None
        try:
            atr = _atr_levels(df, float(closes[-1]))
        except Exception:
            atr = None
        out.append({
            "sym": "龙虾" if sym == "龙虾USDT" else sym.replace("USDT", ""),
            "price": float(closes[-1]),
            "e12": float(ind["e12"].iloc[last]),
            "e144": float(ind["e144"].iloc[last]),
            "e169": float(ind["e169"].iloc[last]),
            "e576": float(ind["e576"].iloc[last]),
            "e676": float(ind["e676"].iloc[last]),
            "dif": float(ind["dif"].iloc[last]),
            "dea": float(ind["dea"].iloc[last]),
            "retr": (float(ind["retr"].iloc[last])
                     if pd.notna(ind["retr"].iloc[last]) else None),
            "long": long,
            "action": act,
            "status": st,
            "color": color,
            "weight": w,
            "exp": exp,
            "regime_ok": regime_ok,
            "s1": s1_daily_bull,
            "score": score_v,
            "parts": parts,
            "x": [ts.to_pydatetime() for ts in tail],
            "close_norm": _norm(closes[-CHART_N:].tolist()),
            "atr": atr,
        })
    active_w = sum(c["weight"] for c in out if c["long"] and c["price"] is not None)
    suggested = round(sum(c.get("exp", 0.0) for c in out), 3)
    bt = None
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "softhold_backtest.json"),
                  "r", encoding="utf-8") as f:
            bt = json.load(f)
    except Exception:
        bt = None
    return {"coins": out, "active_weight": active_w, "suggested": suggested,
            "ts": int(time.time()),
            "bt": bt}