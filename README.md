# Crypto Quant Dashboard · 加密货币量化看板

一个基于币安（Binance）公开数据的**实时行情看板 + 多策略回测 + 纸上交易**工具集。
用 Dash 做一个浏览器实时面板，把「行情 / 趋势引擎 / 资金费率 / 宏观新闻 / 红包雷达 / 账户只读 / 纪律仪表」整合到一页。

> **English**: A personal crypto quant toolkit built on Binance public market data — a real-time Dash dashboard, several strategy backtests, a delta-neutral funding-rate arbitrage simulator, an XGBoost 5-minute direction experiment, and a paper-trading ledger. **Read-only by design: it never places real orders.** For research/study only.

> **⚠️ 免责声明**
> - 本项目仅供**技术学习与个人研究**，不构成任何投资建议。
> - **本项目不包含下单/提币代码**，对币安的调用仅有公开行情与「只读」账户查询。
> - 加密货币交易风险极高，任何策略都可能亏损。作者不对使用本项目产生的任何后果负责。
> - 请遵守当地法律与币安平台规则，风险自负。

---

## ✨ 功能特性

| 面板 / 模块 | 说明 |
|---|---|
| **多币实时 K 线** | BTC / ETH / SOL / XRP + 永续（含 Meme 合约）分图，WebSocket 实时价 + HTTP 兜底，1 秒刷新 |
| **维纳斯通道趋势引擎** | 4H 级别 Vegas 隧道(144/169) + EMA12 过滤 + 主区 EMA576/676 + MACD + 斐波那契回撤，输出各币共振分与建议仓位 |
| **资金费率套利模拟** | 现货买入 + 合约等额做空的方向中性 carry 策略，读取真实 `fapi` 历史资金费，虚拟 $10K 计权益 |
| **宏观新闻雷达** | 抓取 CoinDesk / Cointelegraph / Decrypt RSS，按 FOMC/通胀/ETF/监管/地缘 分类打标，附 FOMC 事件倒计时 |
| **红包雷达** | 轮询币安官方公告，识别红包/空投/活动类公告并提醒（不代领） |
| **账户（只读）** | 签名查询现货余额与 U 本位合约持仓/未实现盈亏 |
| **纪律仪表** | 手动录入账户净值，自动跟踪峰值与回撤，三级回撤闸门（-15% / -25% / -40%）+ 追涨自查单 |
| **5 分钟方向预测** | XGBoost 二分类预测下一根 5m 涨跌（实验性；样本外验证后已从看板下线） |
| **纸上交易账本** | 记录每轮预测的方向/概率/池子占比与实际结果，按 A/B/C/D 多种规则评估假想投注 |
| **多策略回测** | EMA/唐奇安/布林/RSI/动量等策略跨币对比，输出胜率、盈亏比、回撤 |
| **数据桥接** | `browser_inject.js` 注入币安 Predict 页面，把页面数据回传到本机 Flask 收集器 |

---

## 🗂 目录结构

```
.
├── dashboard.py          # ★ 主程序：Dash 实时看板 (端口 8050)
├── main.py               # 命令行版：BTC 5m XGBoost 预测循环
├── config.example.py     # 配置模板（复制为 config.py 后填密钥）
│
├── fetch_data.py         # 币安 K 线/实时价拉取（含镜像回退、期货 K 线）
├── ws_price.py           # WebSocket 实时行情流
├── features.py           # 技术特征工程
├── model.py              # XGBoost 训练 / 预测
├── executor.py           # 币安签名【只读】查询（余额/持仓）
│
├── trend_engine.py       # 维纳斯通道趋势引擎
├── vegas_strategy.py     # Vegas 隧道指标与共振分
├── simfunding.py         # 资金费率套利模拟
├── macrofeed.py          # 宏观新闻 RSS 雷达
├── hongbao_radar.py      # 红包/空投公告雷达
├── discipline.py         # 回撤纪律仪表
├── paper_trade.py        # 纸上交易评估
├── paper_protocol.py     # 纸上交易协议
├── bridge.py             # 本机 Flask 数据收集器 (端口 8051)
├── browser_inject.js     # 注入币安 Predict 页面的桥接脚本
│
├── backtest.py           # 回测框架
├── strategies.py         # 多策略对比回测
├── vegas_backtest.py     # Vegas 策略回测
├── softhold_backtest.py  # 软性持有回测
├── trend_slow.py         # 慢趋势
├── market_regime.py      # 市场状态识别
├── model_zoo.py / model_zoo_fund.py   # 模型集 / 资金费率模型
├── signal_lever_test.py  # 信号与杠杆测试
├── combat_backtest.py    # 综合回测
├── calib_test.py         # 概率校准
├── wonky_screener.py     # 异动筛选
├── robust_check.py       # 稳健性检查
├── coins_analysis.py     # 币种分析
├── lobster_sim.py        # Meme 币模拟
├── test_run.py           # 快速自测
└── hr_practice.py        # 量化岗在线笔试练习题（18 题，自带 assert）
```

---

## 🚀 快速开始

### 环境要求
- Python 3.10+（开发环境为 3.14）
- 依赖见 `requirements.txt`

### 安装

```bash
git clone https://github.com/runningshark/crypto-quant-dashboard.git
cd crypto-quant-dashboard

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
```

### 配置

```bash
# Windows
copy config.example.py config.py
# macOS / Linux
cp config.example.py config.py
```

编辑 `config.py`：
- `PROXIES`：本地代理地址（如 `http://127.0.0.1:7897`）；没有就保持 `None`，程序会自动走镜像端点。
- `API_KEY` / `API_SECRET`：**留空即可运行看板**（账户面板会跳过）。如需查看余额/持仓再填，且只勾选「读取」权限。

> ⚠️ `config.py` 已被 `.gitignore` 忽略，**绝不要**把真实密钥提交到仓库。

### 运行

```bash
# 启动看板（推荐）
python dashboard.py
# 浏览器打开 http://127.0.0.1:8050
```

```bash
# 命令行版：BTC 5 分钟涨跌预测
python main.py

# 多策略回测对比
python strategies.py

# 量化笔试练习题自测
python hr_practice.py
```

### （可选）数据桥接

1. 运行 `python bridge.py`（或看板启动时一般是常驻的），本机监听 `8051`。
2. 在浏览器打开币安 Predict 页面，按 F12 → Console，粘贴 `browser_inject.js`（把顶部 `SYM` 改成 `BTC` 或 `ETH`）。
3. 保持标签页打开，机器人即可收到实时预测数据。

---

## 🧠 策略与研究笔记

项目里保留了若干**诚实的回测结论**（代码注释中亦有体现），例如：
- **5 分钟方向预测**：在约 12000 根样本外数据上被判定为噪声，因此预测面板已从看板下线。
- **资金费率套利**：方向中性、牛市必然跑输买入持有，主要价值在熊市/大跌时保命。
- **软性持有**：日线主区 + 共振分折算仓位，在 333 天 4H 回测中接近买入持有。

这些结论是项目的一部分，欢迎据此继续改进。

---

## 🤝 贡献

欢迎 Issue / PR。请注意：任何新代码都**不得**引入真实下单、提币或泄露密钥的逻辑。

## 📄 License

[MIT](LICENSE) © 2026 runningshark
