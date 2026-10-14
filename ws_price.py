import time
import threading
import json

import websocket

REAL_URL = "wss://stream.binance.com:9443/ws/{}@miniTicker"
VISION_URL = "wss://data-stream.binance.vision/ws/{}@miniTicker"
RECONNECT_SLEEP = 5


class BinancePriceStream:
    def __init__(self, symbol: str, proxies: dict, on_update):
        self.symbol = symbol.lower()
        self.proxies = proxies or {}
        self.on_update = on_update
        self._stop = False
        self._thread = None

    def start(self):
        self._stop = False
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def _candidates(self):
        real = REAL_URL.format(self.symbol)
        cands = []
        if self.proxies:
            cands.append((real, True, "WS实时(代理)"))
        cands.append((real, False, "WS实时(直连)"))
        cands.append((VISION_URL.format(self.symbol), False, "WS镜像"))
        return cands

    def _run(self):
        while not self._stop:
            self._connect_once()
            for _ in range(RECONNECT_SLEEP):
                if self._stop:
                    return
                time.sleep(1)

    def _connect_once(self):
        for url, use_proxy, label in self._candidates():
            if self._stop:
                return
            try:
                if use_proxy and self.proxies:
                    conn = websocket.create_connection(
                        url, timeout=20,
                        http_proxy_host=self.proxies.get("http", "http://127.0.0.1:7897")
                                       .replace("http://", "").split(":")[0],
                        http_proxy_port=int(self.proxies.get("http", "http://127.0.0.1:7897")
                                            .replace("http://", "").split(":")[1]),
                    )
                else:
                    conn = websocket.create_connection(url, timeout=20)
            except Exception:
                continue
            try:
                self.on_update({"price": None, "ts": time.time(), "source": label,
                                "event": "connected"})
                self._drain(conn, label)
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    def _drain(self, conn, label):
        while not self._stop:
            try:
                raw = conn.recv()
            except Exception:
                return
            if not raw:
                return
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            c = msg.get("c")
            if c is None:
                continue
            self.on_update({
                "price": float(c),
                "ts": msg.get("E", time.time() * 1000) / 1000.0,
                "source": label,
                "event": "tick",
            })


def start_price_stream(symbol: str, proxies: dict, on_update):
    st = BinancePriceStream(symbol, proxies, on_update)
    st.start()
    return st