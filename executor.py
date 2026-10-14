"""币安API执行器: 第一阶段只读(查余额/持仓)。策略下单在后续阶段加入。"""
import time
import hmac
import hashlib
import urllib.parse

import requests

from config import API_KEY, API_SECRET, SPOT_API, FAPI_API, PROXIES

QUOTE_ASSETS = {"USDT", "USDC", "BUSD", "FDUSD", "BTC", "ETH", "BNB"}


def _sign(params: dict, secret: str) -> str:
    q = urllib.parse.urlencode(params)
    return hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()


def _signed_get(url: str, params: dict, key: str, secret: str,
                proxies: dict, retries: int = 3, timeout: int = 20):
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 15000
    q = urllib.parse.urlencode(params)
    full = f"{url}?{q}&signature={_sign(params, secret)}"
    session = requests.Session()
    if proxies:
        session.proxies.update(proxies)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(full, timeout=timeout,
                               headers={"X-MBX-APIKEY": key})
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(attempt * 2)
    raise ConnectionError(f"Binance API error: {last_err}")


def get_spot_balances(key=API_KEY, secret=API_SECRET, proxies=PROXIES):
    if not key or not secret:
        return None
    data = _signed_get(SPOT_API + "/api/v3/account", {}, key, secret, proxies)
    rows = [b for b in data.get("balances", [])
            if float(b.get("free", 0)) > 0 or float(b.get("locked", 0)) > 0]
    return {"rows": rows, "total_btc": data.get("totalAssetOfBtc")}


def get_futures_positions(key=API_KEY, secret=API_SECRET, proxies=PROXIES):
    """USDT-M 合约持仓(含杠杆账户信息), 返回持仓列表与汇总。"""
    if not key or not secret:
        return None
    data = _signed_get(FAPI_API + "/fapi/v2/positionRisk", {}, key, secret, proxies)
    positions = [p for p in data if abs(float(p.get("positionAmt", 0))) > 0]
    try:
        acc = _signed_get(FAPI_API + "/fapi/v2/account", {}, key, secret, proxies)
    except Exception:
        acc = None
    total_pnl = sum(float(p.get("unrealizedProfit", 0)) for p in positions)
    return {
        "positions": positions,
        "wallet_balance": float(acc.get("totalWalletBalance", 0)) if acc else None,
        "unrealized_pnl": float(acc.get("totalUnrealizedProfit", total_pnl)) if acc else total_pnl,
        "margin_balance": float(acc.get("totalMarginBalance", 0)) if acc else None,
    }