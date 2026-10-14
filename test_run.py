import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import SYMBOL, INTERVAL, KLINE_LIMIT, TRAIN_RATIO, XGBOOST_PARAMS, BASE_URL, PROXIES, REQUEST_RETRIES
from fetch_data import fetch_klines
from features import add_features, add_target
from model import train_model, predict_next

df = fetch_klines(SYMBOL, INTERVAL, KLINE_LIMIT,
                  base_url=BASE_URL, proxies=PROXIES, retries=REQUEST_RETRIES)
print(f"Fetched {len(df)} candles, latest close: {df['close'].iloc[-1]:.2f} at {df['open_time'].iloc[-1]}")

df = add_features(df)
df = add_target(df)
print(f"Features added: {len(df.columns)} columns, {df[['target']].notna().sum()['target']} labeled rows")

model, acc, report, imp = train_model(df, XGBOOST_PARAMS, TRAIN_RATIO)
print(f"Test accuracy: {acc:.4f}")
print(report)
print("Top 8 features:")
print(imp.head(8).to_string())

result = predict_next(model, df)
print("\nNext candle prediction:")
for k, v in result.items():
    print(f"  {k}: {v}")