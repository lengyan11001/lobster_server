"""多平台热搜：60s v2 主源 → xxapi 备源，带 5 分钟缓存。"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import cache

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# 平台 -> (60s v2 路径, xxapi 路径)
PLATFORMS: Dict[str, Tuple[str, str]] = {
    "weibo": ("/v2/weibo", "/api/weibohot"),
    "douyin": ("/v2/douyin", "/api/douyinhot"),
    "zhihu": ("/v2/zhihu", ""),
    "toutiao": ("/v2/toutiao", ""),
    "xiaohongshu": ("/v2/rednote", ""),
    "baidu": ("/v2/baidu/hot", "/api/baiduhot"),
    "bilibili": ("/v2/bili", "/api/bilibilihot"),
}
PRIMARY = "https://60s.viki.moe"
FALLBACK = "https://v2.xxapi.cn"
CACHE_TTL = 300


def extract_items(payload: Any) -> List[Dict[str, Any]]:
    """兼容 data 为数组或 data.data 嵌套两种结构。"""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict):
        data = data.get("data")
    if not isinstance(data, list):
        return []
    items: List[Dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("name") or "").strip()
        if not title:
            continue
        hot = row.get("hot") or row.get("hotValue") or row.get("num") or 0
        try:
            hot = int(hot)
        except Exception:  # noqa: BLE001
            hot = 0
        items.append({"title": title, "hot": hot, "url": str(row.get("url") or "")})
    items.sort(key=lambda x: x["hot"], reverse=True)
    return items


async def _get_json(client: httpx.AsyncClient, url: str) -> Optional[Dict[str, Any]]:
    try:
        resp = await client.get(url)
        if resp.status_code >= 400:
            return None
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


async def fetch(platforms: Optional[List[str]] = None, limit: int = 10,
                *, use_cache: bool = True) -> Dict[str, Any]:
    names = [p for p in (platforms or ["weibo", "douyin"]) if p in PLATFORMS]
    cache_key = "hotlist:" + ",".join(sorted(names)) + f":{limit}"
    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            cached = dict(cached)
            cached["from_cache"] = True
            return cached

    result: Dict[str, Any] = {"items": {}, "source": {}, "errors": []}
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, trust_env=True,
                                 headers={"User-Agent": UA, "Accept": "application/json"}) as client:
        async def one(name: str) -> None:
            primary, fallback = PLATFORMS[name]
            payload = await _get_json(client, PRIMARY + primary)
            items = extract_items(payload) if payload else []
            source = "60s"
            if not items and fallback:
                payload = await _get_json(client, FALLBACK + fallback)
                items = extract_items(payload) if payload else []
                source = "xxapi"
            if not items:
                result["errors"].append({"platform": name, "error": "both sources empty"})
                return
            result["items"][name] = items[: max(1, limit)]
            result["source"][name] = source

        await asyncio.gather(*(one(n) for n in names))
    if result["items"] and use_cache:
        cache.set(cache_key, dict(result), CACHE_TTL)
    return result


def format_markdown(data: Dict[str, Any]) -> str:
    lines: List[str] = ["# 热点速报", ""]
    for name, items in (data.get("items") or {}).items():
        src = (data.get("source") or {}).get(name, "")
        lines.append(f"## {name}（来源：{src}）")
        lines.append("| # | 话题 | 热度 |")
        lines.append("|---|---|---|")
        for idx, item in enumerate(items, 1):
            lines.append(f"| {idx} | {item['title']} | {item['hot']} |")
        lines.append("")
    for err in data.get("errors") or []:
        lines.append(f"> {err['platform']}：{err['error']}")
    if not data.get("items"):
        lines.append("所有平台都没取到数据，请稍后重试或直接提供热搜截图。")
    return "\n".join(lines)


def selftest() -> List[str]:
    problems: List[str] = []
    a = extract_items({"code": 200, "data": [{"title": "A", "hot": 10}, {"title": "B", "hot": 20}]})
    if [x["title"] for x in a] != ["B", "A"]:
        problems.append("flat data not sorted by hot")
    b = extract_items({"code": 200, "data": {"data": [{"title": "C", "hot": 5}]}})
    if len(b) != 1 or b[0]["title"] != "C":
        problems.append("nested data.data not parsed")
    if extract_items({"code": 200, "data": None}):
        problems.append("empty data should return []")
    return problems
