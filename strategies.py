"""多策略对比回测: 同一批1h K线, 同一风控(2%止损), 长多策略。
输出每策略每币: 交易数/胜率/盈亏比/总收益/最大回撤。"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

from fetch_data import fetch_klines_any, _parse_kline_data

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
INTERVAL = "1h"
LIMIT = 1500
STOP = 0.02


def ema(vals, n):
    out = np.empty(len(vals))
    out[0] = vals[0]
    k = 2.0 / (n + 1)
    for i in range(1, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1 - k)
    return out


def sma(vals, n):
    return pd.Series(vals).rolling(n).mean().values


def rsi(close, n=14):
    d = np.diff(close)
    up = np.clip(d, 0, None)
    dn = np.clip(-d, 0, None)
    ru = pd.Series(up).rolling(n).mean().values
    rd = pd.Series(dn).rolling(n).mean().values
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = ru / np.where(rd == 0, np.nan, rd)
        out = 100 - 100 / (1 + rs)
    return np.concatenate([[50.0], np.nan_to_num(out, nan=50.0)])


def load(sym):
    df = _parse_kline_data(fetch_klines_any(sym, INTERVAL, LIMIT, None))
    return (df["high"].astype(float).values, df["low"].astype(float).values,
            df["close"].astype(float).values)


def run_strategy(features, fn, stop=STOP):
    """fn(i, feat) -> (enter, exit). long-only. stop优先于信号退出。"""
    n = len(features["close"])
    pos = 0.0
    entry = 0.0
    equity = 1.0
    trades, wins, losses = 0, 0, 0
    gross_w = gross_l = 0.0
    eq_curve = []
    for i in range(n):
        high, low, close = features["high"][i], features["low"][i], features["close"][i]
        if pos == 0:
            enter, _ = fn(i, features)
            if enter:
                pos = 1.0
                entry = close
            else:
                eq_curve.append(equity)
                continue
        # in position
        if low <= entry * (1 - stop):
            exit_price = entry * (1 - stop)
        else:
            _, exit_sig = fn(i, features)
            if exit_sig:
                exit_price = close if close != entry else high
            else:
                eq_curve.append(equity)
                continue
        ret = exit_price / entry - 1
        equity *= (1 + ret)
        trades += 1
        if ret > 0:
            wins += 1
            gross_w += ret
        else:
            losses += 1
            gross_l += abs(ret)
        pos = 0.0
        eq_curve.append(equity)
    # 未平仓按末价清算
    if pos:
        ret = close / entry - 1
        equity *= (1 + ret)
        trades += 1
        if ret > 0:
            wins += 1
            gross_w += ret
        else:
            losses += 1
            gross_l += abs(ret)
    pf = gross_w / gross_l if gross_l > 0 else (None if gross_w == 0 else 999.0)
    # 最大回撤
    curve = np.array(eq_curve if eq_curve else [1.0])
    peak = np.maximum.accumulate(curve)
    mdd = float(np.max((peak - curve) / peak))
    return {"trades": trades, "wins": wins, "losses": losses,
            "win_rate": wins / trades * 100 if trades else 0.0,
            "pf": pf, "total": (equity - 1) * 100, "mdd": mdd * 100}


def strat_ema20(i, f):
    e = f["ema20"]
    if e[i] != e[i] or e[i - 1] != e[i - 1]:
        return False, False
    return (f["close"][i] > e[i] and e[i] > e[i - 1]), (f["close"][i] < e[i])


def strat_ema_cross(i, f):
    a, b = f["ema8"], f["ema21"]
    if np.isnan(a[i]) or np.isnan(b[i]):
        return False, False
    return (a[i] > b[i] and a[i - 1] <= b[i - 1]), (a[i] < b[i])


def strat_donchian(i, f):
    win = f["highest20"]
    return (f["close"][i] > win[i] and f["close"][i - 1] <= win[i - 1]), \
           (f["close"][i] < f["lowest20"][i])


def strat_boll(i, f):
    lo, mid = f["boll_lo"][i], f["boll_mid"][i]
    return (f["low"][i] <= lo), (f["close"][i] >= mid)


def strat_rsi(i, f):
    return (f["rsi"][i] < 30), (f["rsi"][i] > 60)


def strat_mom(i, f):
    m = f["sma48"]
    return (f["close"][i] > m[i] and m[i] > m[i - 1]), (f["close"][i] < m[i])


STRATS = {
    "EMA20趋势": strat_ema20,
    "EMA8/21交叉": strat_ema_cross,
    "唐奇安突破20": strat_donchian,
    "布林带回撤买": strat_boll,
    "RSI14超卖": strat_rsi,
    "动量(2日线)": strat_mom,
}


def build_features(h, l, c):
    w = max(60, 21, 20, 14, 48)
    return {
        "high": h, "low": l, "close": c,
        "ema20": ema(c, 20), "ema8": ema(c, 8), "ema21": ema(c, 21),
        "highest20": pd.Series(c).shift(1).rolling(20).max().values,
        "lowest20": pd.Series(c).shift(1).rolling(20).min().values,
        "sma48": sma(c, 48), "rsi": rsi(c, 14),
        "boll_mid": sma(c, 20),
        "boll_lo": sma(c, 20) - 2 * pd.Series(c).rolling(20).std().values,
    }


def main():
    data = {}
    for sym in SYMBOLS:
        data[sym] = build_features(*load(sym))
        print(f"loaded {sym}: {len(data[sym]['close'])} bars")

    results = {}
    for sname, fn in STRATS.items():
        rows = []
        for sym in SYMBOLS:
            r = run_strategy(data[sym], fn)
            rows.append((sym, r["total"], r["win_rate"], r["pf"], r["trades"], r["mdd"]))
        results[sname] = rows

    print("\n=== 每策略每币: 总收益% / 胜率% / 盈亏比 / 交易数 / 最大回撤% ===")
    for sname, rows in results.items():
        print(f"\n--- {sname} ---")
        for sym, tot, wr, pf, tr, mdd in rows:
            pf_s = f"{pf:.2f}" if pf is not None else "-"
            print(f"  {sym:9s} {tot:+7.1f}%   胜率{wr:5.1f}%   PF {pf_s:>5s}   "
                  f"{tr:3d}笔   回撤{mdd:5.1f}%")

    print("\n=== 跨币平均(方便对比) ===")
    for sname, rows in sorted(results.items(),
                              key=lambda kv: -np.mean([r[1] for r in kv[1]])):
        avg_tot = np.mean([r[1] for r in rows])
        avg_wr = np.mean([r[2] for r in rows])
        avg_pf = np.mean([r[3] for r in rows if r[3] is not None]) or 0
        avg_mdd = np.mean([r[5] for r in rows])
        print(f"{sname:12s} 平均收益 {avg_tot:+6.1f}%  胜率{avg_wr:5.1f}%  "
              f"PF {avg_pf:5.2f}  回撤{avg_mdd:4.1f}%")


if __name__ == "__main__":
    main()