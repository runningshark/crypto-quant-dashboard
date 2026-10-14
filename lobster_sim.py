import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

from fetch_data import fetch_futures_klines_many, _parse_kline_data
from strategies import (
    build_features,
    run_strategy,
    strat_donchian,
    strat_ema20,
    strat_ema_cross,
    strat_rsi,
    strat_mom,
)

SYM = "龙虾USDT"
INTERVAL = "1h"
CANDLES = 3000

STRATS = {
    "唐奇安突破20": strat_donchian,
    "EMA20趋势": strat_ema20,
    "EMA8/21交叉": strat_ema_cross,
    "RSI14超卖": strat_rsi,
    "动量(2日线)": strat_mom,
}


def load_fut(sym):
    raw = fetch_futures_klines_many(sym, INTERVAL, CANDLES)
    df = _parse_kline_data(raw)
    return df


def main():
    df = load_fut(SYM)
    h = df["high"].astype(float).values
    l = df["low"].astype(float).values
    c = df["close"].astype(float).values
    n = len(c)
    start = df["open_time"].iloc[0]
    end = df["open_time"].iloc[-1]
    print(f"loaded {SYM}: {n} bars  ({start:%Y-%m-%d %H:%M} -> {end:%Y-%m-%d %H:%M}, ~{n/24:.0f}天)")
    print(f"price range: {c.min():.8f} ~ {c.max():.8f}, last close {c[-1]:.8f}")

    feat = build_features(h, l, c)

    print("\n=== 1h模拟回测(2%止损, 无真实手续费/资金费率) ===")
    for sname, fn in STRATS.items():
        r = run_strategy(feat, fn)
        pf_s = f"{r['pf']:.2f}" if r["pf"] is not None else "-"
        print(f"  {sname:16s} 收益{r['total']:+7.1f}%  胜率{r['win_rate']:5.1f}%  "
              f"PF {pf_s:>5s}  {r['trades']:3d}笔  回撤{r['mdd']:5.1f}%  "
              f"(赢{r['wins']}/亏{r['losses']})")


if __name__ == "__main__":
    main()