import json
import os
import numpy as np
import pandas as pd

from config import PROXIES
from fetch_data import fetch_klines_many
from simfunding import COINS, _fetch_funding, _spot_df, _spot_at, SPOT_FEE, FUT_FEE

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "combat_backtest.json")
CAPITAL = 10000.0
A_SHARE = 0.6
B_SHARE = 0.4
O_NA = CAPITAL * A_SHARE / len(COINS)
B_NA = CAPITAL * B_SHARE / len(COINS)
EMA_N = 20
ER_N = 20
ER_GATE = 0.15
STOP = 0.02
HOURS = 4000


def engine_a():
    curves = {}
    window_starts = []
    for coin in COINS:
        rates = _fetch_funding(coin)
        df = fetch_klines_many(coin, "1h", HOURS)
        win = max(int((df["open_time"].astype("int64") // 1000).iloc[0]),
                  rates[0][0] // 1000)
        rows = [(t, r, m) for t, r, m in rates if t // 1000 >= win]
        if not rows:
            continue
        spot0 = _spot_at(df, rows[0][0])
        if not spot0:
            continue
        qty = O_NA / spot0
        fee = O_NA * (SPOT_FEE + FUT_FEE)
        perp0 = rows[0][2]
        funding = 0.0
        pts = []
        for t, r, m in rows:
            funding += r * O_NA
            spot = _spot_at(df, t) or spot0
            fl = (spot - spot0) * qty + (perp0 - m) * qty
            eq = O_NA - fee + funding + fl
            pts.append((pd.to_datetime(t // 1000, unit="s"), eq))
        s = pd.Series(dict(pts)).sort_index()
        curves[coin] = s
        window_starts.append(s.index.min())
        window_starts.append(s.index.max())
    return curves


def engine_b():
    curves = {}
    trade_stats = {"trades": [], "n": 0, "win": 0}
    fee = 0.0005
    for coin in COINS:
        df = fetch_klines_many(coin, "1h", HOURS)
        ts = (df["open_time"].astype("int64") // 1000).values
        o = df["open"].values.astype(float)
        h = df["high"].values.astype(float)
        lo = df["low"].values.astype(float)
        c = df["close"].values.astype(float)
        ema = pd.Series(c).ewm(span=EMA_N, adjust=False).mean().values
        er = np.full(len(c), np.nan)
        for i in range(ER_N, len(c)):
            raw = c[i - ER_N + 1:i + 1]
            total = np.sum(np.abs(np.diff(raw))) if len(raw) > 1 else 0.0
            er[i] = (abs(c[i] - c[i - ER_N]) / total) if total > 0 else 0.0
        cash, qty, entry = B_NA, 0.0, 0.0
        eq_pts = []
        for i in range(0, len(c) - 1):
            sig_long = c[i] > ema[i]
            gate = er[i] >= ER_GATE
            if qty == 0:
                if sig_long and gate:
                    qty = (cash / o[i + 1]) * (1 - fee)
                    cash = 0.0
                    entry = o[i + 1]
            else:
                if lo[i] <= entry * (1 - STOP):
                    cash = qty * entry * (1 - STOP) * (1 - fee)
                    trade_stats["trades"].append({"coin": coin, "pnl_pct": -STOP * 100,
                                                  "reason": "stop"})
                    trade_stats["n"] += 1
                    qty, entry = 0.0, 0.0
                elif not sig_long:
                    cash = qty * o[i + 1] * (1 - fee)
                    net = cash - B_NA
                    trade_stats["trades"].append({"coin": coin,
                                                  "pnl_pct": net / B_NA * 100,
                                                  "reason": "trend"})
                    trade_stats["n"] += 1
                    if net > 0:
                        trade_stats["win"] += 1
                    qty, entry = 0.0, 0.0
            equity = cash + qty * c[i]
            eq_pts.append((pd.to_datetime(ts[i], unit="s"), equity))
        s = pd.Series(dict(eq_pts)).sort_index()
        curves[coin] = s
    return curves, trade_stats


def main():
    a_curves = engine_a()
    b_curves, b_stats = engine_b()

    start = min(s.index.min() for s in a_curves.values())
    end = max(s.index.max() for s in a_curves.values())
    grid = pd.date_range(start, end, freq="h")

    a_df = pd.DataFrame(index=grid)
    for coin, s in a_curves.items():
        a_df[coin] = s.reindex(grid).ffill()
    a_df = a_df.fillna(O_NA)
    a_total = a_df.sum(axis=1)

    b_df = pd.DataFrame(index=grid)
    for coin, s in b_curves.items():
        b_df[coin] = s.reindex(grid).ffill()
    b_df = b_df.fillna(B_NA)
    b_total = b_df.sum(axis=1)

    combined = a_total + b_total
    peak = combined.cummax()
    max_dd = float(((combined / peak - 1) * 100).min())
    pnl_a = float(a_total.iloc[-1]) - CAPITAL * A_SHARE
    pnl_b = float(b_total.iloc[-1]) - CAPITAL * B_SHARE
    pnl_c = float(combined.iloc[-1]) - CAPITAL

    last = fetch_klines_many("BTCUSDT", "1h", HOURS)
    bh = float(last["close"].iloc[-1]) / float(last["close"].iloc[0]) - 1

    sstart = grid[0].strftime("%Y-%m-%d %H:%M")
    send = grid[-1].strftime("%Y-%m-%d %H:%M")
    days = (grid[-1] - grid[0]).total_seconds() / 86400
    wins = 0
    for t in b_stats["trades"]:
        wins += 1 if t["pnl_pct"] > 0 else 0

    print("=" * 88)
    print(f"组合回测窗口: {sstart} → {send}  (共{days:.1f}天, 小时级净值)")
    print("-" * 88)
    print(f"{'引擎':<10}{'期初':>8}{'期末':>10}{'盈亏':>10}{'收益率':>9}{'占比':>7}")
    print(f"{'引擎A套利':<10}{CAPITAL*A_SHARE:>8.0f}{a_total.iloc[-1]:>10.0f}"
          f"{pnl_a:>+10.2f}{pnl_a/(CAPITAL*A_SHARE)*100:>+8.2f}%{60:>6d}%")
    print(f"{'引擎B趋势':<10}{CAPITAL*B_SHARE:>8.0f}{b_total.iloc[-1]:>10.0f}"
          f"{pnl_b:>+10.2f}{pnl_b/(CAPITAL*B_SHARE)*100:>+8.2f}%{40:>6d}%")
    print(f"{'组合':<10}{CAPITAL:>8.0f}{combined.iloc[-1]:>10.0f}"
          f"{pnl_c:>+10.2f}{pnl_c/CAPITAL*100:>+8.2f}%{100:>5d}%")
    print("-" * 88)
    print(f"组合最大回撤: {max_dd:.2f}%   引擎B交易次数: {b_stats['n']}"
          f"   B盈利数: {wins}")
    if b_stats["n"]:
        print(f"引擎B命中率: {wins/b_stats['n']*100:.1f}%")
    else:
        print("引擎B在窗口内未触发趋势信号(ER<15% 或 无EMA20上穿) → 全程持币待机")
    print(f"对照组: 同期买入持有BTC = {bh*100:+.1f}%")
    print("=" * 88)
    for t in b_stats["trades"][:25]:
        print(f"  B {t['coin']} {t['reason']} {t['pnl_pct']:+.2f}%")

    summary = {"window": {"start": sstart, "end": send, "days": round(days, 1)},
               "A": {"start": round(CAPITAL*A_SHARE, 2), "end": round(a_total.iloc[-1], 2),
                     "pnl": round(pnl_a, 2), "pct": round(pnl_a/(CAPITAL*A_SHARE)*100, 2)},
               "B": {"start": round(CAPITAL*B_SHARE, 2), "end": round(b_total.iloc[-1], 2),
                     "pnl": round(pnl_b, 2), "pct": round(pnl_b/(CAPITAL*B_SHARE)*100, 2),
                     "trades": b_stats["n"], "win": wins,
                     "win_rate": round(wins/b_stats["n"], 4) if b_stats["n"] else None},
               "combined": {"end": round(combined.iloc[-1], 2), "pnl": round(pnl_c, 2),
                            "pct": round(pnl_c/CAPITAL*100, 2),
                            "max_dd_pct": round(max_dd, 2)},
               "benchmark_btc_hold": round(bh*100, 2)}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"已存: {OUT}")


if __name__ == "__main__":
    main()