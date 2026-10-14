"""Vegas(维纳斯)+斐波那契+MACD 策略 - 4小时诚实回测。

对比: 买入持有 / 旧S1(日线EMA20/60)。次日开盘成交, 单边0.05%手续费。
龙虾USDT走fapi永续(现货无此交易对)。禁止调用 simfunding.init()。
"""
import json
import os
import sys
import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import fetch_klines_many, fetch_futures_klines_many
import vegas_strategy as vs

COINS = {
    "BTCUSDT": 0.45, "ETHUSDT": 0.25, "SOLUSDT": 0.15,
    "XRPUSDT": 0.10, "龙虾USDT": 0.05,
}
WARM = 700
N4H = 1000


def load4h(sym):
    if "USDT" in sym and sym not in ("龙虾USDT",):
        try:
            return fetch_klines_many(sym, "4h", N4H)
        except Exception:
            return fetch_futures_klines_many(sym, "4h", N4H)
    return fetch_futures_klines_many(sym, "4h", N4H)


def load_daily(sym):
    if "龙虾" in sym:
        return fetch_futures_klines_many(sym, "1d", 400)
    return fetch_klines_many(sym, "1d", 400)


def backtest4h(df):
    ind = vs.indicators(df)
    opens = df["open"].values.astype(float)
    closes = df["close"].values.astype(float)
    t0 = df["open_time"].iloc[0].to_pydatetime()
    t1 = df["open_time"].iloc[-1].to_pydatetime()
    pos = 0.0          # 持仓股数
    cash = 100.0
    buys, sells, wins = [], [], 0
    n = len(df)
    bought = False
    entry_idx = None
    eq = []
    for i in range(WARM, n - 1):
        if not bought:
            s, _ = vs.long_signal(ind, i)
            if s:
                qty = cash / opens[i + 1]
                buys.append((i + 1, opens[i + 1]))
                cash = 0.0
                pos = qty
                bought = True
                entry_idx = i + 1
        else:
            s = vs.exit_signal(ind, i)
            if s:
                proceeds = pos * opens[i + 1] * (1 - vs.FEE)
                cash = proceeds * (1 - vs.FEE)
                sell_p = opens[i + 1]
                buy_p = opens[entry_idx]
                wins += 1 if sell_p > buy_p else 0
                sells.append((i + 1, sell_p))
                pos = 0.0
                bought = False
        eq.append(cash + pos * closes[min(i, n - 1)])
    # 结尾强平
    if bought:
        cash = pos * closes[-1] * (1 - vs.FEE) * (1 - vs.FEE)
        pos = 0.0
    eq.append(cash)
    eq = np.array(eq)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    total = eq[-1] / 100.0 - 1
    ntr = len(buys)
    return {
        "window": f"{t0.strftime('%Y-%m-%d %H:%M')} ~ {t1.strftime('%Y-%m-%d %H:%M')}",
        "bars_used": n - WARM - 1,
        "trades": ntr,
        "wins": wins,
        "win_rate": round(wins / ntr, 4) if ntr else None,
        "total": round(total, 4),
        "max_dd": round(float(dd.min()), 4),
        "buy_hold": round(closes[-1] / closes[WARM] - 1, 4),
    }


def s1_daily(sym):
    """旧的S1 日线EMA20/60 (次日开盘成交)。"""
    df = load_daily(sym)
    closes = df["close"].values.astype(float)
    opens = df["open"].values.astype(float)
    e20 = pd.Series(closes).ewm(span=20, adjust=False)
    e60 = pd.Series(closes).ewm(span=60, adjust=False)
    e20 = e20.mean().values
    e60 = e60.mean().values
    cash = 100.0
    pos = 0.0
    bought = False
    n = len(df)
    for i in range(60, n - 1):
        if not bought:
            if e20[i] > e60[i] and closes[i] > e20[i] and e20[i - 1] > e60[i - 1] and closes[i - 1] > e20[i - 1]:
                pos = cash / opens[i + 1]
                cash = 0.0
                bought = True
        else:
            if closes[i] < e60[i]:
                cash = pos * opens[i + 1] * (1 - vs.FEE) * (1 - vs.FEE)
                pos = 0.0
                bought = False
    if bought:
        cash = pos * closes[-1] * (1 - vs.FEE) * (1 - vs.FEE)
    return round(cash / 100.0 - 1, 4)


def main():
    out = {}
    for sym, w in COINS.items():
        try:
            df = load4h(sym)
            st = backtest4h(df)
            st["weight"] = w
            try:
                st["s1_daily"] = s1_daily(sym)
            except Exception as e:
                st["s1_daily"] = None
            out[sym] = st
            print(f"{sym:10s} window={st['window']} trades={st['trades']} "
                  f"win={st['win_rate']} total={st['total']:.2%} "
                  f"mdd={st['max_dd']:.2%} bh={st['buy_hold']:.2%} "
                  f"s1={st['s1_daily']}")
        except Exception as e:
            out[sym] = {"error": str(e)}
            print(f"{sym:10s} ERROR {e}")

    port = 0.0
    bh = 0.0
    for sym, st in out.items():
        if "total" in st and "weight" in st:
            port += st["total"] * st["weight"]
    for sym, st in out.items():
        if "buy_hold" in st and "weight" in st:
            bh += st["buy_hold"] * st["weight"]
    out["_portfolio"] = {"vf": round(port, 4), "buy_hold": round(bh, 4),
                         "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    print(f"[portfolio] vf={port:.2%}  buy&hold={bh:.2%}")
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "vegas_backtest.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("saved vegas_backtest.json")


if __name__ == "__main__":
    main()