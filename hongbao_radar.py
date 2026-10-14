# -*- coding: utf-8 -*-
"""红包雷达: 轮询币安官方公告, 识别红包/空投类公告, 供Dashboard面板展示。
广场个人帖被反爬(202/403/404)无法抓取 —— 本模块只覆盖官方公告渠道。
"""
import os
import re
import time
import json

import requests

from config import PROXIES

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hongbao_cache.json")
BAPI = "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "Chrome/126.0 Safari/537.36"}
CATALOGS = (48, 51)          # 48=全部公告 51=活动类
PAGE_SIZE = 50
STRONG = ("红包", "red packet", "红 包", "空投", "airdrop", "赠金", "体验金")

_state = {"items": [], "ts": 0, "last_items": [], "err": None, "inited": False}


def _fetch_page():
    out = []
    for cid in CATALOGS:
        try:
            r = requests.get(BAPI,
                             params={"catalogId": cid, "pageNo": 1, "pageSize": PAGE_SIZE},
                             headers=UA, proxies=PROXIES or None, timeout=25)
            j = r.json()
            for a in ((j.get("data") or {}).get("articles", []) or []):
                out.append(a)
        except Exception as e:
            _state["err"] = str(e)[:80]
    return out


def _load_cache():
    try:
        with open(CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seen": {}, "items": []}


def _save_cache(c):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
    except Exception:
        pass


def _classify(title):
    t = title.lower()
    if any(k in t for k in STRONG):
        return "红包/空投"
    return None


def refresh(state, force=False):
    now = time.time()
    if not force and _state["inited"] and now - _state["ts"] < 600:
        return _state
    c = _load_cache()
    seen = c.get("seen", {})
    rawarts = _fetch_page()
    fresh = []
    seen_ids = set()
    for a in rawarts:
        aid = a.get("id")
        title = (a.get("title") or "").strip()
        tag = _classify(title)
        if not tag:
            continue
        seen_ids.add(aid)
        ts = a.get("releaseDate") or a.get("publishTime") or ""
        if isinstance(ts, (int, float)):
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
        fresh.append({
            "id": aid,
            "title": title,
            "tag": tag,
            "ts": str(ts),
            "url": f"https://www.binance.com/en/support/announcement/{a.get('code') or aid}",
        })
    new_items = [x for x in fresh if x["id"] not in seen]
    for x in fresh:
        seen[x["id"]] = x["ts"]
    if fresh:
        merged = []
        added = set()
        for x in (fresh + c.get("items", [])):
            if x["id"] not in added:
                added.add(x["id"])
                merged.append(x)
        all_items = merged[:30]
        c["seen"] = seen
        c["items"] = all_items
        _save_cache(c)
        _state["items"] = all_items
    else:
        _state["items"] = c.get("items", [])
    _state["new"] = [x["title"] for x in new_items][:5]
    _state["ts"] = now
    _state["inited"] = True
    state["hongbao"] = _state
    return _state


if __name__ == "__main__":
    s = {"hongbao": None}
    st = refresh(s, force=True)
    print("err:", st["err"], "items:", len(st["items"]), "new:", st.get("new"))
    for it in st["items"][:12]:
        print("-", it["ts"], it["tag"], "|", it["title"][:58])