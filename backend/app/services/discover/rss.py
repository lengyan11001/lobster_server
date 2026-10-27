"""RSS / Atom 解析与抓取（纯标准库解析，无第三方依赖）。"""
from __future__ import annotations

import asyncio
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _text(node: Optional[ET.Element]) -> str:
    if node is None or node.text is None:
        return ""
    return str(node.text).strip()


def _parse_date(raw: str) -> Optional[float]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).timestamp()
    except Exception:  # noqa: BLE001
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).timestamp()
        except Exception:  # noqa: BLE001
            continue
    return None


def parse_feed(xml_text: str, *, keyword: str = "", since_days: Optional[int] = None,
               limit: int = 20) -> List[Dict[str, Any]]:
    """解析 RSS 2.0 / Atom，返回 [{title, url, summary, published_at}]。"""
    text = (xml_text or "").strip()
    if not text:
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    items: List[Dict[str, Any]] = []
    now = time.time()
    cutoff = now - since_days * 86400 if since_days else None
    words = [w.strip().lower() for w in str(keyword or "").split(",") if w.strip()]

    rss_items = root.findall(".//item")
    atom_items = root.findall(".//atom:entry", _NS)
    for node in rss_items or atom_items:
        is_atom = node.tag.endswith("entry")
        title = _text(node.find("title") if not is_atom else node.find("atom:title", _NS))
        if is_atom:
            link_node = node.find("atom:link", _NS)
            url = (link_node.get("href") if link_node is not None else "") or ""
            summary = _text(node.find("atom:summary", _NS)) or _text(node.find("atom:content", _NS))
            published = _text(node.find("atom:updated", _NS)) or _text(node.find("atom:published", _NS))
        else:
            url = _text(node.find("link"))
            summary = _text(node.find("description"))
            published = _text(node.find("pubDate")) or _text(node.find("dc:date", _NS))
        published_at = _parse_date(published)
        if cutoff and published_at and published_at < cutoff:
            continue
        hay = f"{title} {summary}".lower()
        if words and not any(w in hay for w in words):
            continue
        items.append({
            "title": title,
            "url": url,
            "summary": summary[:400],
            "published_at": published_at,
        })
        if len(items) >= max(1, limit):
            break
    return items


async def fetch(urls: List[str], *, keyword: str = "", since_days: Optional[int] = None,
                limit: int = 20, timeout: float = 12.0) -> Dict[str, Any]:
    """并发抓取多个 RSS 源；单源失败不影响其它源。"""
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=True,
                                 headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml"}) as client:
        async def one(url: str) -> None:
            try:
                resp = await client.get(url)
                if resp.status_code >= 400:
                    errors.append({"url": url, "error": f"HTTP {resp.status_code}"})
                    return
                for item in parse_feed(resp.text, keyword=keyword, since_days=since_days, limit=limit):
                    item["feed"] = url
                    results.append(item)
            except Exception as exc:  # noqa: BLE001
                errors.append({"url": url, "error": type(exc).__name__})

        await asyncio.gather(*(one(u) for u in urls))
    results.sort(key=lambda x: x.get("published_at") or 0, reverse=True)
    return {"items": results, "errors": errors}


def selftest() -> List[str]:
    sample_rss = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>AI 大模型发布</title><link>https://e/1</link>
    <description>新模型</description><pubDate>Mon, 26 Oct 2026 10:00:00 GMT</pubDate></item>
    <item><title>无关新闻</title><link>https://e/2</link><description>x</description></item>
    </channel></rss>"""
    problems: List[str] = []
    items = parse_feed(sample_rss, keyword="AI")
    if len(items) != 1:
        problems.append(f"rss keyword filter expected 1 got {len(items)}")
    items_all = parse_feed(sample_rss)
    if len(items_all) != 2:
        problems.append(f"rss parse expected 2 got {len(items_all)}")
    atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom 条目</title>
    <link href="https://e/3"/><summary>s</summary><updated>2026-10-26T10:00:00Z</updated></entry></feed>"""
    if len(parse_feed(atom)) != 1:
        problems.append("atom parse failed")
    return problems
