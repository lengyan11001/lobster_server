"""服务端网页正文提取：适配器 → 直连 → Jina → 无头浏览器，四级兜底。

供 AI 调度助手的 browser_extract_page 使用：以前这个动作被标记成
client_workflow，服务端直接拒绝；现在改为服务端可执行。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger(__name__)

MAX_TEXT = 30000
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def _clean(text: str, limit: int = MAX_TEXT) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def _x_status(url: str) -> Optional[str]:
    m = re.search(r"(?:twitter\.com|x\.com)/([^/]+)/status/(\d+)", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _github_repo(url: str) -> Optional[str]:
    p = urlparse(url).path.strip("/").split("/")
    if len(p) >= 2 and re.fullmatch(r"[A-Za-z0-9_.-]+", p[0]) and re.fullmatch(r"[A-Za-z0-9_.-]+", p[1]):
        return f"{p[0]}/{p[1]}"
    return None


async def _fetch_json(client: httpx.AsyncClient, url: str) -> Optional[Dict[str, Any]]:
    for attempt in range(2):
        try:
            r = await client.get(url, headers={"User-Agent": UA, "Accept": "application/json"})
            if r.status_code >= 400:
                logger.info("[web-extract] json http %s %s", r.status_code, url)
                if attempt == 0:
                    await asyncio.sleep(0.6)
                    continue
                return None
            return r.json()
        except Exception as exc:  # noqa: BLE001
            logger.info("[web-extract] json fail %s: %s", url, exc)
            if attempt == 0:
                await asyncio.sleep(0.6)
                continue
            return None
    return None


async def _via_github(client: httpx.AsyncClient, url: str, max_chars: int) -> Optional[Dict[str, Any]]:
    repo = _github_repo(url)
    if not repo:
        return None
    data = await _fetch_json(client, f"https://api.github.com/repos/{repo}")
    if not data:
        return None
    readme = ""
    try:
        r = await client.get(f"https://api.github.com/repos/{repo}/readme",
                             headers={"User-Agent": UA, "Accept": "application/vnd.github.raw"})
        if r.status_code < 400:
            readme = r.text
    except Exception:  # noqa: BLE001
        pass
    text = "\n".join(x for x in [
        f"{data.get('full_name')} — {data.get('description') or ''}",
        f"语言：{data.get('language')}｜Star：{data.get('stargazers_count')}｜Fork：{data.get('forks_count')}",
        f"协议：{(data.get('license') or {}).get('spdx_id') or '未标注'}",
        readme,
    ] if x)
    return {"ok": True, "url": url, "title": data.get("full_name") or repo, "text": _clean(text, max_chars),
            "links": [{"text": "仓库主页", "href": f"https://github.com/{repo}"}],
            "images": [], "method": "github-api", "notes": []}


async def _via_fxtwitter(client: httpx.AsyncClient, url: str, max_chars: int) -> Optional[Dict[str, Any]]:
    path = _x_status(url)
    if not path:
        return None
    data = await _fetch_json(client, f"https://api.fxtwitter.com/{path}")
    tweet = (data or {}).get("tweet") or {}
    if not tweet:
        return None
    author = tweet.get("author") or {}
    media = []
    for item in (tweet.get("media") or {}).get("all") or []:
        if item.get("url"):
            media.append(item["url"])
    lines = [
        f"{author.get('name') or ''}（@{author.get('screen_name') or ''}）",
        tweet.get("text") or "",
        f"发布：{tweet.get('created_at') or ''}｜浏览：{tweet.get('views') or 0}"
        f"｜点赞：{tweet.get('likes') or 0}｜转发：{tweet.get('retweets') or 0}",
    ]
    return {"ok": True, "url": url, "title": (tweet.get("text") or "")[:80], "text": _clean("\n".join(lines), max_chars),
            "links": [], "images": media, "method": "fxtwitter", "notes": []}


async def _via_syndication(client: httpx.AsyncClient, url: str, max_chars: int) -> Optional[Dict[str, Any]]:
    m = re.search(r"/status/(\d+)", url)
    if not m:
        return None
    data = await _fetch_json(client, f"https://cdn.syndication.twimg.com/tweet-result?id={m.group(1)}&token=a")
    if not isinstance(data, dict) or not data.get("text"):
        return None
    user = data.get("user") or {}
    media = []
    for item in data.get("mediaDetails") or []:
        if item.get("media_url_https"):
            media.append(item["media_url_https"])
    lines = [
        f"{user.get('name') or ''}（@{user.get('screen_name') or ''}）",
        data.get("text") or "",
        f"发布：{data.get('created_at') or ''}｜点赞：{data.get('favorite_count') or 0}"
        f"｜评论：{data.get('conversation_count') or 0}",
    ]
    return {"ok": True, "url": url, "title": (data.get("text") or "")[:80], "text": _clean("\n".join(lines), max_chars),
            "links": [], "images": media, "method": "twitter-syndication", "notes": []}


async def _via_direct(url: str, max_chars: int) -> Optional[Dict[str, Any]]:
    from .web_page_analyzer import analyze_page, WebFetchError

    def run() -> Optional[Dict[str, Any]]:
        try:
            data = analyze_page(url, max_chars=max_chars)
        except WebFetchError as exc:
            logger.info("[web-extract] direct fail %s: %s", url, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.info("[web-extract] direct error %s: %s", url, exc)
            return None
        text = data.get("text") or ""
        if len(text) < 80:
            return None
        return {"ok": True, "url": url, "title": data.get("title") or "", "text": _clean(text, max_chars),
                "links": data.get("links") or [], "images": data.get("images") or [],
                "method": "direct", "notes": data.get("notes") or []}

    return await asyncio.to_thread(run)


async def _via_jina(client: httpx.AsyncClient, url: str, max_chars: int) -> Optional[Dict[str, Any]]:
    try:
        r = await client.get("https://r.jina.ai/" + quote(url, safe=""), timeout=45.0,
                             headers={"User-Agent": UA, "Accept": "text/plain"})
        if r.status_code >= 400:
            return None
        text = r.text
        if len(text) < 80:
            return None
        title = ""
        m = re.search(r"^Title:\s*(.+)$", text, re.M)
        if m:
            title = m.group(1).strip()
        return {"ok": True, "url": url, "title": title, "text": _clean(text, max_chars),
                "links": [], "images": [], "method": "jina-reader", "notes": []}
    except Exception as exc:  # noqa: BLE001
        logger.info("[web-extract] jina fail %s: %s", url, exc)
        return None


async def _via_playwright(url: str, *, wait_ms: int, scroll_rounds: int, max_chars: int) -> Optional[Dict[str, Any]]:
    def run() -> Optional[Dict[str, Any]]:
        try:
            from playwright.sync_api import sync_playwright
        except Exception:  # noqa: BLE001
            return None
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
                page = browser.new_page(user_agent=UA, viewport={"width": 1366, "height": 900})
                page.goto(url, wait_until="networkidle", timeout=max(15000, wait_ms + 15000))
                for _ in range(max(1, scroll_rounds)):
                    page.mouse.wheel(0, 2400)
                    page.wait_for_timeout(600)
                title = page.title()
                text = page.inner_text("body")
                links: List[Dict[str, str]] = []
                for a in page.query_selector_all("a[href]")[:200]:
                    href = a.get_attribute("href") or ""
                    label = (a.inner_text() or "").strip()
                    if href:
                        links.append({"text": label[:60], "href": href})
                browser.close()
            if len(text) < 60:
                return None
            return {"ok": True, "url": url, "title": title, "text": _clean(text, max_chars),
                    "links": links, "images": [], "method": "playwright", "notes": []}
        except Exception as exc:  # noqa: BLE001
            logger.info("[web-extract] playwright fail %s: %s", url, exc)
            return None

    return await asyncio.to_thread(run)


async def extract_page_server(params: Dict[str, Any]) -> Dict[str, Any]:
    """按 适配器 → 直连 → Jina → Playwright 的顺序抓取。"""
    url = str(params.get("url") or "").strip()
    max_chars = min(int(params.get("max_chars") or MAX_TEXT), MAX_TEXT)
    wait_ms = int(params.get("wait_ms") or 3000)
    scroll_rounds = int(params.get("scroll_rounds") or 2)
    notes: List[str] = []

    if not url.startswith("http"):
        return {"ok": False, "url": url, "error": "网址不合法", "notes": notes}

    timeout = httpx.Timeout(30.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=True) as client:
        result = await _via_fxtwitter(client, url, max_chars)
        if result:
            return result
        result = await _via_syndication(client, url, max_chars)
        if result:
            return result
        result = await _via_github(client, url, max_chars)
        if result:
            return result
        result = await _via_direct(url, max_chars)
        if result:
            return result
        notes.append("直连失败")
        result = await _via_jina(client, url, max_chars)
        if result:
            result["notes"] = notes
            return result
        notes.append("Jina 失败")

    result = await _via_playwright(url, wait_ms=wait_ms, scroll_rounds=scroll_rounds, max_chars=max_chars)
    if result:
        result["notes"] = notes
        return result
    notes.append("无头浏览器失败")
    return {"ok": False, "url": url, "error": "所有抓取方式都失败", "notes": notes}


def format_extract_reply(result: Dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"抓取失败：{result.get('error')}（url={result.get('url')}）"
    text = result.get("text") or ""
    head = f"标题：{result.get('title') or '(无)'}\n方式：{result.get('method')}\n链接：{result.get('url')}"
    if result.get("images"):
        head += "\n配图：" + " ".join(result["images"][:6])
    return head + "\n\n" + text
