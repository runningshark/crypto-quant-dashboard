import os
import numpy as np
import pandas as pd
from xgboost import XGBClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
from features import FEATURE_COLS

MODEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_cache.ubj")


def _model_path(symbol: str) -> str:
    if symbol == "BTCUSDT":
        return MODEL_FILE
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), f"model_{symbol}.ubj")


def save_model(model: XGBClassifier, symbol: str = "BTCUSDT") -> None:
    model.save_model(_model_path(symbol))


def load_model(symbol: str = "BTCUSDT"):
    p = _model_path(symbol)
    if os.path.exists(p):
        m = XGBClassifier()
        m.load_model(p)
        return m
    return None


def train_model(df: pd.DataFrame, params: dict, train_ratio: float = 0.8):
    df_clean = df.dropna(subset=FEATURE_COLS + ["target"])
    X = df_clean[FEATURE_COLS].values
    y = df_clean["target"].values

    split = int(len(X) * train_ratio)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    model = XGBClassifier(**params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)

    report = classification_report(y_test, y_pred, target_names=["DOWN", "UP"])

    importances = pd.Series(
        model.feature_importances_, index=FEATURE_COLS
    ).sort_values(ascending=False)

    return model, acc, report, importances


def predict_next(model: XGBClassifier, df: pd.DataFrame) -> dict:
    last_row = df[FEATURE_COLS].iloc[[-1]].values
    proba = model.predict_proba(last_row)[0]
    pred = int(model.predict(last_row)[0])
    return {
        "direction": "UP" if pred == 1 else "DOWN",
        "prob_up": round(float(proba[1]), 4),
        "prob_down": round(float(proba[0]), 4),
        "last_close": round(float(df["close"].iloc[-1]), 2),
        "last_time": str(df["open_time"].iloc[-1]),
    }
