import json
import os
import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from config import SYMBOL, INTERVAL, XGBOOST_PARAMS, RANDOM_STATE
from fetch_data import fetch_klines_many
from features import add_features, add_target, FEATURE_COLS

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signal_lever_test.json")
TOTAL_BARS = 15000
WARM_UP = 3000
REFIT = 300
MARGIN = 10.0
LEVERAGE = 2.0
NOTIONAL = MARGIN * LEVERAGE
TAKER = 0.0005
ENTER_PROB = 0.80
EXIT_PROB = 0.50
STOP_SL = 0.02


def walk_forward_probs(df):
    n = len(df)
    probs = np.full(n, np.nan)
    full = df[FEATURE_COLS + ["target"]].dropna()
    idx = full.index
    model = None
    refit_at = WARM_UP
    for i in range(WARM_UP, n):
        if i >= refit_at:
            tr = full[full.index < i]
            Xtr = tr[FEATURE_COLS].values
            ytr = tr["target"].values.astype(int)
            model = XGBClassifier(**XGBOOST_PARAMS)
            model.fit(Xtr, ytr)
            refit_at = i + REFIT
        if model is not None and i in idx:
            x = df.loc[i, FEATURE_COLS].values.reshape(1, -1)
            probs[i] = model.predict_proba(x)[0][1]
    return probs, df["open"].values, df["close"].values


def simulate(probs, opens, closes, stop=None):
    n = len(probs)
    trades = []
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    pos = None
    hist = []
    i = WARM_UP
    while i < n - 1:
        p = probs[i]
        if pos is None:
            if not np.isnan(p):
                if p >= ENTER_PROB:
                    pos = {"side": 1, "entry_price": opens[i + 1], "entry_i": i + 1,
                           "fee": NOTIONAL * TAKER}
                elif (1 - p) >= ENTER_PROB:
                    pos = {"side": -1, "entry_price": opens[i + 1], "entry_i": i + 1,
                           "fee": NOTIONAL * TAKER}
            i += 1
            continue
        sig = p if pos["side"] == 1 else (1 - p)
        exit_i = None
        exit_price = None
        if stop is not None:
            for j in range(i + 1, n):
                ep = pos["entry_price"]
                lo = min(closes[j], opens[j])
                hi = max(closes[j], opens[j])
                if pos["side"] == 1 and lo <= ep * (1 - stop):
                    exit_i, exit_price = j, ep * (1 - stop)
                    break
                if pos["side"] == -1 and hi >= ep * (1 + stop):
                    exit_i, exit_price = j, ep * (1 + stop)
                    break
        if exit_i is None and not np.isnan(sig) and sig < EXIT_PROB:
            exit_i, exit_price = i + 1, opens[i + 1]
        if exit_i is None and i == n - 2:
            exit_i, exit_price = n - 1, closes[n - 1]
        if exit_i is not None:
            ret = pos["side"] * (exit_price / pos["entry_price"] - 1) * NOTIONAL
            pnl = ret - pos["fee"] - NOTIONAL * TAKER
            equity += pnl
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
            trades.append({
                "entry_i": pos["entry_i"], "exit_i": exit_i,
                "side": "LONG" if pos["side"] == 1 else "SHORT",
                "entry_price": round(pos["entry_price"], 2),
                "exit_price": round(exit_price, 2),
                "ret_pct": round((exit_price / pos["entry_price"] - 1) * 100 * pos["side"], 3),
                "pnl": round(pnl, 2),
                "bars": exit_i - pos["entry_i"],
            })
            pos = None
            i = exit_i + 1
            continue
        if i % 50 == 0:
            hist.append((i, equity))
        i += 1
    wins = sum(1 for t in trades if t["pnl"] > 0)
    return {
        "trades": trades,
        "n": len(trades),
        "wins": wins,
        "win_rate": wins / len(trades) if trades else None,
        "total_pnl": round(equity, 2),
        "fees": round(2 * NOTIONAL * TAKER * len(trades), 2),
        "max_dd": round(max_dd, 2),
        "avg_bars": round(np.mean([t["bars"] for t in trades]), 1) if trades else None,
        "hist": hist,
    }


def report(name, r):
    print(f"\n===== {name} =====")
    if not r["trades"]:
        print("0 笔交易: 该231规则在样本期内没有触发 (概率从未到80%)")
        return
    print(f"开仓 {r['n']} 次  盈利 {r['wins']} 次  命中率 {r['win_rate']*100:.1f}%")
    print(f"平均持仓 {r['avg_bars']} 根5分钟K线")
    print(f"总盈亏 {r['total_pnl']:+.2f}u   (投入本金10u → {r['total_pnl']/MARGIN*100:+.1f}%)")
    print(f"最大回撤 {r['max_dd']:.2f}u   手续费合计 {r['fees']}u")
    for t in r["trades"]:
        print(f"  #{t['entry_i']} {t['side']} @ {t['entry_price']} -> {t['exit_price']} "
              f"{t['ret_pct']:+.2f}%  {t['pnl']:+.2f}u  持仓{t['bars']}根")
    day_len = len(r["hist"])
    span_days = (len(probs) - WARM_UP) * 5 / 60 / 24 if False else None


def main():
    global probs
    df_raw = fetch_klines_many(SYMBOL, INTERVAL, TOTAL_BARS)
    df = add_target(add_features(df_raw))
    probs, opens, closes = walk_forward_probs(df)

    r1 = simulate(probs, opens, closes, stop=None)
    r2 = simulate(probs, opens, closes, stop=STOP_SL)

    span_days = (len(probs) - WARM_UP) * 5 / 60 / 24
    for name, r in (("规则原始: 80%进 / <50%出, 2倍杠杆", r1),
                    (f"加2%硬止损 (对照)", r2)):
        report(name, r)
        if r["trades"]:
            ann = (r["total_pnl"] / MARGIN) / span_days * 365
            print(f"  ≈ 年化(按52天趋势外推, 极不可靠) {ann*100:+.0f}%")

    summary = {"span_days": span_days, "rule": "prob>=80 enter, prob<50 exit, 10u*2x",
               "no_stop": r1, "with_2pct_stop": r2}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n已存: {OUT}")


if __name__ == "__main__":
    main()