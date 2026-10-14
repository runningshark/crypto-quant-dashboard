"""纪律仪表: 实盘账户回撤闸门 (用户手动喂当前净值)。

三级闸门(开仓前计划):
  G1 -15%  →  仓位减半, 只做最高置信触发
  G2 -25%  →  清仓停手, 逐笔复盘检查单
  G3 -40%  →  宣告策略失效, 重做回测
峰值自动跟踪: 新记录高于历史峰值则更新峰值。
"""
import json
import os
import time

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "discipline.json")

GATES = [("G1", 15, "仓位减半，只做最高置信触发"),
         ("G2", 25, "清仓停手，逐笔复盘检查单"),
         ("G3", 40, "宣告策略失效，重做回测")]

TRAP_CHECKLIST = [
    "垂直蜡烛: 单根4H直线+30%且其后无回踩阶梯 -> 不追 (AIN/BR/LAB家族特征)",
    "入场要有回踩位: 站上隧道后的第一个回调, 而非追最高点",
    "成本被套: 放弃锚定成本, 今天的钱按今天的价重新决策",
    "结构才有依据: 4H收盘站回e12之前, 反弹只是屠刀缝隙",
    "杠杆自检: 10x要求止损距离<=6%, 垂直币一根就能穿止损",
]


def _default():
    return {"peak": 10000.0, "current": 10000.0, "ts": 0}


def load():
    try:
        with open(FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        peak = float(d.get("peak", 0) or 0)
        cur = float(d.get("current", d.get("peak", 0)) or 0)
        if peak <= 0 and cur <= 0:
            return _default()
        if peak <= 0:
            peak = cur
        return {"peak": peak, "current": cur, "ts": float(d.get("ts", 0))}
    except Exception:
        return _default()


def save(current):
    cur = float(current)
    d = load()
    if cur > d["peak"] or d["ts"] == 0:
        d["peak"] = cur
    d["current"] = cur
    d["ts"] = time.time()
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return d


def summary():
    d = load()
    cur, peak = d["current"], d["peak"]
    dd = (peak - cur) / peak if peak > 0 else 0.0
    dd_pct = dd * 100
    gates = []
    for name, gate, action in GATES:
        gates.append({"name": name, "gate": gate, "action": action,
                      "left_pct": gate - dd_pct, "triggered": dd_pct >= gate})
    if dd_pct >= 40:
        status = "失效"
    elif dd_pct >= 25:
        status = "危险"
    elif dd_pct >= 15:
        status = "警戒"
    else:
        status = "正常"
    return {"current": cur, "peak": peak, "dd_pct": dd_pct,
            "gates": gates, "status": status, "ts": d["ts"]}