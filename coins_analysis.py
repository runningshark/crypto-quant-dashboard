import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import json
import numpy as np

from config import PROXIES
from fetch_data import fetch_klines_any, _parse_kline_data

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
           "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
           "LTCUSDT", "UNIUSDT", "TRXUSDT", "NEARUSDT", "ARBUSDT",
           "OPUSDT", "SUIUSDT", "TONUSDT", "ATOMUSDT", "FILUSDT"]
INTERVAL = "1h"
LIMIT = 1500


def ema(vals, n):
    out = np.empty(len(vals))
    out[0] = vals[0]
    k = 2.0 / (n + 1)
    for i in range(1, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1 - k)
    return out


def ema_trend_backtest(high, low, close):
    """多头EMA20趋势: 收盘>EMA20开仓, 收盘<EMA20或盘中-2%止损平仓。"""
    ema20 = ema(close, 20)
    pos = 0.0
    entry = 0.0
    peak = 0.0
    stop = 0.02
    wins = losses = 0
    total_pct = 0.0
    for i in range(1, len(close)):
        if pos == 0 and close[i] > ema20[i]:
            pos = 1.0
            entry = close[i]
            peak = high[i]
            continue
        if pos:
            peak = max(peak, high[i])
            loose = low[i] <= entry * (1 - stop)
            exit_price = entry * (1 - stop) if loose else (low[i] if close[i] < ema20[i] else None)
            if exit_price is not None:
                pnl = exit_price / entry - 1
                total_pct += pnl
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
                pos = 0
    if pos:
        pnl = close[-1] / entry - 1
        total_pct += pnl
        if pnl > 0:
            wins += 1
        else:
            losses += 1
    return {"trades": wins + losses, "win": wins, "loss": losses,
            "total_pct": total_pct * 100}


def main():
    results = []
    for sym in SYMBOLS:
        try:
            df = _parse_kline_data(fetch_klines_any(sym, INTERVAL, LIMIT, PROXIES))
        except Exception as e:
            print(f"SKIP {sym}: {e}")
            continue
        c = df["close"].astype(float).values
        h = df["high"].astype(float).values
        l = df["low"].astype(float).values
        v = df["volume"].astype(float).values

        ret = np.diff(c) / c[:-1]
        vol = float(np.std(ret) * np.sqrt(365 * 24) * 100)
        ret30d = c[-1] / c[0] - 1

        liq24 = float((v[-24:] * c[-24:]).sum())
        er = float(abs(c[-1] - c[0]) / (np.sum(np.abs(np.diff(c))) + 1e-12))
        ac1 = float(np.corrcoef(ret[:-1], ret[1:])[0, 1]) if len(ret) > 2 else 0.0
        bt = ema_trend_backtest(h, l, c)
        winr = bt["win"] / bt["trades"] * 100 if bt["trades"] else 0.0

        results.append({
            "symbol": sym, "price": float(c[-1]), "vol_pct": vol,
            "ret30d": ret30d * 100, "liq24h_usd": liq24,
            "er": er, "ac1": ac1, "trades": bt["trades"],
            "win_rate": winr, "total_pct": bt["total_pct"],
        })
        print(f"{sym:9s} 现价{float(c[-1]):>10.4f}  年化波动{vol:6.1f}%  "
              f"30d收益{ret30d*100:+7.1f}%  24h成交额${liq24/1e6:6.1f}M  趋势性ER{er:.2f}  "
              f"回归性AC1{ac1:+.3f}")

    print("\n[EMA20趋势策略1h回测] 胜率 / 交易数 / 累计收益%")
    for r in sorted(results, key=lambda x: x["total_pct"], reverse=True):
        print(f"{r['symbol']:9s}  胜率{r['win_rate']:5.1f}% / {r['trades']:3d}笔  "
              f"累计{r['total_pct']:+7.1f}%")

    json.dump(results, open("coins_analysis.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\nsaved coins_analysis.json")


if __name__ == "__main__":
    main()