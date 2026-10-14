import json
import os
import time
import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from config import SYMBOL, INTERVAL, TRAIN_RATIO, RANDOM_STATE, XGBOOST_PARAMS
from fetch_data import fetch_klines_many
from features import add_features, add_target, FEATURE_COLS
from simfunding import _fapi_get

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_zoo_fund.json")
TOTAL_BARS = 15000


def _fetch_funding(sym):
    data = _fapi_get("/fapi/v1/fundingRate", {"symbol": sym, "limit": 1000})
    f = pd.DataFrame({"fund_time": [int(x["fundingTime"]) for x in data],
                      "rate": [float(x["fundingRate"]) for x in data]})
    f["fund_time"] = pd.to_datetime(f["fund_time"], unit="ms")
    f = f.sort_values("fund_time").reset_index(drop=True)
    f["rate_lag24"] = f["rate"].shift(3)
    f["chg24"] = f["rate"] - f["rate_lag24"]
    f["accum24"] = f["rate"].rolling(3).sum()
    return f.dropna().reset_index(drop=True)


def _fetch_oi(sym, start_ms, end_ms, period="1h"):
    page_ms = {"5m": 300000, "1h": 3600000}[period]
    pages = []
    cur = (start_ms // page_ms) * page_ms
    while cur < end_ms:
        data = _fapi_get("/futures/data/openInterestHist",
                         {"symbol": sym, "period": period, "limit": 500,
                          "startTime": int(cur)})
        if not data:
            break
        o = pd.DataFrame({"oi_time": [int(x["timestamp"]) for x in data],
                          "oi": [float(x["sumOpenInterest"]) for x in data]})
        pages.append(o)
        last = o["oi_time"].max()
        if cur >= last or o["oi_time"].max() - o["oi_time"].min() <= 0:
            break
        cur = last + 1
        time.sleep(0.25)
    oi = pd.concat(pages, ignore_index=True)
    oi["oi_time"] = pd.to_datetime(oi["oi_time"], unit="ms")
    oi = oi.sort_values("oi_time").reset_index(drop=True)
    oi = oi.drop_duplicates("oi_time")
    return oi


def build_fund_features(df, via='ticker'):
    df = df.copy()
    df["bar_ts"] = df["open_time"]
    f = _fetch_funding(SYMBOL)
    merged = pd.merge_asof(df.sort_values("bar_ts"), f.sort_values("fund_time"),
                           left_on="bar_ts", right_on="fund_time", direction="backward")
    merged["fund_rate"] = merged["rate"]
    merged["fund_chg24"] = merged["chg24"]
    merged["fund_accum24"] = merged["accum24"]
    return df, merged


def build_oi_features(df):
    df = df.copy()
    end_ms = int(df["open_time"].iloc[-1].value // 10**6)
    start_ms = max(int((df["open_time"].iloc[0] - pd.Timedelta(hours=48)).value // 10**6),
                   int(end_ms - 29 * 86400000))
    oi = _fetch_oi(SYMBOL, start_ms, end_ms)
    merged = pd.merge_asof(df.sort_values("open_time"), oi.sort_values("oi_time"),
                           left_on="open_time", right_on="oi_time", direction="backward")
    merged = merged.sort_values("open_time").reset_index(drop=True)
    merged["oi_use"] = merged["oi"].shift(12)
    merged["oi_ratio_24h"] = merged["oi_use"] / merged["oi_use"].shift(24)
    merged["oi_ma3d"] = merged["oi_use"] / merged["oi_use"].rolling(72).mean()
    return merged


def evaluate(model, Xt, yt):
    proba = model.predict_proba(Xt)[:, 1]
    pred = (proba >= 0.5).astype(int)
    acc = float((pred == yt).mean())
    brier = float(((proba - yt) ** 2).mean())
    n = len(yt)
    wins = int((pred == yt).sum())
    roi19 = (wins * 1.9 - n) / n
    sub = {}
    for t in (0.60, 0.70, 0.80):
        m = proba >= t
        nt = int(m.sum())
        sub[str(int(t * 100))] = {"n": nt, "acc": float((pred[m] == yt[m]).mean()) if nt else None}
    return {"n_test": n, "acc": acc, "brier": brier, "roi19": roi19, "conf": sub}


def main():
    raw = fetch_klines_many(SYMBOL, INTERVAL, TOTAL_BARS)
    df = add_target(add_features(raw))
    df = df.dropna(subset=FEATURE_COLS + ["target"]).reset_index(drop=True)

    _, merged = build_fund_features(df)
    merged = build_oi_features(merged)
    merged = merged.dropna(subset=FEATURE_COLS + ["target"]).reset_index(drop=True)

    fund_cols = ["fund_rate", "fund_chg24", "fund_accum24"]
    oi_cols = ["oi_use", "oi_ratio_24h", "oi_ma3d"]

    merged = merged.dropna(subset=FEATURE_COLS + ["target"] + oi_cols).reset_index(drop=True)

    split = int(len(merged) * TRAIN_RATIO)
    y = merged["target"].values
    results = []

    variants = [
        ("K线特征(XGB现用)", FEATURE_COLS),
        ("+资金费率特征", FEATURE_COLS + fund_cols),
        ("+资金费率+持仓量OI", FEATURE_COLS + fund_cols + oi_cols),
    ]
    for name, cols in variants:
        X = merged[cols].values
        Xtr, Xte = X[:split], X[split:]
        ytr, yte = y[:split], y[split:]
        m = XGBClassifier(**XGBOOST_PARAMS)
        m.fit(Xtr, ytr)
        ev = evaluate(m, Xte, yte)
        ev["name"] = name
        ev["n_signal_up"] = int((y == 1).sum())
        results.append(ev)

    base_rate = float(merged["target"].iloc[split:].mean())
    print("=" * 100)
    hdr = f"{'特征集':<28}{'命中率':>9}{'Brier':>8}{'全押ROI':>10}{'@60%':>16}{'@70%':>16}{'@80%':>16}"
    print(hdr)
    print("-" * 100)
    for r in results:
        c60, c70, c80 = r["conf"]["60"], r["conf"]["70"], r["conf"]["80"]
        print(f"{r['name']:<28}{r['acc']*100:>8.2f}%{r['brier']:>8.4f}{r['roi19']*100:>9.2f}%"
              f"{cn(c60):>16}{cn(c70):>16}{cn(c80):>16}")
    print("=" * 100)
    print(f"有效样本 {len(merged)} 根(OI窗口约29天), 测试 {results[0]['n_test']} 根(最末20%), 始终看涨命中率={base_rate:.4f}")

    funding_stats = {"mean_rate_8h": float(merged["fund_rate"].mean()),
                     "mean_accum24": float(merged["fund_accum24"].mean())}
    summary = {"total_bars": len(df), "results": results, "up_rate": base_rate,
               "funding": funding_stats}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    best = max(results, key=lambda r: r["acc"])
    print(f"最佳: {best['name']} ({best['acc']*100:.2f}%)")
    print(f"已存: {OUT}")


def cn(c):
    if not c["n"]:
        return f"{c['n']:>4}     -"
    return f"{c['n']:>4}/{c['acc']*100:>4.1f}%"


if __name__ == "__main__":
    main()