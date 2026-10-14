import numpy as np
import pandas as pd
import ta


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["price_change"] = df["close"].pct_change()
    df["high_low_range"] = (df["high"] - df["low"]) / df["close"]
    df["close_open_range"] = (df["close"] - df["open"]) / df["open"]
    df["upper_shadow"] = (df["high"] - df[["open", "close"]].max(axis=1)) / df["close"]
    df["lower_shadow"] = (df[["open", "close"]].min(axis=1) - df["low"]) / df["close"]

    df["ema_7"] = ta.trend.ema_indicator(df["close"], window=7)
    df["ema_21"] = ta.trend.ema_indicator(df["close"], window=21)
    df["sma_20"] = ta.trend.sma_indicator(df["close"], window=20)
    df["ema_diff_7_21"] = (df["ema_7"] - df["ema_21"]) / df["close"]

    macd = ta.trend.MACD(df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    df["rsi_14"] = ta.momentum.rsi(df["close"], window=14)
    df["rsi_7"] = ta.momentum.rsi(df["close"], window=7)

    stoch = ta.momentum.StochasticOscillator(df["high"], df["low"], df["close"])
    df["stoch_k"] = stoch.stoch()
    df["stoch_d"] = stoch.stoch_signal()

    bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["close"]
    df["bb_pband"] = bb.bollinger_pband()

    df["atr_14"] = ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=14)
    df["atr_pct"] = df["atr_14"] / df["close"]

    df["volume_change"] = df["volume"].pct_change()
    df["volume_sma_20"] = df["volume"].rolling(window=20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_sma_20"]

    df["obv"] = ta.volume.on_balance_volume(df["close"], df["volume"])

    df["return_1"] = df["close"].pct_change(1)
    df["return_3"] = df["close"].pct_change(3)
    df["return_5"] = df["close"].pct_change(5)
    df["return_10"] = df["close"].pct_change(10)

    df["volatility_5"] = df["return_1"].rolling(window=5).std()
    df["volatility_10"] = df["return_1"].rolling(window=10).std()
    df["volatility_20"] = df["return_1"].rolling(window=20).std()

    df["momentum_5"] = df["close"] - df["close"].shift(5)
    df["momentum_10"] = df["close"] - df["close"].shift(10)

    return df


FEATURE_COLS = [
    "price_change", "high_low_range", "close_open_range",
    "upper_shadow", "lower_shadow",
    "ema_diff_7_21",
    "macd", "macd_signal", "macd_hist",
    "rsi_14", "rsi_7",
    "stoch_k", "stoch_d",
    "bb_width", "bb_pband",
    "atr_pct",
    "volume_change", "volume_ratio",
    "return_1", "return_3", "return_5", "return_10",
    "volatility_5", "volatility_10", "volatility_20",
    "momentum_5", "momentum_10",
]


def add_target(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["target"] = (df["close"].shift(-1) > df["close"]).astype(int)
    return df
