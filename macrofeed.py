import os
import time
import json
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import requests

from config import PROXIES

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macrofeed_cache.json")
TTL = 600

FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
]

TAGS = [
    ("FOMC/加息", ["fomc", "fed", "powell", "warsh", "rate hike", "rates", "interest rate",
                   "美联储", "加息", "利率"], "#f6465d"),
    ("通胀", ["cpi", "inflation", "ppi", "price index", "通胀", "cpi数据", "物价"], "#f0b90b"),
    ("ETF/资金流", ["etf", "inflow", "outflow", "ibit", "spot etf", "资金", "etf流入"], "#0ecb81"),
    ("监管/法案", ["clarity act", "sec", "cftc", "senate", "regulation", "coinbase",
                    "监管", "法案", "众议院", "参议院"], "#9b8cff"),
    ("地缘/油价", ["iran", "oil", "brent", "war", "geopolitical", "中东", "伊朗", "石油", "战争"], "#ff9f43"),
    ("爆仓/波动", ["liquidation", "short squeeze", "liquidate", "爆仓", "清算", "轧空"], "#e75ff7"),
]

SANE_TITLE_RE = re.compile(r"\s+")

FOMC_2026 = [
    ("2026-01-28", "FOMC 议息决议"),
    ("2026-03-18", "FOMC 议息决议"),
    ("2026-04-29", "FOMC 议息决议"),
    ("2026-06-17", "FOMC 议息决议"),
    ("2026-07-29", "FOMC 议息决议"),
    ("2026-09-16", "FOMC 议息决议"),
    ("2026-10-28", "FOMC 议息决议"),
    ("2026-12-09", "FOMC 议息决议"),
]
MONTHLY = ["CPI", "PPI", "非农"]

UTC8 = timezone(timedelta(hours=8))


def _tag_item(title):
    low = title.lower().replace("-", " ").replace("\u2013", " ").replace("\u2014", " ")
    hits = []
    for name, words, color in TAGS:
        for w in words:
            if w in low:
                hits.append((name, color))
                break
        if len(hits) >= 2:
            break
    return hits or [("行情", "#7b8ca3")]


def _clean(title):
    return SANE_TITLE_RE.sub(" ", title or "").strip()


def _fetch_feed(source, url):
    items = []
    proxies = None
    try:
        proxies = dict(PROXIES or {})
    except Exception:
        proxies = None
    last_exc = None
    for px in (proxies, None):
        try:
            r = requests.get(url, timeout=12, proxies=px)
            if r.status_code != 200:
                raise ValueError(f"status {r.status_code} via {px and 'proxy' or 'direct'}")
            break
        except Exception as e:
            last_exc = e
            continue
    else:
        raise last_exc or RuntimeError("all attempts failed")
    root = ET.fromstring(r.content)
    for item in root.iter("item"):
        title = _clean(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        pub = item.findtext("pubDate")
        ts = None
        if pub:
            try:
                ts = parsedate_to_datetime(pub).replace(tzinfo=None)
            except Exception:
                ts = None
        if title:
            items.append({"source": source, "title": title, "url": link, "ts": ts})
        if len(items) >= 20:
            break
    return items


def poll():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    out = []
    for source, url in FEEDS:
        try:
            out.extend(_fetch_feed(source, url))
        except Exception as e:
            out.append({"source": source, "title": f"(订阅源暂不可用: {str(e)[:80]})",
                        "url": "", "ts": None})
    out.sort(key=lambda x: (x["ts"] or now).timestamp(), reverse=True)
    seen = set()
    items = []
    for it in out:
        k = it["title"]
        if k in seen:
            continue
        seen.add(k)
        it["tags"] = _tag_item(it["title"])
        items.append(it)
        if len(items) >= 30:
            break
    pack = {"ts": time.time(), "items": items}
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(pack, f, ensure_ascii=False)
    except Exception:
        pass
    return pack


def load_cached():
    try:
        with open(CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def upcoming_events():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = now.date()
    evs = []
    for d, name in FOMC_2026:
        date = datetime.strptime(d, "%Y-%m-%d").date()
        if date >= today:
            evs.append({"name": name, "date": d, "days": (date - today).days})
    # 月度数据: 按"下个自然月"粗略占位(官方日期以BLS公告为准)
    ns = [__import__("calendar").monthrange(now.year, m)[1]
          for m in (now.month, (now.month % 12) + 1)]
    for m in (now.month, (now.month % 12) + 1):
        y = now.year if m >= now.month else now.year + 1
        last = __import__("calendar").monthrange(y, m)[1]
        cand = datetime(y, m, last).date() if m == now.month else None
        if cand and cand < today:
            continue
    return evs[:4]


def ago(ts):
    if not ts:
        return "?"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    secs = int((now - ts).total_seconds())
    if secs < 3600:
        return f"{max(0, secs // 60)}分钟前"
    if secs < 86400:
        return f"{secs // 3600}小时前"
    return f"{secs // 86400}天前"


def refresh_macro(state):
    tick = state["counter"]
    cached = load_cached()
    fresh = cached and time.time() - cached.get("ts", 0) < TTL
    if fresh:
        if state.get("macro") is None:
            state["macro"] = cached
        return
    if tick == 0:
        return
    if tick % 600 != 0 and state.get("macro") is not None:
        return
    try:
        state["macro"] = poll()
        state["macro_err"] = None
    except Exception as e:
        state["macro_err"] = str(e)[:160]


if __name__ == "__main__":
    p = poll()
    print("items:", len(p["items"]))
    for it in p["items"][:6]:
        print("-", it["source"], "|", [t[0] for t in it["tags"]], "|", it["title"][:70])
    print("next events:", upcoming_events())