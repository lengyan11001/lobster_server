"""免费搜索联想词：Bing osjson 主源 → 百度 su 备源，1 小时缓存。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

import httpx

from . import cache

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
CACHE_TTL = 3600


def parse_bing(text: str) -> List[str]:
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        return []
    if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
        return [str(x).strip() for x in data[1] if str(x).strip()]
    return []


def parse_baidu(text: str) -> List[str]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for item in data.get("g") or []:
        q = str((item or {}).get("q") or "").strip()
        if q:
            out.append(q)
    return out


async def fetch(keyword: str, *, use_cache: bool = True) -> Dict[str, Any]:
    kw = str(keyword or "").strip()
    if not kw:
        return {"keyword": kw, "items": [], "source": "", "error": "empty keyword"}
    key = f"suggest:{kw}"
    if use_cache:
        cached = cache.get(key)
        if cached:
            out = dict(cached)
            out["from_cache"] = True
            return out

    result: Dict[str, Any] = {"keyword": kw, "items": [], "source": "", "error": ""}
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, trust_env=True,
                                 headers={"User-Agent": UA}) as client:
        try:
            resp = await client.get("https://api.bing.com/osjson.aspx", params={"query": kw})
            items = parse_bing(resp.text) if resp.status_code == 200 else []
            if items:
                result.update({"items": items, "source": "bing"})
        except Exception as exc:  # noqa: BLE001
            result["error"] = type(exc).__name__
        if not result["items"]:
            try:
                resp = await client.get("https://suggestion.baidu.com/su",
                                        params={"wd": kw, "cid": "0", "json": "1"})
                items = parse_baidu(resp.text) if resp.status_code == 200 else []
                if items:
                    result.update({"items": items, "source": "baidu", "error": ""})
            except Exception as exc:  # noqa: BLE001
                result["error"] = type(exc).__name__
    if result["items"] and use_cache:
        cache.set(key, dict(result), CACHE_TTL)
    return result


def selftest() -> List[str]:
    problems: List[str] = []
    bing = parse_bing('["家居收纳",["家居收纳","家居收纳盒"]]')
    if bing != ["家居收纳", "家居收纳盒"]:
        problems.append("bing parse failed")
    baidu = parse_baidu('window.baidu.sug({"q":"x","g":[{"q":"洗衣液"},{"q":"洗衣液推荐"}]})')
    if baidu != ["洗衣液", "洗衣液推荐"]:
        problems.append("baidu parse failed")
    return problems
