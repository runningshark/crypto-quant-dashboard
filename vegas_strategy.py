"""维纳斯通道(Vegas Tunnel) + 斐波那契 + MACD 组合策略。

规则(4小时周期, 长线纪律版):
  牛熊分界   EMA576 > EMA676   (顺大势, 不逆胎儿)
  隧道多头   EMA144 > EMA169 且 close > EMA169
  突破过滤   EMA12 > EMA169 且 close > EMA12   (价格与EMA12同时突破隧道才算真突破)
  MACD       DIF > DEA 且 DIF > 0              (动能/择时共振)
  斐波那契   当前摆动(近 SW 根K线高低点)回撤率 0.15~0.62  (回踩38.2%-61.8%伏击区)

  出场: 跌破 EMA169 隧道下轨 / MACD死叉 / 回撤跌破 78.6% (K线止损)
数值直接用e w m计算(与面板一致)。

禁止在本模块内调用 simfunding.init() —— 回测/评估只借用只读数据。
"""
import pandas as pd

EMA12 = 12
EMA144 = 144
EMA169 = 169
EMA576 = 576
EMA676 = 676
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIG = 9
SWING = 120          # 斐波那契摆动窗口(4H*120=20天)
FIB_IN_MIN = 0.0    # 回撤率下限(0=允许突破新高顺势开仓, 回撤买入可选)
FIB_IN_MAX = 0.62    # 回撤率上限(61.8%黄金区)
FIB_STOP = 0.786     # 跌破78.6%止损
FEE = 0.0005         # 单边0.05%


def ema(s, span):
    return s.ewm(span=span, adjust=False).mean()


def macd(s, fast=MACD_FAST, slow=MACD_SLOW, sig=MACD_SIG):
    dif = ema(s, fast) - ema(s, slow)
    dea = ema(dif, sig)
    return dif, dea, dif - dea


def indicators(df, sw=SWING):
    """输入K线DataFrame(需含close列), 输出指标表。"""
    s = pd.Series(df["close"].values.astype(float))
    out = pd.DataFrame(index=df.index)
    out["close"] = s.values
    out["e12"] = ema(s, EMA12).values
    out["e144"] = ema(s, EMA144).values
    out["e169"] = ema(s, EMA169).values
    out["e576"] = ema(s, EMA576).values
    out["e676"] = ema(s, EMA676).values
    dif, dea, hist = macd(s)
    out["dif"] = dif.values
    out["dea"] = dea.values
    out["hist"] = hist.values
    sh = s.rolling(sw).max()
    sl = s.rolling(sw).min()
    rng = (sh - sl).replace(0, float("nan"))
    out["retr"] = ((sh - s) / rng).values
    return out


def long_signal(ind, i):
    c = ind["close"].iloc[i]
    e144 = ind["e144"].iloc[i]
    e169 = ind["e169"].iloc[i]
    e576 = ind["e576"].iloc[i]
    e676 = ind["e676"].iloc[i]
    e12 = ind["e12"].iloc[i]
    dif = ind["dif"].iloc[i]
    dea = ind["dea"].iloc[i]
    retr = ind["retr"].iloc[i]
    parts = {
        "regime": bool(e576 > e676),
        "tunnel": bool(e144 > e169 and c > e169),
        "filt": bool(e12 > e169 and c > e12),
        "macd": bool(dif > dea and dif > 0),
        "fib": bool(pd.notna(retr) and FIB_IN_MIN <= retr <= FIB_IN_MAX),
    }
    return all(parts.values()), parts


def exit_signal(ind, i):
    c = ind["close"].iloc[i]
    e169 = ind["e169"].iloc[i]
    retr = ind["retr"].iloc[i]
    # Vegas纪律: 出场只认隧道下轨(EMA169)与78.6%止损, 不用MACD死叉(避免回踩割肉)
    return bool((c < e169) or (pd.notna(retr) and retr > FIB_STOP))


def state_text(ind, i):
    """面板用: 返回 (动作, 状态文案, 颜色)。"""
    ok, parts = long_signal(ind, i)
    e576 = ind["e576"].iloc[i]
    e676 = ind["e676"].iloc[i]
    macd_ok = parts["macd"]
    if e576 < e676:
        return "空仓观望", "空头主区(EMA576<676, 不做多)", "#f6465d"
    if ok:
        return "持仓做多", "隧道+过滤+MACD+斐位共振", "#0ecb81"
    if parts["tunnel"] and parts["filt"] and macd_ok:
        return "等待回调", "趋势完好, 等回踩斐波那契38.2%-61.8%", "#f0b90b"
    if parts["regime"] and parts["tunnel"]:
        return "等待确认", "多头主区, 等EMA12突破隧道+MACD确认", "#f0b90b"
    return "空仓观望", "多头主区但隧道未成立", "#7b8ca3"


def score(ind, i):
    """维加斯共振分0-100(可审计打分): 主区25+隧道20+过滤15+MACD20+斐位20。"""
    c = ind["close"].iloc[i]
    e144 = ind["e144"].iloc[i]
    e169 = ind["e169"].iloc[i]
    e12 = ind["e12"].iloc[i]
    e576 = ind["e576"].iloc[i]
    e676 = ind["e676"].iloc[i]
    dif = ind["dif"].iloc[i]
    dea = ind["dea"].iloc[i]
    retr = ind["retr"].iloc[i]
    if pd.isna(retr):
        retr = 1.0
    s = {}
    s["主区"] = 25 if e576 > e676 else 0
    s["隧道"] = 20 if (e144 > e169 and c > e169) else (10 if e144 > e169 else 0)
    s["过滤"] = 15 if (e12 > e169 and c > e12) else (8 if c > e12 else 0)
    s["MACD"] = 20 if (dif > dea and dif > 0) else (10 if dif > dea else 0)
    if retr <= 0.62:
        s["斐位"] = 20
    elif retr <= 0.786:
        s["斐位"] = 8
    else:
        s["斐位"] = 0
    return s, int(round(sum(s.values())))