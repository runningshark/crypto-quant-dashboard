"""软性持有策略(Soft-Hold) - 4小时诚实回测。

规则: 每币子账本起始100, 主区(EMA576>676)成立时目标仓位=共振分/100,
      主区破坏则目标0。次日开盘调仓(敞口变化≥2%才动), 单边0.05%费。
对比: 买入持有 / 二值Vegas(现vegastrategy) / S1日线(EMA20/60)。
龙虾走fapi永续。禁止调用 simfunding.init()。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import fetch_klines_many, fetch_futures_klines_many
import vegas_strategy as vs

COINS = [("BTCUSDT", 0.45, "spot"), ("ETHUSDT", 0.25, "spot"),
         ("SOLUSDT", 0.15, "spot"), ("XRPUSDT", 0.10, "spot"),
         ("龙虾USDT", 0.05, "fut")]
WARM = 700
N4H = 2000
REBAL = 0.02
FEE = vs.FEE


def load4h(sym, kind):
    if kind == "fut":
        return fetch_futures_klines_many(sym, "4h", N4H)
    try:
        return fetch_klines_many(sym, "4h", N4H)
    except Exception:
        return fetch_futures_klines_many(sym, "4h", N4H)


def load_daily(sym, kind):
    if kind == "fut":
        return fetch_futures_klines_many(sym, "1d", 400)
    try:
        return fetch_klines_many(sym, "1d", 400)
    except Exception:
        return fetch_futures_klines_many(sym, "1d", 400)


def softhold(df):
    ind = vs.indicators(df)
    n = len(df)
    opens = df["open"].values.astype(float)
    closes = df["close"].values.astype(float)
    times = df["open_time"].values

    target = np.zeros(n)
    for i in range(n):
        e576 = ind["e576"].iloc[i]
        e676 = ind["e676"].iloc[i]
        if e576 > e676 and not pd.isna(ind["e12"].iloc[i]):
            _, sc = vs.score(ind, i)
            target[i] = sc / 100.0
        else:
            target[i] = 0.0

    C0 = 100.0
    cash = C0
    units = 0.0
    cur_f = 0.0
    fees = 0.0
    n_reb = 0
    eq = []
    for i in range(WARM, n - 1):
        f = target[i]
        if abs(f - cur_f) >= REBAL:
            px = opens[i + 1]
            val = cash + units * px
            want = f * val
            now = units * px
            d = want - now
            if d > 0:
                cash -= d
                units += (d * (1 - FEE)) / px
                fees += d * FEE
            elif d < 0:
                cash += (-d) * (1 - FEE)
                units += d / px
                fees += (-d) * FEE
            cur_f = f
            n_reb += 1
        eq.append(cash + units * closes[i + 1])

    eqv = np.array(eq)
    if units > 0:
        cash += units * closes[-1] * (1 - 2 * FEE)
        units = 0.0
    final = cash
    eqv[-1] = final
    peak = np.maximum.accumulate(eqv)
    mdd = float(((eqv - peak) / peak).min())
    ts_all = df["open_time"].iloc[WARM:n - 1]
    t0 = ts_all.iloc[0].to_pydatetime().strftime("%Y-%m-%d %H:%M")
    t1 = ts_all.iloc[-1].to_pydatetime().strftime("%Y-%m-%d %H:%M")
    return {
        "window": f"{t0} ~ {t1}",
        "bars": n - WARM - 1,
        "rebalances": n_reb,
        "fees": round(fees, 2),
        "total": round(final / C0 - 1, 4),
        "max_dd": round(mdd, 4),
        "avg_exposure": round(float(np.mean(target[WARM:n - 1])), 3),
        "buy_hold": round(closes[-1] / closes[WARM] - 1, 4),
        "eq": [round(float(x), 4) for x in eqv],
        "times": [t.to_pydatetime().strftime("%Y-%m-%d %H:%M") for t in ts_all],
    }


def vegas_binary(df):
    ind = vs.indicators(df)
    n = len(df)
    opens = df["open"].values.astype(float)
    closes = df["close"].values.astype(float)
    cash = 100.0
    pos = 0.0
    bought = False
    entry_idx = None
    wins = 0
    ntr = 0
    for i in range(WARM, n - 1):
        if not bought:
            s, _ = vs.long_signal(ind, i)
            if s:
                pos = cash / opens[i + 1]
                cash = 0.0
                bought = True
                entry_idx = i + 1
                ntr += 1
        else:
            s = vs.exit_signal(ind, i)
            if s:
                sell = opens[i + 1]
                cash = pos * sell * (1 - 2 * FEE)
                wins += 1 if sell > opens[entry_idx] else 0
                pos = 0.0
                bought = False
    if bought:
        cash = pos * closes[-1] * (1 - 2 * FEE)
    return {"trades": ntr, "wins": wins,
            "total": round(cash / 100.0 - 1, 4),
            "buy_hold": round(closes[-1] / closes[WARM] - 1, 4)}


def s1_daily(df):
    closes = df["close"].values.astype(float)
    opens = df["open"].values.astype(float)
    e20 = pd.Series(closes).ewm(span=20, adjust=False).mean().values
    e60 = pd.Series(closes).ewm(span=60, adjust=False).mean().values
    cash = 100.0
    pos = 0.0
    bought = False
    n = len(df)
    for i in range(60, n - 1):
        if not bought:
            if (e20[i] > e60[i] and closes[i] > e20[i]
                    and e20[i - 1] > e60[i - 1] and closes[i - 1] > e20[i - 1]):
                pos = cash / opens[i + 1]
                cash = 0.0
                bought = True
        else:
            if closes[i] < e60[i]:
                cash = pos * opens[i + 1] * (1 - 2 * FEE)
                pos = 0.0
                bought = False
    if bought:
        cash = pos * closes[-1] * (1 - 2 * FEE)
    return round(cash / 100.0 - 1, 4)


def daily_bull_map(daily_df):
    closes = daily_df["close"].values.astype(float)
    e20 = pd.Series(closes).ewm(span=20, adjust=False).mean().values
    e60 = pd.Series(closes).ewm(span=60, adjust=False).mean().values
    days = daily_df["open_time"].dt.floor("D")
    bull = {}
    for i in range(len(daily_df)):
        if i >= 1:
            bull[days.iloc[i]] = bool(e20[i] > e60[i] and closes[i] > e20[i])
    return bull


def softhold_variant(df, daily_df):
    """变体B: 用S1日线EMA20/60当懒熊过滤(快), 共振分继续算仓位。"""
    bull_d = daily_bull_map(daily_df)
    ind = vs.indicators(df)
    n = len(df)
    opens = df["open"].values.astype(float)
    closes = df["close"].values.astype(float)
    days = df["open_time"].dt.floor("D")

    target = np.zeros(n)
    for i in range(n):
        b = bull_d.get(days.iloc[i], False)
        if b and not pd.isna(ind["e12"].iloc[i]):
            _, sc = vs.score(ind, i)
            target[i] = sc / 100.0
        else:
            target[i] = 0.0

    C0 = 100.0
    cash = C0
    units = 0.0
    cur_f = 0.0
    for i in range(WARM, n - 1):
        f = target[i]
        if abs(f - cur_f) >= REBAL:
            px = opens[i + 1]
            val = cash + units * px
            d = f * val - units * px
            if d > 0:
                cash -= d
                units += (d * (1 - FEE)) / px
            elif d < 0:
                cash += (-d) * (1 - FEE)
                units += d / px
            cur_f = f
    if units > 0:
        cash += units * closes[-1] * (1 - 2 * FEE)
    return round(cash / C0 - 1, 4)


def run_variant(out):
    var = {}
    for sym, w, kind in COINS:
        df = load4h(sym, kind)
        daily = load_daily(sym, kind)
        var[sym] = softhold_variant(df, daily)
    port = round(sum(var[s] * w for (s, w, _) in COINS), 4)
    out["_variant"] = {"per_coin": {s: var[s] for s, _, _ in COINS},
                       "portfolio": port}
    print(f"[variant B S1日线主区] portfolio={port:+.2%}  "
          + "  ".join(f"{s[:4]}={var[s]:+.1%}" for s, _, _ in COINS))


def main():
    out = {}
    curves = {}
    for sym, w, kind in COINS:
        df = load4h(sym, kind)
        st = softhold(df)
        vb = vegas_binary(df)
        try:
            st["s1_daily"] = s1_daily(load_daily(sym, kind))
        except Exception:
            st["s1_daily"] = None
        st["weight"] = w
        st["vegas_binary"] = vb
        out[sym] = {k: v for k, v in st.items() if k not in ("eq", "times")}
        curves[sym] = (list(st["times"]), st["eq"], w)
        print(f"{sym:6s} soft={st['total']:+.2%} mdd={st['max_dd']:.2%} "
              f"exp={st['avg_exposure']:.0%} bh={st['buy_hold']:+.2%} "
              f"vbin={vb['total']:+.2%} s1={st['s1_daily']} rebs={st['rebalances']}")

    # 组合权益曲线: 按各币时间对齐(龙虾后加入则之前按现金持有)
    times = sorted({t for sym, (ts, eq, _) in curves.items() for t in ts})
    ti = {t: k for k, t in enumerate(times)}
    port = np.zeros(len(times))
    contrib = {}
    for sym, (ts, eq, w) in curves.items():
        arr = np.zeros(len(times))
        idx = [ti[t] for t in ts]
        arr[idx] = np.array(eq) * w
        # 币种数据开始前的部分: 权重以现金持有(不增长)
        last = 0.0
        prev_i = -1
        for t, v in zip(ts, eq):
            k = ti[t]
            if prev_i >= 0 and k > prev_i + 1:
                arr[prev_i + 1:k] = last
            prev_i = k
            last = v * w
        if prev_i < len(times) - 1:
            arr[prev_i + 1:] = last
        contrib[sym] = arr
        port += arr
    peak = np.maximum.accumulate(port)
    pdd = float(((port - peak) / peak).min())
    out["_portfolio"] = {
        "soft": round(port[-1] / 100.0 - 1, 4),
        "max_dd": round(pdd, 4),
        "buy_hold": round(sum(
            st.get("buy_hold", 0) * st.get("weight", 0)
            for sym, st in out.items() if sym != "_portfolio"), 4),
        "ts": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    print(f"[portfolio] soft={out['_portfolio']['soft']:+.2%} "
          f"mdd={out['_portfolio']['max_dd']:.2%} "
          f"buy&hold={out['_portfolio']['buy_hold']:+.2%}")
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "softhold_backtest.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    run_variant(out)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "softhold_backtest.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("saved softhold_backtest.json")


if __name__ == "__main__":
    main()