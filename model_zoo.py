import json
import os
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier, VotingClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from xgboost import XGBClassifier

from config import SYMBOL, INTERVAL, TRAIN_RATIO, RANDOM_STATE, XGBOOST_PARAMS
from fetch_data import fetch_klines_many
from features import add_features, add_target, FEATURE_COLS

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_zoo.json")
TOTAL_BARS = 15000


def get_data():
    df = fetch_klines_many(SYMBOL, INTERVAL, TOTAL_BARS)
    df = add_target(add_features(df))
    df = df.dropna(subset=FEATURE_COLS + ["target"])
    return df


def evaluate(model, Xt, yt, name):
    proba = model.predict_proba(Xt)[:, 1]
    pred = (proba >= 0.5).astype(int)
    acc = float((pred == yt).mean())
    brier = float(((proba - yt) ** 2).mean())
    n = len(yt)
    wins = int((pred == yt).sum())
    roi19 = (wins * 1.9 - n) / n
    sub = {}
    for t in (0.60, 0.70, 0.80):
        mask = proba >= t
        n_t = int(mask.sum())
        acc_t = float((pred[mask] == yt[mask]).mean()) if n_t else None
        sub[str(int(t * 100))] = {"n": n_t, "acc": acc_t}
    return {"name": name, "n_test": n, "acc": acc, "brier": brier,
            "roi19": roi19, "conf": sub}


def main():
    df = get_data()
    X = df[FEATURE_COLS].values
    y = df["target"].values
    split = int(len(X) * TRAIN_RATIO)
    Xtr, Xte = X[:split], X[split:]
    ytr, yte = y[:split], y[split:]
    base_rate = float(yte.mean())

    models = [
        ("XGB(现用)", XGBClassifier(**XGBOOST_PARAMS)),
        ("逻辑回归", make_pipeline(StandardScaler(),
             LogisticRegression(max_iter=2000, random_state=RANDOM_STATE))),
        ("HistGradBoost", HistGradientBoostingClassifier(
             max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
             random_state=RANDOM_STATE)),
        ("随机森林", RandomForestClassifier(
             n_estimators=300, max_depth=8, min_samples_leaf=30,
             n_jobs=-1, random_state=RANDOM_STATE)),
        ("校准XGB(isotonic)", CalibratedClassifierCV(
             XGBClassifier(**XGBOOST_PARAMS), method="isotonic", cv=3)),
        ("集成(XGB+HGB+RF)", VotingClassifier(
             estimators=[
                 ("xgb", XGBClassifier(**XGBOOST_PARAMS)),
                 ("hgb", HistGradientBoostingClassifier(
                      max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
                      random_state=RANDOM_STATE)),
                 ("rf", RandomForestClassifier(
                      n_estimators=300, max_depth=8, min_samples_leaf=30,
                      n_jobs=-1, random_state=RANDOM_STATE)),
             ], voting="soft", n_jobs=-1)),
    ]

    results = []
    for name, m in models:
        m.fit(Xtr, ytr)
        results.append(evaluate(m, Xte, yte, name))

    rows = ["始终看涨(baseline)", base_rate]
    header = f"{'模型':<18}{'样本外命中':>10}{'Brier':>8}{'全押ROI1.9':>12}{'@60%n/acc':>16}{'@70%n/acc':>16}{'@80%n/acc':>16}"
    print("=" * 120)
    print(header)
    print("-" * 120)
    for r in results:
        c60 = r["conf"]["60"]
        c70 = r["conf"]["70"]
        c80 = r["conf"]["80"]
        line = (f"{r['name']:<18}{r['acc']*100:>9.2f}%{r['brier']:>8.4f}"
                f"{r['roi19']*100:>11.2f}%"
                f"{cn(c60):>16}{cn(c70):>16}{cn(c80):>16}")
        print(line)
    print("=" * 120)
    print(f"测试样本 {results[0]['n_test']} 根(最末20%时间)   'n/acc'=满足该置信区间的样本数/其命中率")
    print(f"始终看涨命中率 = {base_rate:.4f}  (这就是抛硬币的参照线)")

    best = max(results, key=lambda r: r["acc"])
    best_conf = max(results, key=lambda r: (r["conf"]["80"]["acc"] or 0) if r["conf"]["80"]["n"] >= 30 else 0)
    summary = {
        "total_bars": len(df),
        "n_train": split,
        "n_test": len(yte),
        "up_rate": base_rate,
        "results": results,
        "best_overall": best["name"],
        "best_conf80": best_conf["name"],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n总体最佳: {best['name']}   |   80%过滤下最佳: {best_conf['name']}")
    print(f"结果已存: {OUT}")


def cn(c):
    if not c["n"]:
        return f"{c['n']:>4}     -"
    return f"{c['n']:>4}/{c['acc']*100:>4.1f}%"


if __name__ == "__main__":
    main()