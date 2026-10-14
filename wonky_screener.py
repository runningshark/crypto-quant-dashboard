import os
import json
import time
import math
import threading
from datetime import datetime, timezone

import requests
import pandas as pd

from config import PROXIES
from fetch_data import fetch_klines_many

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wonky_scan.json")
SCAN_EVERY = 6 * 3600

API_DATA = "https://data-api.binance.vision/api/v3"
API_WWW = "https://www.binance.com/api/v3"
FAPI = "https://fapi.binance.com"

AVOID_BASE = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USD1", "USDE",
              "EUR", "GBP", "TRY", "BRL", "JPY", "BTC", "ETH", "BNB", "WBTC"}
LEV_TAIL = ("BULL", "BEAR", "UP", "DOWN", "1L", "2L", "3L", "5L")
MIN_AGE_DAYS = 730
MAX_QVOL_24H = 1_500_000
MAX_DD_52W = -0.60
MAX_RET_52W = -0.30
MAX_SCAN = 120


def _get(url, params=None, proxies=None, retries=2, timeout=25):
    session = requests.Session()
    session.proxies.update(proxies or {})
    last = None
    for i in range(retries):
        try:
            r = session.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise ConnectionError(f"{url}: {last}")


def _spot_symbols():
    tries = [
        (API_DATA + "/exchangeInfo", None),
        (API_WWW + "/exchangeInfo", PROXIES),
    ]
    last = None
    for u, px in tries:
        try:
            info = _get(u, proxies=px)
            break
        except Exception as e:
            last = e
    else:
        raise last
    out = []
    for s in info.get("symbols", []):
        base = s.get("baseAsset", "")
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        if base in AVOID_BASE or any(base.endswith(t) for t in LEV_TAIL):
            continue
        out.append({"base": base, "symbol": s["symbol"]})
    return out


def _scan(force=False):
    state = {"phase": "market", "done": 0, "total": 0, "errs": 0}
    try:
        tick24 = _get(API_DATA + "/ticker/24hr", proxies=PROXIES)
        volmap = {t["symbol"]: (float(t.get("quoteVolume") or 0),
                                float(t.get("priceChangePercent") or 0))
                  for t in tick24}
    except Exception as e:
        state["phase"] = "market_fail"
        raise
    symbols = _spot_symbols()

    # 便宜过滤: 量小 + 跌幅大
    cand = []
    for s in symbols:
        sm = s["symbol"]
        v, chg = volmap.get(sm, (None, None))
        if v is None or v <= 0:
            continue
        if v > MAX_QVOL_24H:
            continue
        if chg is None:
            continue
        cand.append((v, chg, s))
    cand.sort(key=lambda x: (x[0], x[2]["symbol"]))
    hot_top = sorted(volmap.items(), key=lambda kv: kv[1][0], reverse=True)[:60]
    hot_set = {sm for sm, _ in hot_top}
    cand = [c for c in cand if c[2]["symbol"] not in hot_set]

    # 期货符号集(用来标记有无合约)
    futs = set()
    try:
        fi = _get(FAPI + "/fapi/v1/exchangeInfo", proxies=PROXIES)
        futs = {s["symbol"] for s in fi.get("symbols", [])}
    except Exception:
        pass

    picks = cand[:MAX_SCAN]
    state["phase"] = "klines"
    state["total"] = len(picks)

    now_ts = time.time()
    rows = []
    for i, (v, chg, s) in enumerate(picks):
        state["done"] = i + 1
        sym = s["symbol"]
        try:
            df = fetch_klines_many(sym, "1d", 600, proxies=PROXIES)
        except Exception:
            state["errs"] += 1
            continue
        if df is None or len(df) < 120:
            continue
        closes = df["close"].astype(float)
        high = df["high"].astype(float)
        qv = df["quote_volume"].astype(float)
        n = len(closes)
        # 上市日期由首根日K近似(老币成立, 新币会被年龄过滤剔除)
        first_ts = float(pd.Timestamp(df["open_time"].iloc[0]).value) / 1e6
        age_y = (now_ts - first_ts / 1000) / 86400 / 365
        if age_y < MIN_AGE_DAYS / 365:
            continue  # 新币剔除
        w52 = min(n - 1, 365)
        hi52 = float(high.iloc[-w52:].max())
        close_now = float(closes.iloc[-1])
        dd52 = close_now / hi52 - 1 if hi52 else -1.0
        ret52 = close_now / float(closes.iloc[-w52]) - 1 if w52 > 0 else -1.0
        # 保守口径硬过滤: 52周回撤>=60% 且 全年负收益
        if dd52 > MAX_DD_52W or ret52 > MAX_RET_52W:
            continue
        chg1d = float(closes.iloc[-1] / closes.iloc[-2] - 1) if n > 1 else 0.0
        vol30 = float(qv.iloc[-30:].mean())
        ret30 = close_now / float(closes.iloc[-30]) - 1 if n > 30 else 0.0
        seg = closes.iloc[-w52:]
        down = int((seg.diff().dropna() < 0).sum())
        up = int((seg.diff().dropna() > 0).sum())
        down_ratio = down / max(1, down + up)
        # 阴跌稳健度: 近30日无大涨(天线越少越像妖币的阴跌形态)
        spike30 = float((high.iloc[-30:] / closes.iloc[-31:-1].values - 1).max()) if n > 31 else 0.0

        scarce = math.log10(max(vol30, 1)) if vol30 > 0 else 0
        score = (min(age_y, 4) / 4) * 18 \
            + max(0, -dd52 - 0.60) * 18 \
            + max(0, -ret52 - 0.30) * 14 \
            + down_ratio * 12 \
            + (12 if not sym in futs else 0) \
            + max(0, 2.2 - scarce) * 14 \
            + (8 if spike30 < 0.15 else 0)
        score = round(min(100, score), 1)

        rows.append({
            "symbol": sym, "age_y": round(age_y, 1),
            "onboard": datetime.fromtimestamp(first_ts / 1000,
                                              timezone.utc).strftime("%Y-%m-%d"),
            "price": round(close_now, 8),
            "dd52": round(dd52 * 100, 1), "ret52": round(ret52 * 100, 1),
            "ret30": round(ret30 * 100, 1), "chg24": round(float(chg or 0), 2),
            "vol24": round(v, 0), "vol30": round(vol30, 0),
            "down_ratio": round(down_ratio, 2), "spike30": round(spike30 * 100, 1),
            "has_fut": sym in futs, "score": score,
        })

    rows.sort(key=lambda r: (r["score"], -r["vol24"]), reverse=True)
    pack = {"ts": time.time(), "items": rows[:15], "count": len(rows),
            "scanned": state["total"], "errs": state["errs"]}
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(pack, f, ensure_ascii=False)
    except Exception:
        pass
    return pack


def load_cached():
    try:
        with open(CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def scan_background(state, force=False):
    cached = load_cached()
    if not force and cached and time.time() - cached.get("ts", 0) < SCAN_EVERY:
        if state.get("wonky") is None:
            state["wonky"] = cached
        state["wonky_state"] = "ok"
        return
    try:
        state["wonky_state"] = "scanning"
        res = _scan()
        state["wonky"] = res
        state["wonky_err"] = None
        state["wonky_state"] = "ok"
    except Exception as e:
        state["wonky_err"] = str(e)[:160]
        state["wonky_state"] = "fail"
        if state.get("wonky") is None and cached:
            state["wonky"] = cached


if __name__ == "__main__":
    t0 = time.time()
    res = _scan()
    print("scan seconds:", round(time.time() - t0, 1),
          "count:", res["count"], "scanned:", res["scanned"], "errs:", res["errs"])
    for r in res["items"][:12]:
        print(r["score"], r["symbol"], "age", r["age_y"], "y  dd52",
              r["dd52"], "%  ret52", r["ret52"], "%  vol24", round(r["vol24"]),
              " fut" if r["has_fut"] else "")