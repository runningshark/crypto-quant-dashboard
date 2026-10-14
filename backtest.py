import sys
import os
import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from config import (
    SYMBOL, INTERVAL, HISTORY_CANDLES,
    BACKTEST_TRAIN_WINDOW, BACKTEST_RETRAIN,
    BACKTEST_STAKE, BACKTEST_PAYOUT, BACKTEST_CONF,
    RANDOM_STATE, XGBOOST_PARAMS,
    BASE_URL, PROXIES, REQUEST_RETRIES,
)
from fetch_data import fetch_klines_many
from features import add_features, add_target, FEATURE_COLS


def run_backtest(df_raw: pd.DataFrame,
                 train_window: int = BACKTEST_TRAIN_WINDOW,
                 retrain_every: int = BACKTEST_RETRAIN,
                 stake: float = BACKTEST_STAKE,
                 payout: float = BACKTEST_PAYOUT,
                 conf: float = BACKTEST_CONF) -> dict:

    df = add_features(df_raw)
    df = add_target(df)

    base_params = dict(XGBOOST_PARAMS)
    base_params["objective"] = "binary:logistic"
    base_params["eval_metric"] = "logloss"

    n = len(df)
    if n <= train_window + 50:
        raise ValueError(f"Data too small ({n} candles) for train_window={train_window}")
    results = []

    model = None
    for i in range(train_window, n - 1):
        if i % retrain_every == 0 or model is None:
            train_slice = df.iloc[i - train_window:i]
            train_slice = train_slice.dropna(subset=FEATURE_COLS + ["target"])
            X_train = train_slice[FEATURE_COLS].values
            y_train = train_slice["target"].values
            model = XGBClassifier(**base_params)
            model.fit(X_train, y_train)

        row = df.iloc[i]
        X_cur = row[FEATURE_COLS].values.reshape(1, -1)
        proba = model.predict_proba(X_cur)[0]
        prob_up = float(proba[1])

        actual_up = int(df["target"].iloc[i])  # 下一根是否上涨

        pred_up = prob_up >= 0.5
        bet = prob_up >= conf or prob_up <= 1 - conf
        bet_up = prob_up >= conf
        predicted_up = (bet_up if bet else None)

        win = None
        pnl = 0.0
        if bet:
            win = (predicted_up == actual_up)
            pnl = stake * (payout - 1) if win else -stake

        results.append({
            "time": row["close_time"],
            "close": row["close"],
            "prob_up": prob_up,
            "actual_up": actual_up,
            "bet": bet,
            "pred_up": predicted_up,
            "win": win,
            "pnl": pnl,
        })

    r = pd.DataFrame(results)

    # 全部预测的准确率
    acc_all = (r["prob_up"] >= 0.5).eq(r["actual_up"]).mean()

    b = r[r["bet"]].copy()
    b["win"] = b["win"].astype("boolean")
    b["win"] = b["win"].fillna(False)
    win_rate = b["win"].mean() if len(b) else 0.0
    net_pnl = b["pnl"].sum() if len(b) else 0.0
    trades = len(b)

    equity = np.concatenate(([0.0], np.cumsum(b["pnl"].values))) if len(b) else np.array([0.0])
    peak = np.maximum.accumulate(equity)
    drawdown = (peak - equity).max()

    # 朴素基准：跟随上一根方向
    r["naive_up"] = r["close"].diff() > 0
    naive_acc = r["naive_up"].shift(1).eq(r["actual_up"]).mean()

    # 基准2：长期下注全UP
    always_up_acc = r["actual_up"].mean()

    won_pos = b.loc[b["win"], "pnl"]
    lost_pos = b.loc[~b["win"], "pnl"]

    return {
        "candles": n,
        "pred_period": f"{r['time'].iloc[0]} -> {r['time'].iloc[-1]}",
        "accuracy_all": float(acc_all),
        "naive_follow_accuracy": float(naive_acc),
        "always_up_accuracy": float(always_up_acc),
        "trades": trades,
        "win_rate": float(win_rate),
        "net_pnl": float(net_pnl),
        "roi": float(net_pnl / max(trades * stake, 1e-9)),
        "max_drawdown": float(drawdown),
        "profit_factor": float(won_pos.sum() / max(-lost_pos.sum(), 1e-9)) if len(b) else 0.0,
    }


def load_history(cache_file="btc_5m_history.csv"):
    if os.path.exists(cache_file):
        print(f"Loading cached history from {cache_file} ...")
        df = pd.read_csv(cache_file, parse_dates=["open_time", "close_time"])
        if len(df) >= HISTORY_CANDLES:
            return df
    print(f"Fetching {HISTORY_CANDLES} candles (~{HISTORY_CANDLES // 288:.0f} days) ...")
    df = fetch_klines_many(SYMBOL, INTERVAL, HISTORY_CANDLES,
                           base_url=BASE_URL, proxies=PROXIES,
                           retries=REQUEST_RETRIES)
    df.to_csv(cache_file, index=False)
    print(f"  Saved to {cache_file}")
    return df


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    df = load_history()
    print(f"  Received {len(df)} candles: {df['open_time'].iloc[0]} -> {df['close_time'].iloc[-1]}\n")

    print("Running walk-forward backtest (train on past, predict future, retrain periodically) ...")
    res = run_backtest(df)

    print("\n" + "=" * 56)
    print("  BACKTEST RESULT")
    print("=" * 56)
    print(f"  回测区间        : {res['pred_period']}")
    print(f"  下注金额        : ${BACKTEST_STAKE}  /  胜出返还 {BACKTEST_PAYOUT}x  /  置信阈值 {BACKTEST_CONF}")
    print(f"  ---------------------------------------------------------")
    print(f"  模型整体准确率  : {res['accuracy_all']:.4f}")
    print(f"  跟随上一根方向  : {res['naive_follow_accuracy']:.4f}  (朴素基准)")
    print(f"  永远买涨准确率  : {res['always_up_accuracy']:.4f}  (朴素基准)")
    print(f"  ---------------------------------------------------------")
    print(f"  触发下注次数    : {res['trades']}")
    print(f"  下注胜率        : {res['win_rate']:.4f}")
    print(f"  ---------------------------------------------")
    print(f"  净利润          : ${res['net_pnl']:+.2f}")
    print(f"  回报率(ROI)     : {res['roi']:.2%}")
    print(f"  最大回撤        : ${res['max_drawdown']:.2f}")
    print(f"  盈亏因子        : {res['profit_factor']:.3f}")
    print("=" * 56)

    need = 1 / BACKTEST_PAYOUT
    print(f"\n  盈亏平衡胜率 = 1/赔率 = {need:.2%}")
    print(f"  (赔率{BACKTEST_PAYOUT}x表示胜出连本带利拿回{BACKTEST_PAYOUT}倍，纯利润是{BACKTEST_PAYOUT-1:.1f}倍)")
    print(f"  若回测胜率 < {need:.2%} 且赔率 {BACKTEST_PAYOUT}x，长期必亏。")
    print(f"  结论: {'模型未达到盈亏平衡，不建议下注' if res['win_rate'] < need else '达到盈亏平衡，但需真实盘小仓验证'} ")


if __name__ == "__main__":
    main()