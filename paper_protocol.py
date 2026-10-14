# -*- coding: utf-8 -*-
"""纸面执行器 (Paper Executor): 只用验证过的两条策略。
  A) S1 软持有: BTC/ETH/SOL/XRP 现货, 日线 EMA20>EMA60 持币, 破位卖
  B) 资金费 Carry: 记录 4 币永续资金费率年化 vs 100 名义额日累计
纸面资本 10000 虚拟。规则: 单策略不预测, 只按收盘信号; 结果先跑 20~30 轮才接真钱。
"""
import os
import sys
import time
import json

import requests

from config import PROXIES

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trend_engine as te

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_ledger.json")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
VIRTUAL = 10000.0
CARRY_NOTIONAL = 100.0


def _daily_price(sym):
    df = te._load_daily(sym, "spot")
    return df


def _s1_state(sym):
    try:
        df = te._load_daily(sym, "spot")
        return bool(te._s1_daily_bull(df))
    except Exception:
        return False


def _funding_rates():
    out = {}
    s = requests.Session()
    s.proxies.update(PROXIES or {})
    for sym in COINS:
        try:
            j = s.get("https://fapi.binance.com/fapi/v1/premiumIndex",
                      params={"symbol": sym}, timeout=20).json()
            r = float(j.get("lastFundingRate", 0)) or 0.0
            out[sym] = {"rate": r, "annual": r * 1095.0}  # 8h结算 *3/天 *365
        except Exception:
            out[sym] = {"rate": 0.0, "annual": 0.0}
    return out


def _load():
    if os.path.exists(LEDGER):
        try:
            with open(LEDGER, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"start": time.strftime("%Y-%m-%d %H:%M"),
            "capital": VIRTUAL, "cash": VIRTUAL, "pos": {},
            "history": [], "trades": [], "carry_total_ann": 0.0}


def _save(led):
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(led, f, ensure_ascii=False, indent=2)


def _equity(led, price_map):
    eq = led["cash"]
    for sym, p in led["pos"].items():
        eq += p["shares"] * price_map.get(sym, p["entry"])
    return eq


def step():
    """正向一轮: 用最近一根已收盘日线的收盘价做信号与成交。"""
    led = _load()
    prices = {}
    signals = {}
    for sym in COINS:
        df = _daily_price(sym)
        closes = [float(x) for x in df["close"]]
        if len(closes) < 3:
            continue
        eval_close = closes[-2]
        signals[sym] = _s1_state(sym)
        prices[sym] = eval_close
        pos = led["pos"].get(sym)
        if signals[sym] and (pos is None or pos.get("state") != "持币"):
            # 进场: 5%位等权再平衡 → 简单分配剩余现金
            n_open = sum(1 for p in led["pos"].values() if p["state"] == "持币")
            alloc = led["cash"] / max(1, len(COINS) - n_open) if n_open == 0 else led["cash"]
            shares = alloc / eval_close
            led["pos"][sym] = {"state": "持币", "shares": shares, "entry": eval_close}
            led["cash"] -= shares * eval_close
            led["trades"].append({"date": time.strftime("%Y-%m-%d %H:%M"), "sym": sym,
                                  "side": "买", "price": eval_close})
        elif not signals[sym] and pos is not None and pos.get("state") == "持币":
            led["cash"] += pos["shares"] * eval_close
            led["trades"].append({"date": time.strftime("%Y-%m-%d %H:%M"), "sym": sym,
                                  "side": "卖", "price": eval_close,
                                  "pnl_pct": round((eval_close / pos["entry"] - 1) * 100, 2)})
            del led["pos"][sym]
    funds = _funding_rates()
    carry = sum(v["annual"] for v in funds.values()) / len(funds) if funds else 0.0
    led["carry_total_ann"] = carry
    eq = _equity(led, prices)
    led["history"].append({
        "ts": time.strftime("%Y-%m-%d %H:%M"), "cash": round(led["cash"], 2),
        "equity": round(eq, 2), "signals": signals,
        "carry_ann": round(carry, 2) if not isinstance(carry, complex) else 0.0})
    _save(led)
    return led, prices


def replay(days=30):
    """历史回放: 用最近 days 根已收盘日线, 逐日执行 S1 信号, 输出成绩单。"""
    led = _load()
    led["cash"] = led["capital"]
    led["pos"] = {}
    led["trades"] = []
    led["history"] = []
    dfs = {s: _daily_price(s) for s in COINS}
    n = min(days, min(len(dfs[s]["close"]) - 2 for s in COINS))
    for i in range(-n - 1, -1):
        closes = {s: float(dfs[s]["close"].iloc[i]) for s in COINS}
        for sym in COINS:
            closes_pre = [float(x) for x in dfs[sym]["close"].iloc[:i + 1]]
            if len(closes_pre) < 60:
                continue
            ema20 = sum(closes_pre[-20:]) / 20.0
            ema60 = sum(closes_pre[-60:]) / 60.0
            sig = ema20 > ema60
            pos = led["pos"].get(sym)
            if sig and (pos is None or pos["state"] != "持币"):
                n_open = sum(1 for p in led["pos"].values() if p["state"] == "持币")
                alloc = led["cash"] / max(1, len(COINS) - n_open) if not n_open else led["cash"]
                shares = alloc / closes[sym]
                led["pos"][sym] = {"state": "持币", "shares": shares, "entry": closes[sym]}
                led["cash"] -= shares * closes[sym]
            elif not sig and pos is not None and pos["state"] == "持币":
                led["cash"] += pos["shares"] * closes[sym]
                led["trades"].append({"date": str(i), "sym": sym,
                                      "price": closes[sym],
                                      "pnl_pct": round((closes[sym] / pos["entry"] - 1) * 100, 2)})
                del led["pos"][sym]
        eq = led["cash"] + sum(p["shares"] * closes[s] for s, p in led["pos"].items())
        led["history"].append({"day": str(i), "equity": round(eq, 2)})
    led["equity_final"] = round(led["cash"] + sum(
        p["shares"] * (float(dfs[s]["close"].iloc[-2])) for s, p in led["pos"].items()), 2)
    _save(led)
    return led


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "replay"
    if mode == "replay":
        led = replay(30)
        print("=== 纸面执行器 · S1软持有 (BTC/ETH/SOL/XRP 现货) 近30已收盘日 回放 ===")
        print("起始虚拟本金: $%.0f" % led["capital"])
        print("期末权益:     $%s" % led.get("equity_final"))
        print("累计交易笔数: %d" % len(led["trades"]))
        wins = [t for t in led["trades"] if t.get("pnl_pct", 0) > 0]
        print("盈利笔数:     %d  亏损笔数: %d" % (len(wins), len(led["trades"]) - len(wins)))
        if led["trades"]:
            avg = sum(t.get("pnl_pct", 0) for t in led["trades"]) / len(led["trades"])
            print("每笔平均:     %+.2f%%" % avg)
        eqs = [h["equity"] for h in led["history"]]
        if eqs:
            peak = max(eqs)
            print("期间峰值权益: $%.2f   最大回撤: %.2f%%" % (
                peak, (peak - eqs[-1]) / peak * 100 if peak else 0))
        h = led["history"][0]
        print("首日权益: $%.2f → 末日权益: $%.2f" % (h["equity"], eqs[-1]))
    else:
        led, prices = step()
        print("=== 正向推进一轮 ===")
        print("现金: $%.2f   持仓: %s   权益: $%.2f   资金费年化基线: %.2f%%" % (
            led["cash"], {k: round(p["shares"], 4) for k, p in led["pos"].items()},
            round(_equity(led, prices), 2), led.get("carry_total_ann", 0)))
        print("交易日志尾部:", led["trades"][-3:])