import time
import sys
from datetime import datetime

from config import (
    SYMBOL, INTERVAL, KLINE_LIMIT,
    RETRAIN_EVERY, PREDICT_EVERY,
    TRAIN_RATIO, XGBOOST_PARAMS,
    BASE_URL, PROXIES, REQUEST_RETRIES,
)
from fetch_data import fetch_klines
from features import add_features, add_target
from model import train_model, predict_next


def print_header():
    print("=" * 60)
    print("  BTC 5-Min Prediction Bot  (XGBoost)")
    print(f"  Symbol: {SYMBOL}  |  Interval: {INTERVAL}")
    print("=" * 60)


def run():
    print_header()
    model = None
    train_counter = 0

    while True:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{now}] Fetching {SYMBOL} {INTERVAL} klines ...")
            df = fetch_klines(SYMBOL, INTERVAL, KLINE_LIMIT,
                              base_url=BASE_URL, proxies=PROXIES,
                              retries=REQUEST_RETRIES)
            print(f"  Received {len(df)} candles, latest: {df['open_time'].iloc[-1]}")

            df = add_features(df)
            df = add_target(df)

            should_train = (model is None) or (train_counter >= RETRAIN_EVERY)
            if should_train:
                print("\n  >> Training XGBoost model ...")
                model, acc, report, importances = train_model(df, XGBOOST_PARAMS, TRAIN_RATIO)
                print(f"  >> Test accuracy: {acc:.4f}")
                print(f"  >> Top features:")
                for feat, imp in importances.head(8).items():
                    print(f"       {feat:>20s} : {imp:.4f}")
                train_counter = 0
            else:
                print(f"  Using existing model (retrain in {RETRAIN_EVERY - train_counter} cycles)")

            result = predict_next(model, df)
            print(f"\n  ========================================")
            print(f"  Prediction for next {INTERVAL} candle:")
            print(f"    Direction : {result['direction']}")
            print(f"    Prob UP   : {result['prob_up']:.2%}")
            print(f"    Prob DOWN : {result['prob_down']:.2%}")
            print(f"    Last close: ${result['last_close']:,.2f}")
            print(f"    Candle time: {result['last_time']}")
            print(f"  ========================================")

            train_counter += 1
            print(f"\n  Sleeping {PREDICT_EVERY}s ...")
            time.sleep(PREDICT_EVERY)

        except KeyboardInterrupt:
            print("\nBot stopped by user.")
            sys.exit(0)
        except Exception as e:
            print(f"\n  [ERROR] {e}")
            print("  Retrying in 10s ...")
            time.sleep(10)


if __name__ == "__main__":
    run()
