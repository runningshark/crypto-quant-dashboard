import json
import os
import numpy as np
import pandas as pd

from fetch_data import fetch_klines_many
from simfunding import COINS

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trend_slow.json")
FEE = 0.0005
WEIGHTS = {"BTCUSDT": 0.50, "ETHUSDT": 0.25, "SOLUSDT": 0.15, "XRPUSDT": 0.10}


def daily(sym, n=200):
    df = fetch_klines_many(sym, "1d", n)
    s = pd.Series(df["close"].values.astype(float),
                  index=pd.to_datetime(df["open_time"]))
    return s.sort_index()


def run(weights):
    nav = 1.0
    trades = 0
    exits = []
    series = []
    t0 = None
    for sym, w in weights.items():
        s = daily(sym)
        ema20 = s.ewm(span=20, adjust=False).mean()
        ema60 = s.ewm(span=60, adjust=False).mean()
        long = (ema20 > ema60) & (s > ema20)
        long = long.reindex(s.index).ffill().fillna(False)
        cash = w
        qty = 0.0
        curve = []
        for i in range(1, len(s)):
            if qty == 0 and long.iloc[i] and long.iloc[i - 1]:
                qty = cash * (1 - FEE) / s.iloc[i]
                cash = 0.0
                trades += 1
            elif qty > 0 and not long.iloc[i]:
                cash = qty * s.iloc[i] * (1 - FEE)
                exits.append(cash / w - 1)
                qty = 0.0
            eq = cash + qty * s.iloc[i]
            curve.append((s.index[i], eq))
            if t0 is None:
                t0 = s.index[i]
        cs = pd.Series(dict(curve)).sort_index()
        cs = cs / cs.iloc[0] * w
        series.append(cs)
    comb = pd.concat(series).groupby(level=0).sum().sort_index()
    peak = comb.cummax()
    mdd = float(((comb / peak - 1) * 100).min())
    return {"comb": comb, "mdd": mdd, "trades": trades,
            "exit_n": len(exits), "win_n": int(sum(1 for e in exits if e > 0))}


def main():
    res = run(WEIGHTS)
    comb = res["comb"]
    span_days = (comb.index[-1] - comb.index[0]).days
    total = float(comb.iloc[-1]) - 1.0
    btc = daily("BTCUSDT")
    bhc = float(btc.iloc[-1]) / float(btc.iloc[-5 * 8 - 1]) - 1.0

    s = comb.index[0].strftime("%Y-%m-%d")
    e = comb.index[-1].strftime("%Y-%m-%d")
    t1 = comb.index[0]
    w = btc.loc[t1:]

    print("=" * 76)
    print(f"S1 slow-trend window: {s} -> {e}  ({span_days}d, daily nav)")
    print("-" * 76)
    print(f"S1 return        : {total * 100:+.2f}%      maxDD {res['mdd']:.1f}%")
    print(f"    total trades : {res['trades']}   exits {res['exit_n']}  win {res['win_n']}")
    print(f"BTC buy&hold     : {(float(btc.iloc[-1]) / float(w.iloc[0]) - 1) * 100:+.2f}%")
    print(f"Old B engine(1h) : -1.69% (651 trades, 5.2% win, same 5.5m window)")
    print("=" * 76)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"window": {"start": s, "end": e, "days": int(span_days)},
                   "s1_return_pct": round(total * 100, 2),
                   "s1_maxdd_pct": round(res["mdd"], 2),
                   "s1_trades": res["trades"], "s1_exits": res["exit_n"],
                   "s1_win": res["win_n"],
                   "benchmark_btc_hold_pct": round((float(btc.iloc[-1]) / float(w.iloc[0]) - 1) * 100, 2)},
                  f, ensure_ascii=False, indent=2)
    print("saved:", OUT)


if __name__ == "__main__":
    main()