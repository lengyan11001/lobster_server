"""UGC / 品牌提及：免费搜索页抓取（Bing），结果有限，明确标注。"""
from __future__ import annotations

import re
from typing import Any, Dict, List

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

_RESULT_RE = re.compile(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
PLATFORM_SITES = {
    "xiaohongshu": "site:xiaohongshu.com",
    "douyin": "site:douyin.com",
    "weibo": "site:weibo.com",
    "zhihu": "site:zhihu.com",
    "bilibili": "site:bilibili.com",
}


def parse_results(html: str, limit: int = 10) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for href, title in _RESULT_RE.findall(html or ""):
        clean = _TAG_RE.sub("", title).strip()
        if not href.startswith("http"):
            continue
        items.append({"title": clean, "url": href})
        if len(items) >= max(1, limit):
            break
    return items


async def search(brand: str, *, platforms: List[str] | None = None, limit: int = 10) -> Dict[str, Any]:
    brand = str(brand or "").strip()
    if not brand:
        return {"brand": brand, "items": [], "errors": [{"error": "missing brand"}], "limits": []}
    sites = [PLATFORM_SITES[p] for p in (platforms or []) if p in PLATFORM_SITES]
    query = brand if not sites else f"{brand} ({' OR '.join(sites)})"
    items: List[Dict[str, str]] = []
    errors: List[Dict[str, str]] = []
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, trust_env=True,
                                 headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}) as client:
        try:
            resp = await client.get("https://www.bing.com/search", params={"q": query, "setlang": "zh-Hans"})
            if resp.status_code >= 400:
                errors.append({"source": "bing", "error": f"HTTP {resp.status_code}"})
            else:
                items = parse_results(resp.text, limit=limit)
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": "bing", "error": type(exc).__name__})
    return {"brand": brand, "items": items, "errors": errors,
            "limits": ["免费搜索只能拿到公开索引结果，评论/互动明细需人工补"]}


def format_markdown(data: Dict[str, Any]) -> str:
    lines = [f"# {data.get('brand', '')} 的公开提及", ""]
    if not data.get("items"):
        lines.append("没有搜到公开提及；可能品牌名太泛或被索引限制。")
    for idx, item in enumerate(data.get("items") or [], 1):
        lines.append(f"{idx}. [{item['title'][:60]}]({item['url']})")
    if data.get("items"):
        lines.append("")
        lines.append(f"> 共 {len(data['items'])} 条；" + "；".join(data.get("limits") or []))
    for err in data.get("errors") or []:
        lines.append(f"> {err['source']}：{err['error']}")
    return "\n".join(lines)


def selftest() -> List[str]:
    problems: List[str] = []
    html = '<h2><a href="https://x/1">标题一</a></h2><h2><a href="https://x/2">标题二</a></h2>'
    items = parse_results(html, limit=5)
    if len(items) != 2 or items[0]["title"] != "标题一":
        problems.append("bing result parse failed")
    return problems
