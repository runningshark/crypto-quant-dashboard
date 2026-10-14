import json
import os
import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from config import SYMBOL, INTERVAL, XGBOOST_PARAMS
from fetch_data import fetch_klines_many
from features import add_features, add_target, FEATURE_COLS

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calib_test.json")
TOTAL_BARS = 15000
WARM_UP = 3000
REFIT = 300
K = 200
PAYOUT = 1.9
BE = 1 / PAYOUT


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
            model = XGBClassifier(**XGBOOST_PARAMS)
            model.fit(tr[FEATURE_COLS].values, tr["target"].values.astype(int))
            refit_at = i + REFIT
        if model is not None and i in idx:
            x = df.loc[i, FEATURE_COLS].values.reshape(1, -1)
            probs[i] = model.predict_proba(x)[0][1]
    return probs, df["target"].values.astype(int)


def decision_result(pred, act):
    n = len(pred)
    w = int((pred == act).sum())
    return {"n": n, "win": w,
            "hit": round(w / n, 4) if n else None,
            "roi": round((w * PAYOUT - n) / n, 4) if n else None}


def main():
    df = add_target(add_features(fetch_klines_many(SYMBOL, INTERVAL, TOTAL_BARS)))
    probs, act = walk_forward_probs(df)

    n = len(probs)
    idx = np.arange(n)
    cand = np.arange(WARM_UP, n)
    p = pd.Series(probs, index=idx)
    a = pd.Series(act, index=idx)

    market_up = a.rolling(K).mean().shift(1)          # 过去K根实际涨率
    model_up_call = (p >= 0.5).astype(int).rolling(K).mean().shift(1)  # 过去K根模型判涨率
    bias = (model_up_call - market_up).clip(-0.45, 0.45)
    tau = (0.5 - bias).clip(0.05, 0.95)               # 校准后的判涨阈值(仅用过去)

    A = (p >= 0.5).astype(int)                        # 跟模型
    B = (market_up >= 0.5).astype(int)                # 只看滚动基准率(多数类)
    C = (p >= tau).astype(int)                        # 校准阈值版
    D = (1 - A)                                       # 反做模型

    rules = {"A_follow": A, "B_base_rate": B, "C_calibrated": C, "D_fade": D}
    results = {}
    print("=" * 88)
    print("walk-forward OOS bars:", len(cand),
          " window: {} ~ No.{}".format(cand[0], cand[-1]))
    print("beakeven hit @1.9 payout:", "{:.2%}".format(BE))
    print("-" * 88)
    print("{:<16}{:>7}{:>8}{:>9}{:>9}  |  half1/half2 hit".format("rule", "n", "win", "hit", "roi"))
    for name, r in rules.items():
        pred = r.loc[cand].values
        actc = a.loc[cand].values
        m = decision_result(pred, actc)
        h = int(len(cand) // 2)
        h1 = decision_result(pred[:h], actc[:h])
        h2 = decision_result(pred[h:], actc[h:])
        results[name] = m
        print("{:<16}{:>7}{:>8}{:>9.1%}{:>+9.1%}  |  {:.1%} / {:.1%}".format(
            name, m["n"], m["win"], m["hit"] or float("nan"),
            m["roi"] or float("nan"),
            h1["hit"] or float("nan"), h2["hit"] or float("nan")))
    actual_up = float(a.loc[cand].mean())
    print("-" * 88)
    print("market UP rate (OOS):", "{:.2%}".format(actual_up))
    print("model UP-call rate (OOS):", "{:.2%}".format(float((A.loc[cand] == 1).mean())))
    print("calibrated tau: mean", "{:.3f}".format(float(tau.loc[cand].mean())),
          "last", "{:.3f}".format(float(tau.iloc[-1])))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"window": {"start_i": int(cand[0]), "end_i": int(cand[-1]),
                              "bars": int(len(cand))},
                   "market_up_rate": round(actual_up, 4),
                   "payout": PAYOUT, "rules": results}, f, ensure_ascii=False, indent=2)
    print("saved:", OUT)


if __name__ == "__main__":
    main()