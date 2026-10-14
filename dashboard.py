import sys
import os
import json
import time
from datetime import datetime, timezone, timedelta

import dash
from dash import html, dcc, Input, Output, State
import plotly.graph_objects as go

from config import (
    SYMBOL, INTERVAL, KLINE_LIMIT,
    BASE_URL, PROXIES, REQUEST_RETRIES,
)
from fetch_data import (fetch_klines_any, fetch_klines_many,
                        fetch_futures_klines_many, fetch_live_price)
from ws_price import start_price_stream
from executor import get_spot_balances, get_futures_positions
from config import API_KEY, API_SECRET
import simfunding
import trend_engine
import macrofeed
import hongbao_radar
import discipline

CHART_CANDLES = 150
REFRESH_MS = 1000
KLINE_EVERY = 6
UTC8 = timezone(timedelta(hours=8))
ROUND_SECONDS = 300

state = {
    "df": None,
    "counter": 0,
    "updated_at": None,
    "error": None,
    "data_source": "镜像端点",
    "live_price": None,
    "live_price_ts": None,
    "price_source": None,
    "ws_ts": None,
    "account": None,
    "account_err": None,
    "trend": None,
    "trend_err": None,
}


def _to_epoch(ts):
    if isinstance(ts, (int, float)):
        return int(ts)
    dt = ts
    if hasattr(ts, "to_pydatetime"):
        dt = ts.to_pydatetime()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())



def _patch_current_candle(df, live):
    """把当前5分钟边界对应的K线收盘/高低价更新为最新的实时价。"""
    if df is None or df.empty or live is None:
        return
    bucket = int(state.get("live_price_ts") or 0)
    if not bucket:
        return
    bucket = int(bucket // ROUND_SECONDS * ROUND_SECONDS)
    last_idx = df.index[-1]
    last_bucket = int(_to_epoch(df.loc[last_idx, "open_time"]) // ROUND_SECONDS * ROUND_SECONDS)
    idx = None
    if last_bucket == bucket:
        idx = last_idx
    else:
        for i in range(len(df) - 1, -1, -1):
            b = int(_to_epoch(df.iloc[i]["open_time"]))
            if b == bucket:
                idx = df.index[i]
                break
    if idx is None:
        return
    df.loc[idx, "close"] = live
    df.loc[idx, "high"] = max(float(df.loc[idx, "high"]), live)
    df.loc[idx, "low"] = min(float(df.loc[idx, "low"]), live)


def _ws_on_update(msg):
    if not msg:
        return
    if msg.get("price") is not None:
        state["live_price"] = msg["price"]
        state["live_price_ts"] = msg["ts"]
        state["price_source"] = msg["source"]
        state["ws_ts"] = time.time()


def refresh():
    """每次界面tick调用: 1秒快通道(实时价+预测), 每KLINE_EVERY次拉整批K线。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    tick = state["counter"]

    # 1) 实时价: WebSocket新鲜则直接用, 否则HTTP轮询兜底
    ws_fresh = state.get("ws_ts") is not None and time.time() - state["ws_ts"] <= 4
    if not ws_fresh:
        try:
            lp = fetch_live_price(SYMBOL, PROXIES)
            state["live_price"] = lp["price"]
            state["live_price_ts"] = lp["time"]
            state["price_source"] = lp["source"]
        except Exception:
            pass

    # 2) 整批K线: 按KLINE_EVERY节流网络拉取
    need_klines = tick % KLINE_EVERY == 0 or state["df"] is None
    if need_klines:
        try:
            state["data_source"] = "币安实时(代理)" if PROXIES else "镜像端点"
            state["df"] = fetch_klines_any(SYMBOL, INTERVAL, KLINE_LIMIT, PROXIES)
        except Exception as e:
            if state["df"] is not None:
                state["error"] = str(e)
            else:
                raise

    if state["df"] is None:
        return

    # 3) 实时价回填当前K线
    if state.get("live_price") is not None:
        try:
            _patch_current_candle(state["df"], state["live_price"])
        except Exception:
            pass

    state["updated_at"] = now
    state["counter"] = tick + 1

    # 账户只读: 每20个tick拉一次(约20秒), 密钥为空则跳过
    if API_KEY and API_SECRET and tick % 20 == 0:
        try:
            state["account"] = {
                "spot": get_spot_balances(),
                "futures": get_futures_positions(),
                "ts": time.time(),
            }
            state["account_err"] = None
        except Exception as e:
            state["account_err"] = f"账户查询失败: {e}"

    # 资金费率套利: 首次初始化, 之后每60个tick补抓最新资金费
    if not simfunding._state["initialized"]:
        try:
            simfunding.init()
        except Exception:
            pass
    elif tick % 60 == 0:
        try:
            simfunding.update()
        except Exception:
            pass

    # S1慢趋势引擎: 启动首帧(tick=0)跳过, 交给首个后台tick计算以免拖慢服务器绑定
    if state["counter"] > 0 and (tick % 300 == 0 or state["trend"] is None):
        try:
            state["trend"] = trend_engine.refresh()
            state["trend_err"] = None
        except Exception as e:
            state["trend_err"] = str(e)

    # 宏观雷达: RSS头条+事件日历, 每10分钟刷新(缓存节流, 首帧不抓网)
    try:
        macrofeed.refresh_macro(state)
    except Exception:
        pass

    # 红包雷达: 官方公告红包/空投/活动, 内部10分钟节流
    try:
        hongbao_radar.refresh(state)
    except Exception:
        pass

    # 多币K线(顶部分图): SOL/XRP现货 + 龙虾永续, 每KLINE_EVERY同步
    try:
        refresh_coin_charts()
    except Exception:
        pass


COIN_CHART_SYMS = [("ETHUSDT", "spot"), ("SOLUSDT", "spot"), ("XRPUSDT", "spot"), ("龙虾USDT", "fut")]


def refresh_coin_charts():
    tick = state["counter"]
    if tick % KLINE_EVERY != 0 and state.get("coin_charts") is not None:
        return
    out = {}
    for sym, kind in COIN_CHART_SYMS:
        try:
            if kind == "fut":
                df = fetch_futures_klines_many(sym, "5m", 400)
            else:
                df = fetch_klines_many(sym, "5m", 400)
            if df is not None and not df.empty:
                out[sym] = df.tail(400)
                state.pop(sym + "_kerr", None)
        except Exception as e:
            state[sym + "_kerr"] = f"{sym} K线: {e}"
    if out:
        state["coin_charts"] = out



_COLORS = dict(up="#0ecb81", dn="#f6465d", ma="#f0b90b", ma2="#7b8ca3")


def build_coin_chart(sym, df, height=190):
    sd = df.tail(CHART_CANDLES)
    last = sd["close"].iloc[-1]
    prefix = "$" if last >= 1 else ""
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=sd["open_time"],
        open=sd["open"], high=sd["high"], low=sd["low"], close=sd["close"],
        name=sym,
        increasing_line_color=_COLORS["up"], decreasing_line_color=_COLORS["dn"],
        increasing_fillcolor=_COLORS["up"], decreasing_fillcolor=_COLORS["dn"],
        whiskerwidth=0.4,
    ))
    ma20 = sd["close"].rolling(20).mean()
    ma60 = sd["close"].rolling(60).mean()
    fig.add_trace(go.Scatter(x=sd["open_time"], y=ma20, mode="lines",
                             name="MA20", line=dict(color=_COLORS["ma"], width=1.1)))
    fig.add_trace(go.Scatter(x=sd["open_time"], y=ma60, mode="lines",
                             name="MA60", line=dict(color=_COLORS["ma2"], width=1.1)))
    fig.update_layout(
        template="plotly_dark",
        height=height,
        margin=dict(l=6, r=6, t=26, b=6),
        xaxis_rangeslider_visible=False,
        paper_bgcolor="#141820",
        plot_bgcolor="#141820",
        font=dict(color="#d0d5df"),
        title=dict(text=f"{sym}  5m   现价: {prefix}{last:,.4f}",
                   font=dict(size=13, color="#f0b90b")),
        showlegend=False,
        xaxis=dict(gridcolor="#1b2029"),
        yaxis=dict(gridcolor="#1b2029", tickprefix="$" if last >= 1 else ""),
    )
    return fig


def build_coin_grid():
    cards = []
    srcs = [("BTCUSDT", state.get("df")),
            ("ETHUSDT", (state.get("coin_charts") or {}).get("ETHUSDT")),
            ("SOLUSDT", (state.get("coin_charts") or {}).get("SOLUSDT")),
            ("XRPUSDT", (state.get("coin_charts") or {}).get("XRPUSDT")),
            ("龙虾USDT", (state.get("coin_charts") or {}).get("龙虾USDT"))]
    for sym, df in srcs:
        if df is None:
            cards.append(html.Div([
                html.Div(sym, style={"fontSize": "13px", "color": "#f0b90b",
                                     "fontWeight": "bold"}),
                html.Div("等待K线...", style={"fontSize": "12px",
                          "color": "#55606e", "marginTop": "8px"}),
            ], style={"flex": "1 1 22%", "minWidth": "230px",
                      "background": "#141820", "borderRadius": "14px",
                      "padding": "14px", "border": "1px dashed #2a3140",
                      "height": "196px"}))
        else:
            cards.append(html.Div(
                dcc.Graph(figure=build_coin_chart(sym, df),
                          config={"displayModeBar": False},
                          style={"height": "196px"}),
                style={"flex": "1 1 22%", "minWidth": "230px",
                       "background": "#141820", "borderRadius": "14px",
                       "padding": "8px", "border": "1px solid #232936"}))
    return html.Div(cards, style={"display": "flex", "gap": "12px", "flexWrap": "wrap"})

def build_macro_panel():
    m = state.get("macro") or {}
    items = (m.get("items") or [])[:6]
    events = macrofeed.upcoming_events()

    ev_chips = []
    for e in events[:3]:
        d = int(e["days"])
        tone = "#f6465d" if d <= 3 else ("#f0b90b" if d <= 10 else "#7b8ca3")
        ev_chips.append(html.Span(
            f"{e['name']} {e['date'][5:]} · 还剩{d}天",
            style={"background": tone + "22", "color": tone, "border": f"1px solid {tone}55",
                   "borderRadius": "10px", "padding": "4px 10px", "fontSize": "12px",
                   "marginRight": "8px", "display": "inline-block"}))

    rows = []
    if items:
        for it in items:
            if it.get("title", "").startswith("(订阅源"):
                rows.append(html.Div(it["title"], style={
                    "color": "#55606e", "fontSize": "12px", "padding": "6px 0",
                    "borderBottom": "1px solid #1d232f"}))
                continue
            chips = []
            for tname, color in it.get("tags", [])[:2]:
                chips.append(html.Span(tname, style={
                    "background": color + "22", "color": color, "borderRadius": "6px",
                    "padding": "1px 7px", "fontSize": "11px", "marginRight": "6px"}))
            url = it.get("url") or ""
            title_el = (html.A(it["title"], href=url, target="_blank",
                               style={"color": "#d0d5df", "textDecoration": "none",
                                      "fontSize": "13px"})
                        if url else html.Span(it["title"], style={"fontSize": "13px", "color": "#d0d5df"}))
            rows.append(html.Div([
                html.Div(chips, style={"marginBottom": "2px"}),
                html.Div([title_el, html.Span(
                    f"  {it.get('source','')} · {macrofeed.ago(it.get('ts'))}",
                    style={"color": "#55606e", "fontSize": "11px"})]),
            ], style={"padding": "7px 0", "borderBottom": "1px solid #1d232f"}))
    else:
        rows.append(html.Div("头条暂不可用(RSS/网络), 事件倒计时仍有效",
                             style={"color": "#55606e", "fontSize": "12px"}))

    err_txt = state.get("macro_err")
    return html.Div([
        html.Div("宏观雷达", style={"fontSize": "17px", "fontWeight": "bold", "color": "#f0b90b"}),
        html.Div([html.Div(ev_chips, style={"marginTop": "8px", "marginBottom": "6px"}),
                  html.Div("下次事件",
                           style={"fontSize": "11px", "color": "#55606e",
                                  "marginBottom": "2px"})]
                 if ev_chips else [""]),
        html.Div("— 头条 —", style={"fontSize": "11px", "color": "#55606e",
                                     "marginTop": "8px", "marginBottom": "2px"}),
        *rows,
        html.Div("新闻冲击时先看S1日线主区: 主区未破→利空即上车机会; 主区破→先撤再看新闻。"
                 + (f" (警示: {err_txt})" if err_txt else ""),
                 style={"color": "#55606e", "fontSize": "11px", "marginTop": "8px"}),
    ], style={"background": "#141820", "borderRadius": "14px", "padding": "16px",
              "border": "1px solid #232936", "marginBottom": "16px"})


def build_hongbao_panel():
    hb = state.get("hongbao") or {}
    items = (hb.get("items") or [])[:9]
    ts = time.strftime("%H:%M:%S", time.localtime(hb.get("ts", 0))) if hb.get("ts") else "--"
    head = f"红包雷达 · 币安官方公告 (红包/空投) · 更新 {ts}"
    rows = []
    for it in items:
        tone = "#f6465d" if it["tag"] == "红包/空投" else "#f0b90b"
        rows.append(html.Div([
            html.Span(it["tag"], style={
                "background": tone + "22", "color": tone, "borderRadius": "6px",
                "padding": "1px 7px", "fontSize": "11px", "marginRight": "8px",
                "display": "inline-block"}),
            html.A(it["title"], href=it["url"], target="_blank",
                   style={"color": "#d0d5df", "fontSize": "13px",
                          "textDecoration": "none"}),
            html.Span(f"  {it['ts'][:16]}", style={"color": "#55606e", "fontSize": "11px"}),
        ], style={"padding": "6px 0", "borderBottom": "1px solid #1d232f"}))
    note = ("广场个人帖被反爬(202/403)无法抓取，本面板只覆盖币安官方公告里的红包/空投，"
            "每10分钟自动刷新。参加需在APP内登录领取——本面板只提醒，不代领。")
    return html.Div([
        html.Div(head, style={"fontSize": "15px", "fontWeight": "bold", "color": "#0ecb81"}),
        *(rows or [html.Div("暂无红包/空投公告(官方发布后自动弹出)",
                            style={"color": "#55606e", "fontSize": "12px"})]),
        html.Div(note, style={"color": "#55606e", "fontSize": "11px", "marginTop": "8px"}),
    ], style={"background": "#141820", "borderRadius": "14px", "padding": "16px",
              "border": "1px solid #232936", "marginBottom": "16px"})


def build_discipline_panel(err=None):
    s = discipline.summary()
    color = {"正常": "#0ecb81", "警戒": "#f0b90b", "危险": "#f6465d", "失效": "#f6465d"}[s["status"]]
    ts_txt = time.strftime("%m-%d %H:%M", time.localtime(s["ts"])) if s["ts"] else "尚未记录"
    rows = []
    for g in s["gates"]:
        if g["triggered"]:
            txt = f"{g['name']} <-{g['gate']}% 已达到! 按计划:{g['action']}"
            rc = "#f6465d"
        else:
            txt = f"{g['name']} <-{g['gate']}%  再跌 {g['left_pct']:.1f}% 触发 → {g['action']}"
            rc = "#9aa6b5"
        rows.append(html.Div(txt, style={"fontSize": "12px", "color": rc, "padding": "3px 0"}))
    trap = [html.Div(f"☒ {t}", style={"fontSize": "12px", "color": "#b39dfb", "padding": "3px 0"})
            for t in discipline.TRAP_CHECKLIST]
    return html.Div([
        html.Div([
            html.Div("纪律仪表 · 回撤闸门 (实盘账户)", style={
                "fontSize": "16px", "fontWeight": "bold", "color": "#0ecb81"}),
            html.Span(f"状态: {s['status']}", style={
                "fontSize": "13px", "fontWeight": "bold", "color": color,
                "background": color + "22", "borderRadius": "6px",
                "padding": "2px 10px", "marginLeft": "10px",
                "display": "inline-block"}),
            html.Span(f"  上次记录 {ts_txt}", style={"fontSize": "11px", "color": "#55606e"}),
        ]),
        html.Div([
            html.Span(f"净值峰值 ${s['peak']:,.0f}", style={"color": "#9aa6b5", "fontSize": "13px"}),
            html.Span(f"  当前 ${s['current']:,.0f}", style={"color": "#d0d5df", "fontSize": "13px"}),
            html.Span(f"  回撤 {s['dd_pct']:.1f}%", style={"fontSize": "14px", "fontWeight": "bold",
                                                          "color": color}),
        ], style={"marginTop": "6px"}),
        html.Div(rows, style={"marginTop": "6px"}),
        html.Div([
            html.Div("诱多检查单 · 垂直蜡烛追涨前必查", style={
                "fontSize": "13px", "fontWeight": "bold", "color": "#b39dfb", "marginTop": "10px"}),
        ]),
        html.Div(trap, style={"marginTop": "4px"}),
        html.Div("在App里看到账户权益后填入下方并点记录(只读感知净值, 血线=纪律违约不可逆 -100%)",
                 style={"fontSize": "11px", "color": "#55606e", "marginTop": "8px"}),
        html.Div(err, style={"fontSize": "12px", "color": "#f6465d", "marginTop": "6px"}) if err else None,
    ], style={"background": "#141820", "borderRadius": "14px", "padding": "16px",
              "border": "1px solid #232936", "marginBottom": "16px"})


def build_account_panel():
    if not (API_KEY and API_SECRET):
        return html.Div()

    if state.get("account_err"):
        body = [html.Div(state["account_err"],
                         style={"fontSize": "13px", "color": "#f6465d", "marginTop": "6px"})]
    elif not state.get("account"):
        body = [html.Div("连接中...", style={"fontSize": "13px", "color": "#7b8ca3", "marginTop": "6px"})]
    else:
        acc = state["account"]
        blocks = []

        fu = acc.get("futures") or {}
        if fu and fu.get("wallet_balance") is not None:
            pnl = fu.get("unrealized_pnl", 0)
            pnl_color = "#0ecb81" if pnl >= 0 else "#f6465d"
            lines = [
                html.Div("U本位合约账户", style={"fontSize": "14px", "fontWeight": "bold",
                                                  "color": "#f0b90b"}),
                html.Div(
                    f"钱包余额 ${fu['wallet_balance']:,.2f}   保证金 ${fu.get('margin_balance', 0):,.2f}   "
                    f"未实现盈亏 {pnl:+,.2f}",
                    style={"fontSize": "13px", "color": pnl_color, "padding": "4px 0",
                           "borderBottom": "1px solid #1b2029"}),
            ]
            positions = fu.get("positions") or []
            if positions:
                lines.append(html.Div("持仓:", style={"fontSize": "13px", "color": "#7b8ca3",
                                                      "paddingTop": "4px"}))
                for p in positions[:8]:
                    amt = float(p.get("positionAmt", 0))
                    side = "多" if amt > 0 else "空"
                    entry = float(p.get("entryPrice", 0))
                    upnl = float(p.get("unrealizedProfit", 0))
                    uc = "#0ecb81" if upnl >= 0 else "#f6465d"
                    lines.append(html.Div(
                        f"{p.get('symbol')}  {side} {abs(amt)}   @{entry:.2f}   盈亏 {upnl:+,.2f}",
                        style={"fontSize": "13px", "color": uc, "padding": "3px 0"}))
            else:
                lines.append(html.Div("暂无持仓", style={"fontSize": "13px", "color": "#7b8ca3",
                                                          "padding": "4px 0"}))
            blocks.append(html.Div(lines, style={"marginBottom": "10px"}))

        s_bal = acc.get("spot") or {}
        if s_bal.get("rows"):
            rows = s_bal["rows"][:10]
            spot_header = (f"现货余额(合计≈{float(s_bal.get('total_btc', 0) or 0):.6f} BTC)  "
                           if s_bal.get("total_btc") else "现货余额")
            lines = [html.Div(spot_header, style={"fontSize": "14px", "fontWeight": "bold",
                                                    "color": "#f0b90b"})]
            for b in rows:
                lines.append(html.Div(
                    f"{b.get('asset')}  可用 {float(b.get('free', 0)):,.6f}   冻结 {float(b.get('locked', 0)):,.6f}",
                    style={"fontSize": "13px", "color": "#d0d5df", "padding": "3px 0",
                           "borderBottom": "1px solid #1b2029"}))
            blocks.append(html.Div(lines))

        if not blocks:
            body.append(html.Div("没有查询到数据", style={"fontSize": "13px", "color": "#7b8ca3"}))
        else:
            body = blocks

    return html.Div([
        html.Div([
            html.Div("账户(只读) - API已配置", style={"fontSize": "16px", "fontWeight": "bold",
                                                     "color": "#f0b90b"}),
            html.Div("本阶段仅查询余额/持仓(每20秒)。策略启动/下单按钮将在后续阶段加入。",
                     style={"fontSize": "12px", "color": "#7b8ca3"}),
        ]),
        *body,
    ], style={"background": "#141820", "borderRadius": "14px", "padding": "16px",
              "border": "1px solid #232936", "marginTop": "16px"})


def build_simfunding_panel():
    s = simfunding.summary()
    st = simfunding.next_settlement()
    pnl_color = "#0ecb81" if s["pnl"] >= 0 else "#f6465d"
    fund_fig = go.Figure()
    for info in simfunding._state["per_coin"].values():
        xs = [datetime.fromtimestamp(t / 1000, timezone.utc).replace(tzinfo=None) for t, _, _ in info["series"]]
        ys = [f for _, f, _ in info["series"]]
        fund_fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=info["sym"],
                                      line=dict(width=1.4)))
    fund_fig.update_layout(template="plotly_dark", paper_bgcolor="#171c25",
                           plot_bgcolor="#171c25", height=170,
                           margin=dict(l=40, r=10, t=10, b=20),
                           legend=dict(font=dict(size=9)),
                           yaxis=dict(tickfont=dict(size=9)))
    head_rows = []
    for r in s["rows"]:
        cr = r["cur_rate"]
        carry_txt = f'费{r["carry_pct"]:.1f}%年化'
        net_color = "#0ecb81" if r["net"] >= 0 else "#f6465d"
        head_rows.append(html.Div([
            html.Span(r["sym"].replace("USDT", ""),
                      style={"fontWeight": "bold", "color": "#f0b90b", "minWidth": "58px",
                             "display": "inline-block"}),
            html.Span(f"{r['window']} {r['periods']}期", style={"color": "#9aa6b5",
                                                                "fontSize": "11px",
                                                                "minWidth": "96px"}),
            html.Span(f"现费率 {cr*100:.4f}% ({carry_txt})", style={"color": "#c6cdd8",
                                                                   "minWidth": "110px"}),
            html.Span(f"资金费 +${r['funding']:.2f}", style={"color": "#0ecb81",
                                                             "minWidth": "86px"}),
            html.Span(f"价差 {r['floating']:+.2f}u", style={"color": "#9aa6b5",
                                                            "minWidth": "78px"}),
            html.Span(f"成本 -${r['fees']:.2f}", style={"color": "#f0b90b", "minWidth": "66px"}),
            html.Span(f"净 +${r['net']:.2f}", style={"color": net_color, "fontWeight": "bold"}),
        ], style={"padding": "6px 0", "borderBottom": "1px solid #1f2530"}))
    return html.Div([
        html.Div([
            html.Div("资金费率套利模拟 (方向中性·吃资金费·虚拟$10K)",
                     style={"fontSize": "16px", "fontWeight": "bold", "color": "#f0b90b"}),
            html.Div("现货买入+合约同额做空, 每8h吃一次资金费; 不看涨跌吃结构钱 · 数据源自真实fapi费率",
                     style={"fontSize": "12px", "color": "#7b8ca3"}),
        ]),
        html.Div([
            html.Span(f"期初 ${s['capital']:,.0f}", style={"color": "#7b8ca3"}),
            html.Span(f"  资金费合计 +${s['funding']:.2f}", style={"color": "#0ecb81"}),
            html.Span(f"  手续费 -${s['fees']:.2f}", style={"color": "#f0b90b"}),
            html.Span(f"  价差损益 {s['floating']:+.2f}u", style={"color": "#9aa6b5"}),
            html.Span(f"  当前权益 ${s['equity']:,.2f} ({s['pnl']:+.2f}u / {s['pnl_pct']:+.2f}%)",
                      style={"color": pnl_color, "fontWeight": "bold"}),
            html.Span(f"  |  距下次结算 {st['hms']}", style={"color": "#f0b90b", "fontWeight": "bold"}),
            html.Span(f"(UTC+8 {st['cn_time']} 收)", style={"color": "#7b8ca3"}),
        ], style={"fontSize": "13px", "marginTop": "6px"}),
        dcc.Graph(figure=fund_fig, config={"displayModeBar": False},
                  style={"height": "180px"}),
        html.Div(head_rows, style={"marginTop": "4px"}),
        html.Div("诚实提醒: 43天牛市里这只是+0.94%, 同期买入持有BTC约+35%——中性策略牛市必然跑输(那是安全的价格), "
                 "熊市/大跌时它才是保命道。当前费率偏低(约5-10%年化), 2021年高峰期普遍30-50%年化。",
                 style={"fontSize": "11px", "color": "#55606e", "marginTop": "8px"}),
    ], style={"background": "#171c25", "borderRadius": "14px", "padding": "14px",
              "border": "1px solid #232936", "marginTop": "16px"})


def _fmtpx(x):
    if x is None or x != x:
        return "--"
    if x >= 1000:
        return f"${x:,.2f}"
    if x >= 1:
        return f"${x:.3f}"
    return f"${x:.6f}"


def _fmtlvl(x):
    if x is None or x != x:
        return "--"
    if x >= 1000:
        return f"{x:,.0f}"
    if x >= 1:
        return f"{x:.2f}"
    return f"{x:.5f}"


def build_trend_panel():
    t = state.get("trend")
    if t is None:
        return html.Div("维纳斯通道引擎加载中...",
                        style={"background": "#171c25", "borderRadius": "14px",
                               "padding": "14px", "border": "1px solid #232936",
                               "marginTop": "16px", "color": "#7b8ca3", "fontSize": "13px"})
    coins = t["coins"]
    aw = t["active_weight"]
    sugg = t.get("suggested", aw)
    in_n = sum(1 for c in coins if c.get("long") and c.get("price") is not None)
    if sugg >= 0.85:
        rec, rec_color = (f"满仓持有(建议暴露{sugg*100:.0f}%) → 牛市拿住是唯一被验证的边缘",
                          "#0ecb81")
    elif sugg >= 0.50:
        rec, rec_color = (f"部分持有(建议暴露{sugg*100:.0f}%) → 等共振分数抬升",
                          "#f0b90b")
    else:
        rec, rec_color = (f"轻仓观望(建议暴露{sugg*100:.0f}%) → 主区/共振恶化,现金等待",
                          "#f6465d")

    fig = go.Figure()
    palette = ["#f0b90b", "#0ecb81", "#3b82f6", "#a855f7", "#e24a5b"]
    for i, c in enumerate(coins):
        if c.get("price") is None or not c.get("x"):
            continue
        col = palette[i % len(palette)]
        fig.add_trace(go.Scatter(
            x=c["x"], y=c["close_norm"], mode="lines", name=c["sym"],
            line=dict(width=1.6, color=col, dash=None if c.get("long") else "dot"),
            opacity=1.0 if c.get("long") else 0.45))
    fig.update_layout(template="plotly_dark", paper_bgcolor="#171c25",
                      plot_bgcolor="#171c25", height=200,
                      margin=dict(l=44, r=10, t=10, b=20),
                      legend=dict(font=dict(size=9)),
                      yaxis=dict(tickfont=dict(size=9), title="近90根4H %涨跌"),
                      title=dict(text="4H归一化走势 (实线=持仓做多, 虚线=空仓观望)",
                                 font=dict(size=11, color="#7b8ca3")))

    rows = []
    for c in coins:
        if c.get("price") is None:
            rows.append(html.Div([
                html.Span(c["sym"], style={"fontWeight": "bold",
                                           "color": "#f0b90b", "minWidth": "56px"}),
                html.Span(c["action"], style={"color": c["color"],
                                              "fontWeight": "bold"}),
                html.Span(c["status"], style={"color": "#55606e",
                                              "fontSize": "11px"}),
                html.Span(f"权重{c['weight']*100:.0f}%",
                          style={"color": "#7b8ca3", "fontSize": "11px"}),
            ], style={"padding": "6px 0", "borderBottom": "1px solid #1f2530"}))
            continue
        sc = c["score"]
        sc_col = "#0ecb81" if sc >= 80 else ("#f0b90b" if sc >= 55 else "#7b8ca3")
        retr_s = "--" if c.get("retr") is None else f"{c['retr']*100:.0f}%"
        regime = "多" if c["e576"] > c["e676"] else "空"
        rows.append(html.Div([
            html.Div([
                html.Span(c["sym"], style={"fontWeight": "bold", "color": "#f0b90b",
                                           "minWidth": "56px", "display": "inline-block"}),
                html.Span(_fmtpx(c["price"]),
                          style={"minWidth": "96px", "color": "#c6cdd8"}),
                html.Span(f"共振 {sc}/100",
                          style={"fontWeight": "bold", "color": sc_col,
                                 "minWidth": "78px"}),
                html.Span(c["action"], style={"fontWeight": "bold",
                                              "color": c["color"], "minWidth": "84px"}),
                html.Span(f"建议{c.get('exp', 0)*100:.0f}%/{c['weight']*100:.0f}%",
                          style={"color": "#7b8ca3", "fontSize": "11px"}),
            ], style={"padding": "6px 0 2px 0"}),
            html.Div([
                html.Div(style={"background": "#1b2029", "height": "6px",
                                "borderRadius": "3px", "flex": f"1 1 {max(0, 100-sc)}%"}),
                html.Div(style={"background": sc_col, "height": "6px",
                                "borderRadius": "3px", "flex": f"1 1 {sc}%"}),
            ], style={"display": "flex", "gap": "2px", "margin": "2px 0 6px 0"}),
            html.Div(
                f"隧道 E144 {_fmtlvl(c['e144'])} / E169 {_fmtlvl(c['e169'])}   |   "
                f"主区 E576 {_fmtlvl(c['e576'])} > E676 {_fmtlvl(c['e676'])} ({regime})   |   "
                f"MACD {_fmtlvl(c['dif'])} / {_fmtlvl(c['dea'])}   |   "
                f"斐位回撤 {retr_s}   |   " + c["status"],
                style={"fontSize": "11px", "color": "#9aa6b5",
                       "paddingBottom": "6px", "borderBottom": "1px solid #1f2530"}),
        ]))
        atr = c.get("atr")
        if atr:
            rows.append(html.Div(
                "波动率 ATR14(4H) = "
                f"{atr['atr_pct']}%  →  多: 止损 {_fmtpx(atr['stop_l'])} / "
                f"首目标 {_fmtpx(atr['tp_l'])}   |   空: 止损 {_fmtpx(atr['stop_s'])} / "
                f"首目标 {_fmtpx(atr['tp_s'])}   (2ATR止损/4ATR目标, 按波动率换算)",
                style={"fontSize": "11px", "color": "#8f9bad",
                       "padding": "2px 0 8px 0", "borderBottom": "1px solid #1f2530"}))
        elif atr is None and c.get("price"):
            rows.append(html.Div(
                "波动率 ATR失真(崩盘烛/极端行情) → 止损止盈改用结构位(等行情稳定后自动恢复)",
                style={"fontSize": "11px", "color": "#f6465d",
                       "padding": "2px 0 8px 0", "borderBottom": "1px solid #1f2530"}))
    upd = datetime.fromtimestamp(t["ts"]).strftime("%H:%M:%S")
    bt = t.get("bt")
    if bt and "_portfolio" in bt:
        soft = bt["_portfolio"].get("soft")
        bhh = bt["_portfolio"].get("buy_hold")
        v = bt.get("_variant", {})
        vb = v.get("portfolio")
        lob = bt.get("龙虾USDT", {})
        fnote = (f"软性持有诚实回测(333天,4H, 2025-10~2026-09): "
                 f"4H慢主区版 {soft*100:+.1f}%(回撤{bt['_portfolio'].get('max_dd',0)*100:.1f}%) vs "
                 f"买入持有 {bhh*100:+.1f}%  → 慢主区踏空修复; "
                 f"改S1日线主区+共振分(执行层) {vb*100:+.1f}% ≈ 买入持有。"
                 f"龙虾软性 { (lob.get('total') or 0)*100:+.1f}%(回撤"
                 f"{(lob.get('max_dd') or 0)*100:.0f}%)。执行按S1主区这版, 不美化净值。")
    else:
        fnote = "回测数据待生成(softhold_backtest.json)..."
    return html.Div([
        html.Div([
            html.Div("维纳斯通道引擎 (Vegas隧道144/169 · EMA12过滤 · 主区576/676 · MACD · 斐波那契)",
                     style={"fontSize": "16px", "fontWeight": "bold", "color": "#f0b90b"}),
            html.Div("执行策略(软性持有·S1门): 日线EMA20/60主区成立时按 共振分/100×权重 保留仓位; "
                     "主区跌破清仓; 龙虾=Meme实验仓5% · 每5分钟刷新",
                     style={"fontSize": "12px", "color": "#7b8ca3"}),
        ]),
        html.Div([
            html.Span(f"Vegas持多 {in_n}/5币 · 建议暴露 {sugg*100:.0f}%",
                      style={"color": "#c6cdd8"}),
            html.Span(f"   建议: {rec}",
                      style={"color": rec_color, "fontWeight": "bold"}),
            html.Span(f"   更新 {upd} (UTC+8)",
                      style={"color": "#55606e", "fontSize": "12px"}),
        ], style={"fontSize": "13px", "marginTop": "6px"}),
        dcc.Graph(figure=fig, config={"displayModeBar": False}, style={"height": "210px"}),
        html.Div(rows, style={"marginTop": "4px"}),
        html.Div(fnote,
                 style={"fontSize": "11px", "color": "#55606e", "marginTop": "8px"}),
    ], style={"background": "#171c25", "borderRadius": "14px", "padding": "14px",
              "border": "1px solid #232936", "marginTop": "16px"})



app = dash.Dash(__name__, title="多币K线 · 软性持有")

app.layout = html.Div(style={
    "background": "#10131a", "minHeight": "100vh", "padding": "20px",
    "fontFamily": "Arial, sans-serif", "color": "#d0d5df",
}, children=[
    html.Div(style={"maxWidth": "1200px", "margin": "0 auto"}, children=[
        html.Div([
            html.Div("多币K线 · 软性持有趋势面板",
                     style={"fontSize": "26px", "fontWeight": "bold", "color": "#f0b90b"}),
            html.Div(id="status-line",
                     style={"fontSize": "13px", "color": "#7b8ca3", "marginTop": "6px"}),
        ], style={"marginBottom": "16px"}),

        html.Div(id="coin-charts", style={"marginBottom": "16px"}),

        html.Div(id="macro-panel", style={"marginBottom": "16px"}),

        html.Div(id="hongbao-panel", style={"marginBottom": "16px"}),

        html.Div(id="discipline-panel", style={"marginBottom": "16px"}),

        html.Div([
            html.Span("实盘账户当前净值: ", style={"color": "#7b8ca3", "fontSize": "13px",
                                                  "marginRight": "6px"}),
            dcc.Input(id="discipline-eq", type="number", step=100, placeholder="例如 10000",
                      style={"width": "130px", "background": "#1b2130", "border": "1px solid #2a3242",
                             "color": "#d0d5df", "borderRadius": "6px", "padding": "5px 8px"}),
            html.Button("记录净值", id="discipline-save",
                        style={"marginLeft": "8px", "background": "#0ecb81", "border": "none",
                               "color": "#081", "borderRadius": "6px", "padding": "6px 14px",
                               "fontWeight": "bold", "cursor": "pointer"}),
        ], style={"marginBottom": "16px"}),

        html.Div(id="account-panel"),

        html.Div(id="fund-panel"),

        html.Div(id="trend-panel"),

        html.Div([
            "提示: 每币独立K线每6秒同步; 执行层=软性持有(S1日线主区+共振分); "
            "刻意撤下BTC/ETH 5分钟预测面板 —— 5分钟方向已被12000根样本外判定为噪声。",
        ], style={"color": "#55606e", "fontSize": "12px", "marginTop": "14px"}),

        dcc.Interval(id="tick", interval=REFRESH_MS, n_intervals=0),
    ]),
])


@app.callback(
    Output("coin-charts", "children"),
    Output("macro-panel", "children"),
    Output("hongbao-panel", "children"),
    Output("account-panel", "children"),
    Output("fund-panel", "children"),
    Output("trend-panel", "children"),
    Output("status-line", "children"),
    Input("tick", "n_intervals"),
)
def update(n):
    try:
        refresh()
        utc = state["updated_at"]
        cn = utc + timedelta(hours=8)
        nk = len(state["df"]) if state["df"] is not None else 0
        status = (f"更新于 {cn.strftime('%H:%M:%S')} (UTC+8)   |   轮询 #{state['counter']}   "
                  f"|   BTC K线 {nk} 根   |   数据源: {state['data_source']}")
        return (build_coin_grid(),
                build_macro_panel(), build_hongbao_panel(), build_account_panel(),
                build_simfunding_panel(), build_trend_panel(), status)
    except Exception as e:
        status = f"连接异常: {e} — 10秒后自动重试"
        fig = go.Figure(go.Scatter(x=[0], y=[0], mode="none"))
        fig.update_layout(template="plotly_dark", paper_bgcolor="#10131a",
                          annotations=[dict(text=status, showarrow=False, font=dict(color="#f6465d"))])
        return (html.Div("等待数据...", style={"textAlign": "center"}),
                html.Div("等待数据...", style={"textAlign": "center"}),
                html.Div("等待数据...", style={"textAlign": "center"}),
                html.Div("等待数据...", style={"textAlign": "center"}),
                html.Div("等待数据...", style={"textAlign": "center"}),
                html.Div("等待数据...", style={"textAlign": "center"}),
                status)


@app.callback(
    Output("discipline-panel", "children"),
    Input("discipline-save", "n_clicks"),
    State("discipline-eq", "value"),
)
def save_discipline(n_clicks, value):
    if not n_clicks or value is None:
        return build_discipline_panel()
    try:
        discipline.save(value)
        return build_discipline_panel()
    except Exception as e:
        return build_discipline_panel(err=f"记录失败: {e}")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("正在初始化...")
    refresh()
    try:
        simfunding.init()
        z = simfunding.summary()
        print(f"资金费率套利就绪: 43天纯资金费+${z['funding']:.2f}, "
              f"净值 {z['equity']:,.2f} ({z['pnl_pct']:+.2f}%)")
    except Exception as e:
        print(f"资金费率套利初始化失败: {e}")
    st = start_price_stream(SYMBOL, PROXIES, _ws_on_update)
    ws_label = "WS 已启动(后台重连)"
    print(f"WebSocket实时行情流 {ws_label} (stream.binance.com → data-stream.binance.vision)")
    print("初始化完成。多币K线分图 + 软性持有趋势 + 宏观雷达 就绪 (预测层已下线)")
    print("请在浏览器打开: http://127.0.0.1:8050")
    app.run(host="127.0.0.1", port=8050, debug=False)


if __name__ == "__main__":
    main()