import json
import os
import numpy as np
import pandas as pd

from fetch_data import fetch_klines_many
from simfunding import COINS, _fetch_funding

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_regime.json")
DAYS = 380


def daily(sym):
    df = fetch_klines_many(sym, "1d", DAYS)
    s = pd.Series(df["close"].values.astype(float),
                  index=pd.to_datetime(df["open_time"]))
    return s.sort_index()


def table():
    rows = []
    for sym in COINS:
        s = daily(sym)
        px = s.iloc[-1]
        r7 = px / s.iloc[-8] - 1 if len(s) > 8 else np.nan
        r30 = px / s.iloc[-31] - 1 if len(s) > 31 else np.nan
        r90 = px / s.iloc[-91] - 1 if len(s) > 91 else np.nan
        dd = (s / s.cummax() - 1).iloc[-1] * 100
        ema20 = s.ewm(span=20, adjust=False).mean()
        ema60 = s.ewm(span=60, adjust=False).mean()
        slope60 = (ema60.iloc[-1] / ema60.iloc[-61] - 1) * 100
        bull = ema20.iloc[-1] > ema60.iloc[-1]
        ret30 = s.pct_change().iloc[-30:]
        vol = ret30.std() * np.sqrt(365) * 100
        hi = s.max(); lo = s.min()
        pos = (px - lo) / (hi - lo) * 100
        rows.append({"coin": sym, "px": round(px, 4), "r7_pct": round(r7 * 100, 2),
                     "r30_pct": round(r30 * 100, 2), "r90_pct": round(r90 * 100, 2),
                     "dd_from_high_pct": round(dd, 2), "ema20>ema60": bool(bull),
                     "ema60_slope_60d_pct": round(slope60, 2),
                     "vol_annual_pct": round(vol, 1),
                     "range_pos_pct": round(pos, 1),
                     "last_open": str(s.index[-1].date())})
    return rows


def funding_stmt():
    out = {}
    for sym in COINS:
        rates = _fetch_funding(sym)
        vals = np.array([r for _, r, _ in rates]) * 100
        cur = vals[-1]
        m30 = vals[-90:]
        out[sym] = {"last_8h_pct": round(float(cur), 4),
                    "mean_30d_pct": round(float(m30.mean()), 4),
                    "pct_positive_30d": round(float((m30 > 0).mean()) * 100, 1),
                    "min_30d": round(float(m30.min()), 4),
                    "max_30d": round(float(m30.max()), 4)}
    return out


def corr():
    df = {}
    for sym in COINS:
        s = daily(sym)
        df[sym] = s.pct_change().iloc[-31:]
    return pd.DataFrame(df).corr().round(2).to_dict()


def main():
    rows = table()
    fs = funding_stmt()
    cm = corr()
    print("MARKET REGIME REPORT (window ~380d)")
    print("-" * 80)
    for r in rows:
        print(f"{r['coin']:<9} px={r['px']:<12} r7={r['r7_pct']:+.1f}%  "
              f"r30={r['r30_pct']:+.1f}%  r90={r['r90_pct']:+.1f}%  "
              f"dd={r['dd_from_high_pct']:+.1f}%  bull={'Y' if r['ema20>ema60'] else 'N'}  "
              f"ema60slope60d={r['ema60_slope_60d_pct']:+.1f}%  "
              f"vol_ann={r['vol_annual_pct']}%  range_pos={r['range_pos_pct']}%")
    print("-" * 80)
    for sym, f in fs.items():
        print(f"FUND {sym:<9} last8h={f['last_8h_pct']:+.4f}%  mean30d={f['mean_30d_pct']:+.4f}%  "
              f"pos30d={f['pct_positive_30d']}%  minmax=({f['min_30d']:+.4f},{f['max_30d']:+.4f})%")
    print("-" * 80)
    print("CORR30d:", json.dumps(cm, ensure_ascii=False))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"table": rows, "funding": fs, "corr30": cm},
                  f, ensure_ascii=False, indent=2)
    print("saved:", OUT)


if __name__ == "__main__":
    main()