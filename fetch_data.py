import time
import requests
import pandas as pd

DEFAULT_BASE_URL = "https://data-api.binance.vision/api/v3/klines"
WWW_API = "https://www.binance.com/api/v3"
MIRROR_API = "https://data-api.binance.vision/api/v3"


def fetch_klines_any(symbol: str, interval: str, limit: int,
                     proxies: dict = None,
                     retries: int = 2) -> pd.DataFrame:
    """优先 www.binance.com(实时,需代理)，失败回退镜像。快超时避免卡顿。"""
    urls = []
    if proxies:
        urls.append((WWW_API + "/klines", proxies, 6))
    urls.append((DEFAULT_BASE_URL, None, 15))
    last_err = None
    for u, px, to in urls:
        try:
            return fetch_klines(symbol, interval, limit,
                                base_url=u, proxies=px, retries=retries, timeout=to)
        except Exception as e:
            last_err = e
    raise ConnectionError(f"klines unavailable: {last_err}")


def fetch_live_price(symbol: str, proxies: dict = None) -> dict:
    """实时价格，优先 www.binance.com(需代理)，失败回退镜像。"""
    candidates = []
    if proxies:
        candidates.append((WWW_API + "/ticker/price", proxies, "www"))
    candidates.append((MIRROR_API + "/ticker/price", None, "mirror"))

    for url, px, src in candidates:
        try:
            data = _http_get(url, {"symbol": symbol}, px, 2, naptime=2, timeout=6)
            return {
                "price": float(data["price"]),
                "source": src,
                "time": time.time(),
            }
        except Exception:
            continue
    raise ConnectionError("live price unavailable")


def _parse_kline_data(data) -> pd.DataFrame:
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])

    for col in ("open", "high", "low", "close", "volume", "quote_volume",
                "taker_buy_base", "taker_buy_quote"):
        df[col] = df[col].astype(float)

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    df["trades"] = df["trades"].astype(int)

    return df


def _http_get(url: str, params: dict, proxies: dict, retries: int, naptime: int = 5, timeout: int = 20) -> list:
    session = requests.Session()
    session.proxies.update(proxies or {})
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(naptime * attempt)
    raise ConnectionError(f"Failed to fetch {url}: {last_err}")


def fetch_klines(symbol: str, interval: str, limit: int = 500,
                 base_url: str = DEFAULT_BASE_URL,
                 proxies: dict = None,
                 retries: int = 3,
                 timeout: int = 20) -> pd.DataFrame:
    data = _http_get(base_url, {"symbol": symbol, "interval": interval, "limit": limit},
                     proxies, retries, timeout=timeout)
    return _parse_kline_data(data)


def fetch_klines_many(symbol: str, interval: str, total_candles: int,
                      base_url: str = DEFAULT_BASE_URL,
                      proxies: dict = None,
                      retries: int = 3) -> pd.DataFrame:
    params = {"symbol": symbol, "interval": interval, "limit": 1000}
    frames = []
    end_ms = None

    while total_candles > 0:
        current = min(1000, total_candles)
        if end_ms is not None:
            params["endTime"] = end_ms
        else:
            params.pop("endTime", None)

        data = _http_get(base_url, params.copy(), proxies, retries)

        if not data:
            break

        df = _parse_kline_data(data)
        frames.append(df)
        # 反向分页：以上一批最早一根K线的开盘时间前移1ms为截止点
        end_ms = int(df["open_time"].iloc[0].value // 10**6) - 1
        total_candles -= current

    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values("open_time").reset_index(drop=True)
    return result


FAPI_API = "https://fapi.binance.com"
FAPI_PROXIES = {"http": "http://127.0.0.1:7897",
                "https": "http://127.0.0.1:7897"}


def fetch_futures_klines_many(symbol: str, interval: str, total_candles: int,
                              retries: int = 3) -> pd.DataFrame:
    """U本位永续K线(如中文symbol 龙虾USDT)，需代理。仅对币安期货上线品种有效。"""
    params = {"symbol": symbol, "interval": interval, "limit": 1000}
    frames = []
    end_ms = None

    while total_candles > 0:
        current = min(1000, total_candles)
        if end_ms is not None:
            params["endTime"] = end_ms
        else:
            params.pop("endTime", None)

        data = _http_get(FAPI_API + "/fapi/v1/klines", params.copy(),
                         FAPI_PROXIES, retries, timeout=20)

        if not data:
            break

        df = _parse_kline_data(data)
        frames.append(df)
        end_ms = int(df["open_time"].iloc[0].value // 10**6) - 1
        total_candles -= current

    if not frames:
        raise ConnectionError(f"futures klines unavailable: {symbol}")

    result = pd.concat(frames, ignore_index=True)
    return result.sort_values("open_time").reset_index(drop=True)
