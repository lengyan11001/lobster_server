from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.config import settings
from ..models import User
from .auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


class AlibabaPublicSignalsBody(BaseModel):
    company_name: str = Field("", max_length=255)
    buyer_name: str = Field("", max_length=255)
    country: str = Field("", max_length=120)
    product_keywords: list[str] = Field(default_factory=list)
    market_scope: str = Field("", max_length=32)
    max_results: int = Field(default=6, ge=1, le=12)


_UPSTREAM_TASKS: dict[str, dict[str, Any]] = {
    "professional_network_company": {
        "path": "/api/v1/linkedin/web/get_company_profile",
        "method": "GET",
        "allowed": {"company", "company_id"},
        "title": "职业社媒公司资料",
        "category": "社媒公开资料",
    },
    "short_video_account_search": {
        "path": "/api/v1/tiktok/web/fetch_search_user",
        "method": "GET",
        "allowed": {"keyword", "cursor", "search_id", "cookie"},
        "title": "短视频账号公开资料",
        "category": "社媒公开资料",
    },
    "short_video_content_search": {
        "path": "/api/v1/tiktok/web/fetch_search_video",
        "method": "GET",
        "allowed": {"keyword", "count", "offset", "search_id", "cookie"},
        "title": "短视频内容公开资料",
        "category": "社媒公开资料",
    },
    "commerce_product_search": {
        "path": "/api/v1/tiktok/shop/web/fetch_search_products_list",
        "method": "GET",
        "allowed": {"search_word", "offset", "page_token", "region"},
        "title": "电商商品公开资料",
        "category": "电商公开资料",
    },
    "local_video_account_search": {
        "path": "/api/v1/wechat_channels/fetch_user_search",
        "method": "GET",
        "allowed": {"keywords", "page"},
        "title": "视频号账号公开资料",
        "category": "社媒公开资料",
    },
    "local_video_content_search": {
        "path": "/api/v1/wechat_channels/fetch_search_ordinary",
        "method": "GET",
        "allowed": {"keywords"},
        "title": "视频号内容公开资料",
        "category": "社媒公开资料",
    },
    "visual_social_search": {
        "path": "/api/v1/instagram/v1/fetch_search",
        "method": "GET",
        "allowed": {"query", "select"},
        "title": "图片社媒公开资料",
        "category": "社媒公开资料",
    },
    "public_discussion_search": {
        "path": "/api/v1/twitter/web/fetch_search_timeline",
        "method": "GET",
        "allowed": {"keyword", "search_type", "cursor"},
        "title": "海外公开讨论资料",
        "category": "社媒公开资料",
    },
}


def _upstream_base() -> str:
    base = (getattr(settings, "tikhub_api_base", "") or os.environ.get("TIKHUB_API_BASE") or "").strip()
    if base == "https://api.tikhub.dev":
        base = "https://api.tikhub.io"
    return (base or "https://api.tikhub.io").rstrip("/")


def _upstream_key() -> str:
    key = (getattr(settings, "tikhub_api_key", None) or os.environ.get("TIKHUB_API_KEY") or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="公开资料调研服务暂未配置")
    return key


def _clean_text(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _clean_list(values: Any, limit: int = 8) -> list[str]:
    if not isinstance(values, list):
        values = str(values or "").split(",")
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _clean_text(item, 80)
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _lookup(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _collect_items(payload: Any) -> list[Any]:
    items: list[Any] = []

    def walk(value: Any, depth: int = 0) -> None:
        if len(items) >= 12 or depth > 4:
            return
        if isinstance(value, list):
            for row in value[:12]:
                if isinstance(row, dict):
                    items.append(row)
                else:
                    walk(row, depth + 1)
                if len(items) >= 12:
                    return
            return
        if not isinstance(value, dict):
            return
        for key in ("data", "items", "list", "results", "users", "videos", "products", "aweme_list", "user_list", "companies", "company"):
            if key in value:
                child = value.get(key)
                if isinstance(child, dict) and key == "company":
                    items.append(child)
                else:
                    walk(child, depth + 1)
                if len(items) >= 12:
                    return

    walk(payload)
    if not items and isinstance(payload, dict):
        items.append(payload)
    return items[:12]


def _result_count(payload: Any) -> int:
    items = _collect_items(payload)
    if items:
        return len(items)
    if isinstance(payload, dict):
        for key in ("total", "count", "total_count"):
            try:
                return int(payload.get(key) or 0)
            except Exception:
                pass
    return 0


def _item_summary(item: Any) -> str:
    if not isinstance(item, dict):
        return _clean_text(item, 220)
    title = _clean_text(
        _lookup(item, "title")
        or _lookup(item, "name")
        or _lookup(item, "nickname")
        or _lookup(item, "unique_id")
        or _lookup(item, "username")
        or _lookup(item, "companyName")
        or _lookup(item, "company_name")
        or _lookup(item, "author.nickname")
        or _lookup(item, "user.nickname"),
        100,
    )
    desc = _clean_text(
        _lookup(item, "description")
        or _lookup(item, "desc")
        or _lookup(item, "signature")
        or _lookup(item, "summary")
        or _lookup(item, "content")
        or _lookup(item, "author.signature")
        or _lookup(item, "user.signature"),
        220,
    )
    if title and desc and desc != title:
        return f"{title}：{desc}"
    return title or desc or _clean_text(json.dumps(item, ensure_ascii=False, default=str), 260)


def _payload_snippet(payload: Any) -> str:
    rows = [_item_summary(item) for item in _collect_items(payload)]
    rows = [x for x in rows if x]
    if rows:
        return "\n".join(rows[:8])
    return _clean_text(json.dumps(payload, ensure_ascii=False, default=str), 800)


def _safe_payload_preview(payload: Any) -> Any:
    text = _clean_text(json.dumps(payload, ensure_ascii=False, default=str), 3000)
    try:
        return json.loads(text)
    except Exception:
        return {"preview": text}


async def _call_public_signal(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    spec = _UPSTREAM_TASKS.get(kind)
    if not spec:
        raise HTTPException(status_code=400, detail="不支持的公开资料类型")
    clean_params = {k: v for k, v in (params or {}).items() if k in spec["allowed"] and v not in (None, "")}
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=35.0, trust_env=False) as client:
        resp = await client.get(
            f"{_upstream_base()}{spec['path']}",
            headers={"Authorization": f"Bearer {_upstream_key()}", "Accept": "application/json"},
            params=clean_params,
        )
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        payload = resp.json()
    except Exception:
        payload = {"text": (resp.text or "")[:4000]}
    if resp.status_code >= 400:
        return {"ok": False, "kind": kind, "reason": f"公开资料接口返回 {resp.status_code}", "latency_ms": latency_ms}
    code = payload.get("code") if isinstance(payload, dict) else None
    if code not in (None, 0, 1, 200, "0", "1", "200"):
        return {"ok": False, "kind": kind, "reason": _clean_text(payload.get("message_zh") or payload.get("message") or "公开资料接口未返回成功", 240), "latency_ms": latency_ms}
    count = _result_count(payload)
    return {
        "ok": True,
        "kind": kind,
        "source_type": kind,
        "title": spec["title"],
        "category": spec["category"],
        "result_count": count,
        "snippet": _payload_snippet(payload),
        "latency_ms": latency_ms,
    }


def _build_tasks(body: AlibabaPublicSignalsBody) -> list[dict[str, Any]]:
    company = _clean_text(body.company_name, 160)
    buyer = _clean_text(body.buyer_name, 160)
    country = _clean_text(body.country, 80)
    products = _clean_list(body.product_keywords, 4)
    is_cn = bool(re.search(r"[\u4e00-\u9fa5]", " ".join([company, buyer, country]))) or _clean_text(body.market_scope).upper() == "CN"
    tasks: list[dict[str, Any]] = []
    if company:
        tasks.extend(
            [
                {"kind": "professional_network_company", "params": {"company": company}, "field": "company_name"},
                {"kind": "short_video_account_search", "params": {"keyword": company}, "field": "company_name"},
                {"kind": "visual_social_search", "params": {"query": company, "select": "users"}, "field": "company_name"},
                {"kind": "public_discussion_search", "params": {"keyword": company, "search_type": "Top"}, "field": "company_name"},
            ]
        )
    if products:
        product = products[0]
        tasks.extend(
            [
                {"kind": "short_video_content_search", "params": {"keyword": product, "count": max(2, min(8, body.max_results)), "offset": 0}, "field": "product_keywords"},
                {"kind": "commerce_product_search", "params": {"search_word": product, "offset": 0}, "field": "product_keywords"},
            ]
        )
    if is_cn and (company or buyer):
        keyword = company or buyer
        tasks.extend(
            [
                {"kind": "local_video_account_search", "params": {"keywords": keyword, "page": 1}, "field": "company_name" if company else "buyer_name"},
                {"kind": "local_video_content_search", "params": {"keywords": keyword}, "field": "company_name" if company else "buyer_name"},
            ]
        )
    return tasks[:8]


@router.post("/api/alibaba-customer-research/public-signals", summary="阿里询盘客户公开资料调研")
async def alibaba_customer_public_signals(
    body: AlibabaPublicSignalsBody,
    current_user: User = Depends(get_current_user),
):
    tasks = _build_tasks(body)
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for task in tasks:
        try:
            result = await _call_public_signal(task["kind"], task.get("params") or {})
            if result.get("ok") and int(result.get("result_count") or 0) > 0 and _clean_text(result.get("snippet"), 20):
                items.append(
                    {
                        "source_type": result["source_type"],
                        "title": result["title"],
                        "category": result["category"],
                        "field": task.get("field") or "",
                        "result_count": int(result.get("result_count") or 0),
                        "snippet": _clean_text(result.get("snippet"), 1200),
                        "confidence": "B",
                        "raw": {
                            "field": task.get("field") or "",
                            "result_count": int(result.get("result_count") or 0),
                            "latency_ms": result.get("latency_ms") or 0,
                        },
                    }
                )
            else:
                skipped.append({"kind": task["kind"], "field": task.get("field") or "", "reason": result.get("reason") or "未获取到有效结果"})
        except Exception as exc:
            logger.info("[ALIBABA-RESEARCH] public signal skipped user=%s kind=%s err=%s", current_user.id, task.get("kind"), exc)
            skipped.append({"kind": task.get("kind") or "", "field": task.get("field") or "", "reason": "公开资料暂不可用"})
    return {"ok": True, "items": items, "skipped_count": len(skipped)}
