"""行业资讯：按赛道选源、并发抓取、按时间窗过滤，30 分钟缓存。"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx

from . import cache
from .rss import parse_feed

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

SOURCES: Dict[str, Dict[str, str]] = {
    "woshipm": {"name": "人人都是产品经理", "url": "https://www.woshipm.com/feed", "tag": "自媒体·运营"},
    "huxiu": {"name": "虎嗅", "url": "https://www.huxiu.com/rss/0.xml", "tag": "商业·科技"},
    "tmtpost": {"name": "钛媒体", "url": "https://www.tmtpost.com/feed", "tag": "科技·商业"},
    "geekpark": {"name": "极客公园", "url": "http://www.geekpark.net/rss", "tag": "科技·数码"},
    "sspai": {"name": "少数派", "url": "https://sspai.com/feed", "tag": "数码·效率"},
    "infoq_cn": {"name": "InfoQ 中文", "url": "https://www.infoq.cn/feed.xml", "tag": "技术"},
    "aihot": {"name": "AIHOT", "url": "https://aihot.virxact.com/rss", "tag": "AI"},
    "thepaper": {"name": "澎湃新闻", "url": "https://feedx.net/rss/thepaper.xml", "tag": "时事"},
}

TRACKS: Dict[str, List[str]] = {
    "content": ["woshipm", "huxiu", "aihot"],
    "tech": ["sspai", "geekpark", "infoq_cn", "36kr"],
    "business": ["wallstreetcn", "huxiu", "tmtpost", "36kr"],
    "ai": ["aihot", "infoq_cn"],
    "news": ["thepaper", "huxiu"],
}
CACHE_TTL = 1800


async def fetch(*, track: str = "", keywords: Optional[List[str]] = None,
                since_hours: int = 48, limit_per_source: int = 5,
                sources: Optional[List[str]] = None, use_cache: bool = True) -> Dict[str, Any]:
    keys = [k for k in (sources or TRACKS.get(track or "", []) or list(SOURCES)[:3]) if k in SOURCES]
    if not keys:
        return {"items": [], "errors": [{"source": "*", "error": "no usable source"}], "sources": []}
    kw = ",".join(keywords or [])
    cache_key = f"news:{','.join(sorted(keys))}:{since_hours}:{limit_per_source}:{kw}"
    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            out = dict(cached)
            out["from_cache"] = True
            return out

    items: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, trust_env=True,
                                 headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml"}) as client:
        async def one(key: str) -> None:
            meta = SOURCES[key]
            try:
                resp = await client.get(meta["url"])
                if resp.status_code >= 400:
                    errors.append({"source": key, "error": f"HTTP {resp.status_code}"})
                    return
                parsed = parse_feed(resp.text, keyword=kw,
                                    since_days=max(1, since_hours // 24), limit=limit_per_source)
                for row in parsed:
                    row["source"] = meta["name"]
                    row["tag"] = meta["tag"]
                    items.append(row)
            except Exception as exc:  # noqa: BLE001
                errors.append({"source": key, "error": type(exc).__name__})

        await asyncio.gather(*(one(k) for k in keys))
    items.sort(key=lambda x: x.get("published_at") or 0, reverse=True)
    out = {"items": items[: limit_per_source * len(keys)], "errors": errors,
           "sources": [SOURCES[k]["name"] for k in keys]}
    if items and use_cache:
        cache.set(cache_key, dict(out), CACHE_TTL)
    return out


def format_markdown(data: Dict[str, Any], *, track: str = "") -> str:
    lines = ["# 资讯情报简报", f"赛道：{track or '通用'}｜源：{'、'.join(data.get('sources') or [])}", ""]
    if not data.get("items"):
        lines.append("这次没抓到可用资讯，换一批源或稍后重试。")
    for idx, item in enumerate(data.get("items") or [], 1):
        lines.append(f"### {idx}. {item['title']}")
        lines.append(f"- 来源：{item.get('source', '')}（{item.get('tag', '')}）")
        lines.append(f"- 链接：{item.get('url', '')}")
        if item.get("summary"):
            lines.append(f"- 摘要：{item['summary']}")
        lines.append("")
    for err in data.get("errors") or []:
        lines.append(f"> {err['source']}：{err['error']}")
    return "\n".join(lines)


def selftest() -> List[str]:
    problems: List[str] = []
    if not SOURCES:
        problems.append("sources table empty")
    for key in ("content", "tech", "ai"):
        if not [k for k in TRACKS.get(key, []) if k in SOURCES]:
            problems.append(f"track {key} has no usable source")
    return problems
