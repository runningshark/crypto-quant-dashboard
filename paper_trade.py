"""纸上交易评估器: 每一轮币安Predict(5分钟)自动记录
模型预测/概率/指导价/池子占比/实际结果, 并按规则评估假想下注盈亏。"""
import os
import json
from datetime import datetime, timezone

PAPER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_trades.jsonl")
PAYOUT = 1.9
MIN_EDGE = 0.05
CONF_THRESHOLD = 0.95


def paper_file(symbol="BTCUSDT"):
    if symbol == "BTCUSDT":
        return PAPER_FILE
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        f"paper_trades_{symbol}.jsonl")


def load_all(symbol="BTCUSDT"):
    rounds = []
    path = paper_file(symbol)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rounds.append(json.loads(line))
                    except Exception:
                        pass
    return rounds


def append_round(r, symbol="BTCUSDT"):
    with open(paper_file(symbol), "a", encoding="utf-8") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")


def snapshot_pool(predict):
    """从币安Predict页面数据提取池子金额/人数占比; 拿不到返回 None。"""
    if not predict:
        return None
    market = predict.get("market")
    data = market.get("data") if isinstance(market, dict) else None
    body = data if isinstance(data, dict) else market if isinstance(market, dict) else None
    if not isinstance(body, dict):
        return None

    def num(k):
        v = body.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        return None

    up_amt = num("upAmount")
    down_amt = num("downAmount")
    up_cnt = num("upCount")
    down_cnt = num("downCount")

    if up_amt is not None and down_amt is not None and (up_amt + down_amt) > 0:
        return {"up": up_amt, "down": down_amt, "share": up_amt / (up_amt + down_amt), "kind": "金额"}
    if up_cnt is not None and down_cnt is not None and (up_cnt + down_cnt) > 0:
        return {"up": up_cnt, "down": down_cnt, "share": up_cnt / (up_cnt + down_cnt), "kind": "人数"}
    return None


def paper_tick(state, symbol="BTCUSDT"):
    """每个tick调用。新边界打开时开一轮; 下一边界到来时结算上一轮。"""
    suffix = "" if symbol == "BTCUSDT" else "_eth"
    ar = state.get("api_round" + suffix)
    if not ar:
        return
    cur = ar["boundary"]
    prev = state.get("_paper_boundary" + suffix)
    pending = state.get("_paper_pending" + suffix)

    if pending and pending.get("boundary", -1) < cur:
        outcome = "UP" if ar["price"] > pending["set_price"] else "DOWN"
        rec = dict(pending)
        rec["outcome"] = outcome
        rec["result_price"] = ar["price"]
        rec["ts_settle"] = datetime.fromtimestamp(cur, timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S")
        rec["won_model"] = rec.get("direction") == outcome
        append_round(rec, symbol)
        state["_paper_pending" + suffix] = None

    if prev != cur or pending is None:
        res = state.get("result" + suffix) or {}
        direction = res.get("direction")
        state["_paper_boundary" + suffix] = cur
        if not direction:
            return
        rec = {
            "boundary": cur,
            "ts_open": datetime.fromtimestamp(cur, timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"),
            "set_price": ar["price"],
            "direction": direction,
            "prob_up": res.get("prob_up"),
            "symbol": symbol,
        }
        pool = snapshot_pool(state.get("predict_eth") if symbol != "BTCUSDT"
                             else state.get("predict"))
        if pool:
            rec.update({
                "up_pool": pool["up"],
                "down_pool": pool["down"],
                "up_share": round(pool["share"], 4),
                "pool_kind": pool["kind"],
            })
        state["_paper_pending" + suffix] = rec


def _fmt(st):
    n = st["n"]
    if not n:
        return {"n": 0, "win": 0, "rate": None, "roi": None, "net": None}
    rate = st["win"] / n
    net = st["win"] * (PAYOUT - 1) - (n - st["win"])
    roi = (st["win"] * PAYOUT - n) / n
    return {"n": n, "win": st["win"], "rate": rate, "roi": roi, "net": net}


def compute_stats(rounds):
    a = {"n": 0, "win": 0}
    b = {"n": 0, "win": 0, "skipped": 0}
    c = {"n": 0, "win": 0, "skipped": 0}
    d = {"n": 0, "win": 0}
    d85 = {"n": 0, "win": 0}
    for r in rounds:
        pred = r.get("direction")
        outc = r.get("outcome")
        if not pred or not outc:
            continue
        a["n"] += 1
        if pred == outc:
            a["win"] += 1

        d["n"] += 1
        if pred != outc:
            d["win"] += 1

        prob = r.get("prob_up")
        if prob is None:
            b["skipped"] += 1
            c["skipped"] += 1
            continue
        share = r.get("up_share")
        if share is None:
            share = 0.5
        if pred == "UP":
            condition = prob > share + MIN_EDGE
            max_prob = prob
        else:
            condition = (1.0 - prob) > (1.0 - share) + MIN_EDGE
            max_prob = 1.0 - prob
        if not condition:
            b["skipped"] += 1
        else:
            b["n"] += 1
            if pred == outc:
                b["win"] += 1

        if max_prob >= CONF_THRESHOLD:
            c["n"] += 1
            if pred == outc:
                c["win"] += 1
        else:
            c["skipped"] += 1

        if max_prob >= CONF_THRESHOLD:
            d85["n"] += 1
            if pred != outc:
                d85["win"] += 1
    fb = _fmt(b)
    fb["skipped"] = b["skipped"]
    fc = _fmt(c)
    fc["skipped"] = c["skipped"]
    return {"A": _fmt(a), "B": fb, "C": fc, "D": _fmt(d), "D85": _fmt(d85),
            "total": len(rounds)}