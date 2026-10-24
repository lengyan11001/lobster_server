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


@router.post("/api/web/analyze", summary="抓取并解析单个网页")
def web_analyze_page(body: AnalyzePageBody, current_user: User = Depends(get_current_user)):
    _check_rate_limit(int(current_user.id), "analyze")
    try:
        data = analyze_page(body.url, timeout=body.timeout_seconds, max_chars=body.max_chars)
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
    return {"ok": True, "data": data}