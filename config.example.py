# -*- coding: utf-8 -*-
"""示例配置。复制本文件为 config.py 后再填入你自己的值：
    copy config.example.py config.py
config.py 已被 .gitignore 忽略，切勿把真实密钥提交到仓库。
"""

SYMBOL = "BTCUSDT"
INTERVAL = "5m"
KLINE_LIMIT = 1000

# ---- 行情端点 ----
# 优先走 www.binance.com 实时接口(部分地区需代理)，失败自动回退 data-api 镜像。
BASE_URL = "https://data-api.binance.vision/api/v3/klines"
WWW_API = "https://www.binance.com/api/v3"
MIRROR_API = "https://data-api.binance.vision/api/v3"

# 本地代理(如 Clash/V2Ray)。没有代理就设为 None，会自动走镜像端点。
# PROXIES = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
PROXIES = None
REQUEST_RETRIES = 5

# ---- 账户(只读) ----
# 在币安 App/网页: 账户 → API管理 → 创建API Key。
# 只勾选"读取"权限即可(本项目不下单); 强烈建议绑定本机 IP 白名单。
# 留空则看板跳过账户面板。
API_KEY = ""       # 填入你的 API Key
API_SECRET = ""    # 填入你的 API Secret
SPOT_API = "https://api.binance.com"
FAPI_API = "https://fapi.binance.com"

RETRAIN_EVERY = 60
PREDICT_EVERY = 5

# ---- 回测参数 ----
HISTORY_CANDLES = 6000         # 回测用历史K线总量(约20天)
BACKTEST_TRAIN_WINDOW = 2000   # 每次训练的K线长度
BACKTEST_RETRAIN = 50          # 每隔多少根K线重训一次
BACKTEST_STAKE = 10            # 每注金额
BACKTEST_PAYOUT = 1.9          # 胜出返还倍数(1.9表示押100赢190)
BACKTEST_CONF = 0.55           # 只在概率>=55%时下注

TRAIN_RATIO = 0.8
RANDOM_STATE = 42

XGBOOST_PARAMS = {
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "max_depth": 6,
    "learning_rate": 0.05,
    "n_estimators": 300,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "random_state": RANDOM_STATE,
}
