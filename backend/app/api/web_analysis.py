"""Web page / site analysis API (server-side, read-only)."""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..models import User
from ..services.web_page_analyzer import (
    DEFAULT_TIMEOUT,
    MAX_PAGE_TEXT,
    WebFetchError,
    analyze_page,
    crawl_site,
)
from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

_RATE_LIMIT: Dict[str, Deque[float]] = defaultdict(deque)
_RATE_WINDOW_SECONDS = 3600
_RATE_LIMITS = {"analyze": 30, "site": 10}
_FALLBACK_CODES = {"blocked_host", "timeout", "unsupported_type", "fetch_error", "http_error", "dns_error"}


def _check_rate_limit(user_id: int, bucket: str) -> None:
    key = f"{user_id}:{bucket}"
    now = time.time()
    window = _RATE_LIMIT[key]
    while window and now - window[0] > _RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= _RATE_LIMITS[bucket]:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    window.append(now)


class AnalyzePageBody(BaseModel):
    url: str = Field(..., min_length=4, max_length=2048)
    max_chars: int = Field(MAX_PAGE_TEXT, ge=1000, le=MAX_PAGE_TEXT)
    offset: int = Field(0, ge=0, le=200000)
    timeout_seconds: float = Field(DEFAULT_TIMEOUT, ge=3, le=60)


class CrawlSiteBody(BaseModel):
    seed_urls: List[str] = Field(..., min_length=1, max_length=5)
    max_depth: int = Field(2, ge=0, le=4)
    max_pages: int = Field(25, ge=1, le=100)
    max_pages_per_site: int = Field(25, ge=1, le=100)
    allow_subdomains: bool = True
    allow_domains: List[str] = Field(default_factory=list, max_length=10)
    include_patterns: List[str] = Field(default_factory=list, max_length=20)
    exclude_patterns: List[str] = Field(default_factory=list, max_length=20)
    time_budget_seconds: int = Field(180, ge=20, le=300)
    per_host_delay_ms: int = Field(500, ge=0, le=3000)
    respect_robots: bool = True
    use_sitemap: bool = True


ROLE_MAILBOXES = (
    "sales", "info", "contact", "enquiry", "enquiries", "inquiry", "business",
    "service", "support", "office", "hello", "admin", "marketing",
)


def _page_digest(data: Dict[str, Any]) -> str:
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    text = str(data.get("text") or "").strip()
    links = data.get("links") if isinstance(data.get("links"), list) else []
    lines = [
        f"标题：{title or '(无)'}",
        f"站点：{data.get('site', '')}",
    ]
    if description:
        lines.append(f"描述：{description}")
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    start = 0
    for index, line in enumerate(raw_lines):
        if line.startswith("Notice: This page displays a fallback"):
            continue
        if line.startswith(("Skip to content", ">>>", "#")):
            continue
        if len(line) >= 60:
            start = max(0, index - 1)
            break
    excerpt = "\n".join(raw_lines[start:])[:3000]
    facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
    if facts.get("latest_version"):
        lines.append(f"最新版本：{facts['latest_version']}")
    dated = facts.get("dated_items") or []
    if dated:
        lines.append("关键日期：")
        for item in dated[:5]:
            lines.append(f"- {item.get('date', '')} {item.get('title', '')}")
    nav = facts.get("nav") or []
    if nav:
        lines.append("主要栏目：")
        for item in nav[:10]:
            lines.append(f"- {item.get('text', '')} {item.get('url', '')}")
    contacts = data.get("contacts") if isinstance(data.get("contacts"), dict) else {}
    def _email_rank(value: str) -> int:
        # 通用规则：按“职能邮箱”排序（销售/咨询/客服优先），不针对任何具体站点。
        low = value.lower()
        for index, key in enumerate(ROLE_MAILBOXES):
            if low.startswith(key):
                return index
        return len(ROLE_MAILBOXES)

    emails = sorted([str(item) for item in (contacts.get("emails") or [])], key=_email_rank)[:6]
    phones = [str(item) for item in (contacts.get("phones") or [])][:6]
    if emails or phones:
        lines.append("联系方式：")
        for item in emails:
            lines.append(f"- 邮箱：{item}")
        for item in phones:
            lines.append(f"- 电话：{item}")
    if data.get("needs_browser"):
        lines.append("提示：正文提取过少，页面可能由前端脚本渲染；建议改用设备浏览器 browser_extract_page 再抓一次（用户要联系方式时同时传 click_texts）。")
    lines.append(f"正文字数：{data.get('word_count', 0)}（节选 {len(excerpt)} 字）")
    lines.append("")
    lines.append("正文节选：")
    lines.append(excerpt)
    if data.get("next_offset"):
        lines.append("")
        lines.append(f"（正文还有更多，next_offset={data.get('next_offset')}；可再次调用同一能力并传 offset 继续读取）")
    if links:
        lines.append("")
        lines.append("主要链接：")
        for link in links[:12]:
            label = str(link.get("text") or "").strip() or str(link.get("url") or "")
            lines.append(f"- {label[:60]} {link.get('url', '')}")
    return "\n".join(lines)


def _site_digest(data: Dict[str, Any]) -> str:
    lines: List[str] = []
    for site in data.get("sites", []) or []:
        lines.append(f"站点：{site.get('domain', '')}（{site.get('pages_crawled', 0)} 页）")
        for page in (site.get("pages") or [])[:10]:
            lines.append(f"- {page.get('title', '')[:80]} {page.get('url', '')}")
            excerpt = str(page.get("text") or "")[:160].replace("\n", " ")
            if excerpt:
                lines.append(f"  {excerpt}")
        contacts = site.get("contacts") or {}
        emails = contacts.get("emails") or []
        phones = contacts.get("phones") or []
        if emails or phones:
            lines.append(f"联系方式：{', '.join(emails[:3])} {', '.join(phones[:3])}".strip())
        lines.append("")
    merged = data.get("merged") or {}
    lines.append(f"合计：{merged.get('pages_crawled', 0)} 页")
    return "\n".join(lines)


@router.post("/api/web/analyze", summary="抓取并解析单个网页")
def web_analyze_page(body: AnalyzePageBody, current_user: User = Depends(get_current_user)):
    _check_rate_limit(int(current_user.id), "analyze")
    try:
        data = analyze_page(body.url, timeout=body.timeout_seconds, max_chars=body.max_chars, text_offset=body.offset)
    except WebFetchError as exc:
        payload: Dict[str, Any] = {
            "ok": False,
            "code": exc.code,
            "error": str(exc),
            "url": body.url,
        }
        if exc.code in _FALLBACK_CODES:
            payload["fallback"] = "online"
            payload["fallback_hint"] = "服务器抓取失败；如需登录或页面依赖浏览器渲染，请把任务下发给已登录设备用浏览器打开。"
        return payload
    if int(data.get("word_count", 0) or 0) < 60:
        data["fallback"] = "online"
        data["fallback_hint"] = "服务器只拿到很少内容（可能是年龄门或 JS 渲染），请改用设备浏览器打开。"
    data["digest"] = _page_digest(data)
    return {"ok": True, "data": data}


@router.post("/api/web/site", summary="多级抓取官网并汇总关键信息")
def web_crawl_site(body: CrawlSiteBody, current_user: User = Depends(get_current_user)):
    _check_rate_limit(int(current_user.id), "site")
    try:
        data = crawl_site(
            body.seed_urls,
            max_depth=body.max_depth,
            max_pages=body.max_pages,
            max_pages_per_site=body.max_pages_per_site,
            allow_subdomains=body.allow_subdomains,
            allow_domains=body.allow_domains,
            include_patterns=body.include_patterns,
            exclude_patterns=body.exclude_patterns,
            time_budget_seconds=body.time_budget_seconds,
            per_host_delay_ms=body.per_host_delay_ms,
            respect_robots=body.respect_robots,
            use_sitemap=body.use_sitemap,
        )
    except WebFetchError as exc:
        payload: Dict[str, Any] = {"ok": False, "code": exc.code, "error": str(exc)}
        if exc.code in _FALLBACK_CODES:
            payload["fallback"] = "online"
            payload["fallback_hint"] = "服务器抓取失败；可下发给设备用浏览器打开。"
        return payload
    data["digest"] = _site_digest(data)
    return {"ok": True, "data": data}