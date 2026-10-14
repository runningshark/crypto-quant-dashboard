"""唐奇安突破20 与 EMA8/21交叉 的长周期稳健性回测(约3个月1h)。
跑法相同, 拉长数据验证是否过拟合。"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

from fetch_data import fetch_klines_many, _parse_kline_data
from strategies import (
    ema, sma, rsi,
    run_strategy, strat_donchian, strat_ema_cross,
    build_features,
)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
INTERVAL = "1h"
CANDLES = 2200   # ~3个月

STRATS = {
    "唐奇安突破20": strat_donchian,
    "EMA8/21交叉": strat_ema_cross,
}


def load_long(sym):
    raw = fetch_klines_many(sym, INTERVAL, CANDLES)
    df = _parse_kline_data(raw)
    return (df["high"].astype(float).values, df["low"].astype(float).values,
            df["close"].astype(float).values)


def main():
    data = {}
    for sym in SYMBOLS:
        h, l, c = load_long(sym)
        data[sym] = build_features(h, l, c)
        print(f"loaded {sym}: {len(c)} bars "
              f"({pd.Timestamp.utcnow() - pd.Timedelta(hours=len(c)):.0f} -> now, ~{len(c)/24:.0f}天)")

    print("\n=== 长周期回测(2%止损): 收益% / 胜率% / 盈亏比 / 交易数 / 最大回撤% ===")
    summary = {}
    for sname, fn in STRATS.items():
        print(f"\n--- {sname} ---")
        rows = []
        for sym in SYMBOLS:
            r = run_strategy(data[sym], fn)
            rows.append(r)
            pf_s = f"{r['pf']:.2f}" if r['pf'] is not None else "-"
            print(f"  {sym:9s} {r['total']:+7.1f}%   胜率{r['win_rate']:5.1f}%   "
                  f"PF {pf_s:>5s}   {r['trades']:3d}笔   回撤{r['mdd']:5.1f}%")
        summary[sname] = rows

    print("\n=== 跨币平均 ===")
    for sname, rows in sorted(summary.items(),
                              key=lambda kv: -np.mean([r["total"] for r in kv[1]])):
        print(f"{sname:12s} 平均收益 {np.mean([r['total'] for r in rows]):+6.1f}%  "
              f"PF {np.mean([r['pf'] for r in rows if r['pf'] is not None]):5.2f}  "
              f"回撤{np.mean([r['mdd'] for r in rows]):4.1f}%  "
              f"{sum(r['trades'] for r in rows)}笔")


if __name__ == "__main__":
    main()