from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
import re
import time
import uuid
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .admin import AdminContext, _agent_visible_user_ids, _assert_can_manage_user, _verify_admin_token
from .auth import access_token_claims, create_access_token, get_current_user
from ..core.config import settings
from ..db import get_db
from ..models import ContentCompetitorAccount, IPContentDraftRecord, IPContentKeyword, IPContentScheduleTemplate, TikHubQueryLog, TikHubSourceItem, User
from ..services.credit_ledger import append_credit_ledger
from ..services.credits_amount import credits_json_float, quantize_credits, user_balance_decimal

router = APIRouter()

_SOURCE_META_KEY = "__lobster_ip_content_meta"
_SOURCE_USAGE_KEY = "__lobster_ip_content_usage"
_RETRY_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


def _retry_delay(attempt_index: int) -> float:
    return min(8.0, 1.2 * (attempt_index + 1))


def _is_retryable_detail(text: Any) -> bool:
    raw = str(text or "").lower()
    if not raw:
        return False
    return any(
        marker in raw
        for marker in [
            "timeout",
            "timed out",
            "proxy read timeout",
            "temporarily unavailable",
            "connection reset",
            "connection refused",
            "connection aborted",
            "remote protocol",
            "server disconnected",
            "too many requests",
        ]
    )


def _retryable_http_exception(exc: HTTPException) -> bool:
    try:
        status = int(exc.status_code or 0)
    except Exception:
        status = 0
    return status in _RETRY_HTTP_STATUSES or _is_retryable_detail(exc.detail)


def _sync_error_result(*, source: str, row: Any, exc: Exception, attempts: int) -> dict[str, Any]:
    return {
        "ok": False,
        "source": source,
        "attempts": attempts,
        "error_message": str(getattr(exc, "detail", None) or exc)[:2000],
        "keyword": _keyword_payload(row) if isinstance(row, IPContentKeyword) else None,
        "competitor": _competitor_payload(row) if isinstance(row, ContentCompetitorAccount) else None,
    }


_ENDPOINTS: dict[str, dict[str, Any]] = {
    "douyin_hot_search": {
        "platform": "douyin",
        "source_type": "hot_search",
        "method": "GET",
        "path": "/api/v1/douyin/app/v3/fetch_hot_search_list",
        "allowed_params": {"board_type", "board_sub_type"},
    },
    "douyin_hot_total": {
        "platform": "douyin",
        "source_type": "hot_total",
        "method": "GET",
        "path": "/api/v1/douyin/billboard/fetch_hot_total_list",
        "allowed_params": {
            "page",
            "page_size",
            "type",
            "snapshot_time",
            "start_date",
            "end_date",
            "sentence_tag",
            "keyword",
        },
    },
    "douyin_billboard_video": {
        "platform": "douyin",
        "source_type": "billboard_video",
        "method": "POST",
        "path": "/api/v1/douyin/billboard/fetch_hot_total_video_list",
        "allowed_body": {"page", "page_size", "date_window", "sub_type", "tags"},
    },
    "douyin_billboard_topic": {
        "platform": "douyin",
        "source_type": "billboard_topic",
        "method": "POST",
        "path": "/api/v1/douyin/billboard/fetch_hot_total_topic_list",
        "allowed_body": {"page", "page_size", "date_window", "tags"},
    },
    "douyin_billboard_search": {
        "platform": "douyin",
        "source_type": "billboard_search",
        "method": "POST",
        "path": "/api/v1/douyin/billboard/fetch_hot_total_search_list",
        "allowed_body": {"page_num", "page_size", "date_window", "keyword", "tags"},
    },
    "douyin_search_video_v2": {
        "platform": "douyin",
        "source_type": "keyword_video",
        "method": "POST",
        "path": "/api/v1/douyin/search/fetch_video_search_v2",
        "allowed_body": {"keyword", "cursor", "sort_type", "publish_time", "filter_duration", "content_type", "search_id", "backtrace"},
    },
    "douyin_creator_user_search": {
        "platform": "douyin",
        "source_type": "user_search",
        "method": "GET",
        "path": "/api/v1/douyin/creator/fetch_user_search",
        "allowed_params": {"user_name"},
    },
    "douyin_search_user_v2": {
        "platform": "douyin",
        "source_type": "user_search",
        "method": "POST",
        "path": "/api/v1/douyin/search/fetch_user_search_v2",
        "allowed_body": {"keyword", "cursor"},
    },
    "douyin_user_posts": {
        "platform": "douyin",
        "source_type": "user_post",
        "method": "GET",
        "path": "/api/v1/douyin/app/v3/fetch_user_post_videos",
        "allowed_params": {"sec_user_id", "max_cursor", "count", "sort_type"},
    },
    "wechat_channels_hot_words": {
        "platform": "wechat_channels",
        "source_type": "hot_words",
        "method": "GET",
        "path": "/api/v1/wechat_channels/fetch_hot_words",
        "allowed_params": set(),
    },
    "wechat_channels_search_latest": {
        "platform": "wechat_channels",
        "source_type": "keyword_video",
        "method": "GET",
        "path": "/api/v1/wechat_channels/fetch_search_latest",
        "allowed_params": {"keywords"},
    },
    "wechat_channels_user_search_v2": {
        "platform": "wechat_channels",
        "source_type": "user_search",
        "method": "GET",
        "path": "/api/v1/wechat_channels/fetch_user_search_v2",
        "allowed_params": {"keywords", "page"},
    },
    "wechat_channels_home_page": {
        "platform": "wechat_channels",
        "source_type": "home_page",
        "method": "POST",
        "path": "/api/v1/wechat_channels/fetch_home_page",
        "allowed_body": {"username", "last_buffer"},
    },
}


class TikHubQueryBody(BaseModel):
    query_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)
    save_items: bool = True


class CompetitorCreateBody(BaseModel):
    platform: str
    account_key: str
    display_name: str = ""
    homepage_url: str = ""
    industry_tags: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class CompetitorSyncBody(BaseModel):
    count: int = Field(20, ge=1, le=50)
    last_buffer: str = ""


class KeywordCreateBody(BaseModel):
    keyword: str
    display_name: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class KeywordSyncBody(BaseModel):
    page_size: int = Field(20, ge=1, le=50)
    date_window: int = Field(24, ge=1, le=720)


class DraftRequestBody(BaseModel):
    task: str
    platform: str = ""
    memory_docs: list[dict[str, Any]] = Field(default_factory=list)
    query_ids: list[str] = Field(default_factory=list)
    item_ids: list[int] = Field(default_factory=list)
    extra_requirements: str = ""
    count: int = Field(5, ge=1, le=20)


class AutoDraftRequestBody(BaseModel):
    memory_docs: list[dict[str, Any]] = Field(default_factory=list)
    keyword_ids: list[int] = Field(default_factory=list)
    competitor_ids: list[int] = Field(default_factory=list)
    extra_requirements: str = ""
    count: int = Field(5, ge=1, le=20)
    sync_before: bool = False
    group_id: str = ""


class DraftRecordImageBody(BaseModel):
    image_url: str = ""
    image_asset_id: str = ""
    image_prompt: str = ""
    selected: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)


class ScheduleTemplateBody(BaseModel):
    name: str = Field("", max_length=160)
    keyword_ids: list[int] = Field(default_factory=list)
    competitor_ids: list[int] = Field(default_factory=list)
    memory_docs: list[dict[str, Any]] = Field(default_factory=list)
    requirements: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class ScheduledDailyRunOptions(BaseModel):
    template_id: Optional[int] = None
    keyword_ids: list[int] = Field(default_factory=list)
    competitor_ids: list[int] = Field(default_factory=list)
    memory_docs: list[dict[str, Any]] = Field(default_factory=list)
    requirements: dict[str, Any] = Field(default_factory=dict)
    sync_before: bool = True
    industry_count: int = Field(5, ge=1, le=20)
    ip_count: int = Field(5, ge=1, le=20)
    moments_count: int = Field(20, ge=1, le=20)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _clean_text(value: Any, max_len: int = 255) -> str:
    if value is None:
        return ""
    return str(value).strip()[:max_len]


def _clean_long_text(value: Any, max_len: int = 12000) -> str:
    if value is None:
        return ""
    return str(value).strip()[:max_len]


def _clean_group_id(value: Any) -> str:
    text = _clean_text(value, 80)
    if re.fullmatch(r"[A-Za-z0-9_.:-]{6,80}", text or ""):
        return text
    return ""


def _strip_embedded_image_prompt(value: Any, max_len: int = 8000) -> str:
    text = _clean_long_text(value, max_len)
    if not text:
        return ""
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned: list[str] = []
    skip_block = False
    image_prompt_re = re.compile(r"^\s*(?:配图提示|画面建议|可配画面建议|视觉提示|图片提示|image[_\s-]*prompt|visual[_\s-]*prompt)\s*[:：]", re.I)
    for line in lines:
        if image_prompt_re.match(line):
            skip_block = True
            continue
        if skip_block:
            if not line.strip():
                skip_block = False
                continue
            if re.match(r"^\s*(?:标题|开场|正文|口播|收口|CTA|互动|文案)\s*[:：]", line, re.I):
                skip_block = False
            else:
                continue
        if not image_prompt_re.match(line):
            cleaned.append(line)
    text = "\n".join(cleaned).strip()
    text = re.sub(r"(?:^|\n)\s*(?:配图提示|画面建议|可配画面建议|视觉提示|图片提示)\s*[:：].*(?=\n|$)", "", text, flags=re.I)
    text = re.sub(r"\s*(?:配图提示|画面建议|可配画面建议|视觉提示|图片提示)\s*[:：].*$", "", text, flags=re.I | re.S)
    return text.strip()[:max_len]


def _normalize_image_prompts(item: dict[str, Any], fallback: str = "") -> list[str]:
    raw = item.get("image_prompts") or item.get("配图提示组") or item.get("visual_prompts")
    prompts: list[str] = []
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                text = _clean_long_text(entry.get("prompt") or entry.get("text") or entry.get("配图提示"), 1600)
            else:
                text = _clean_long_text(entry, 1600)
            if text and text not in prompts:
                prompts.append(text)
    elif isinstance(raw, str):
        parts = [p.strip() for p in re.split(r"\n\s*(?:\d+[\.\、)]|[-*])\s*|[；;]\s*", raw) if p.strip()]
        for part in parts:
            text = _clean_long_text(part, 1600)
            if text and text not in prompts:
                prompts.append(text)
    fallback_text = _clean_long_text(fallback, 1600)
    if fallback_text and fallback_text not in prompts:
        prompts.insert(0, fallback_text)
    return prompts[:3]


def _is_moments_task(task: str) -> bool:
    return (task or "").strip().lower() in {"task2_moments", "moments_candidate"}


def _is_oral_task(task: str) -> bool:
    return (task or "").strip().lower() in {"task1_industry", "task1_ip", "industry_hot_oral", "professional_ip_oral"}


def _strip_moments_comment_bait(value: Any, max_len: int = 8000) -> str:
    text = _clean_long_text(value, max_len)
    if not text:
        return ""
    text = re.sub(r"[^。！？!?；;\n]*(?:评论区|留言区|评论里|留言里)[^。！？!?；;\n]*(?:告诉我|聊聊|说说|打出来|扣|回复|留言|评论)[。！？!?；;]?", "", text)
    text = re.sub(r"[^。！？!?；;\n]*(?:你觉得|你认为|你遇到过吗|有没有同感)[^。！？!?；;\n]*(?:评论|留言)[^。！？!?；;]*[。！？!?；;]?", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()[:max_len]


def _strip_search_markup(value: Any, max_len: int = 255) -> str:
    raw = _clean_long_text(value, max(max_len * 4, max_len))
    if not raw:
        return ""
    raw = re.sub(r"<[^>]+>", "", raw)
    return html.unescape(raw).strip()[:max_len]


def _jsonable(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return {"repr": repr(value)[:4000]}


def _tikhub_api_base() -> str:
    base = (getattr(settings, "tikhub_api_base", "") or os.environ.get("TIKHUB_API_BASE") or "").strip()
    return (base or "https://api.tikhub.dev").rstrip("/")


def _tikhub_api_key() -> str:
    key = (getattr(settings, "tikhub_api_key", None) or os.environ.get("TIKHUB_API_KEY") or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="服务器未配置 TIKHUB_API_KEY")
    return key


def _query_price(query_type: str) -> Decimal:
    raw = getattr(settings, "tikhub_query_unit_credits", 1.0)
    try:
        return quantize_credits(Decimal(str(raw)))
    except Exception:
        return quantize_credits(1)


def _internal_api_base() -> str:
    raw = (os.environ.get("LOBSTER_INTERNAL_API_BASE") or "").strip().rstrip("/")
    if raw:
        return raw
    return f"http://127.0.0.1:{int(getattr(settings, 'port', 8000) or 8000)}"


def _clean_mapping(data: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in data.items():
        k = str(key).strip()
        if k not in allowed:
            continue
        if value is None:
            continue
        if isinstance(value, str):
            v = value.strip()
            if not v or v.lower() in {"undefined", "null"}:
                continue
            out[k] = v
        else:
            out[k] = value
    return out


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _lookup(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return None
    return cur


def _first(obj: Any, paths: list[str]) -> Any:
    for path in paths:
        value = _lookup(obj, path)
        if value not in (None, ""):
            return value
    return None


def _stable_hash(value: Any, length: int = 40) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _collect_items(node: Any, *, depth: int = 0) -> list[Any]:
    if depth > 5:
        return []
    if isinstance(node, list):
        return node
    if not isinstance(node, dict):
        return []
    for key in (
        "aweme_list",
        "object_list",
        "word_list",
        "hot_words",
        "hot_list",
        "rank_list",
        "item_list",
        "items",
        "list",
        "results",
        "records",
        "videos",
        "user_list",
        "users",
        "object",
        "objs",
        "data_list",
        "search_list",
        "business_data",
    ):
        value = node.get(key)
        if isinstance(value, list):
            if key == "business_data":
                flattened: list[Any] = []
                for entry in value:
                    nested = _collect_items(entry, depth=depth + 1)
                    if nested:
                        flattened.extend(nested)
                    else:
                        flattened.append(entry)
                return flattened
            return value
        nested = _collect_items(value, depth=depth + 1)
        if nested:
            return nested
    for key in ("data", "result"):
        nested = _collect_items(node.get(key), depth=depth + 1)
        if nested:
            return nested
    return []


def _response_code(payload: Any) -> Optional[int]:
    if not isinstance(payload, dict):
        return None
    for key in ("code", "status_code", "statusCode", "status"):
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
    return None


def _response_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    value = _first(payload, ["message_zh", "message", "msg", "error.message", "detail.message_zh", "detail.message", "detail"])
    return _clean_long_text(value, 2000)


def _response_success_flag(payload: Any) -> Optional[bool]:
    if not isinstance(payload, dict):
        return None
    for key in ("success", "ok"):
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
    return None


def _response_request_id(payload: Any, headers: httpx.Headers | None = None) -> str:
    if isinstance(payload, dict):
        value = _first(payload, ["request_id", "requestId", "data.request_id", "trace_id", "traceId"])
        if value:
            return _clean_text(value, 128)
    if headers is not None:
        for key in ("x-request-id", "x-trace-id", "x-tikhub-request-id"):
            value = headers.get(key)
            if value:
                return _clean_text(value, 128)
    return ""


def _response_cache_url(payload: Any) -> str:
    if isinstance(payload, dict):
        value = _first(payload, ["cache_url", "cacheUrl", "data.cache_url", "data.cacheUrl"])
        if value:
            return _clean_long_text(value, 4096)
    return ""


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.replace("json\n", "", 1).replace("JSON\n", "", 1).strip()
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _public_url(raw: Any) -> str:
    value = _first(
        raw,
        [
            "share_url",
            "url",
            "item_url",
            "uri",
            "aweme_url",
            "video_url",
            "web_url",
            "finder_info_export.url",
            "object_desc.media.0.url",
            "object_desc.media.0.video_url",
            "object_desc.media.0.full_url",
            "share_info.share_url",
            "aweme_info.share_url",
            "aweme_info.share_info.share_url",
        ],
    )
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = _first(value, ["url", "uri", "download_addr.url_list.0"])
    return _clean_long_text(value, 4096)


def _cover_url(raw: Any) -> str:
    value = _first(
        raw,
        [
            "cover_url",
            "item_cover_url",
            "cover",
            "image_url",
            "thumbUrl",
            "thumb_url",
            "cover.url_list.0",
            "object_desc.media.0.cover_url",
            "object_desc.media.0.thumb_url",
            "object_desc.media.0.thumbUrl",
            "object_desc.media.0.url",
            "contact.cover_img_url",
            "video.cover.url_list.0",
            "aweme_info.video.cover.url_list.0",
        ],
    )
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = _first(value, ["url", "uri", "url_list.0"])
    return _clean_long_text(value, 4096)


def _metric_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    source = raw
    stats = raw.get("statistics") if isinstance(raw.get("statistics"), dict) else {}
    aweme_stats = _lookup(raw, "aweme_info.statistics")
    if isinstance(aweme_stats, dict):
        stats = {**stats, **aweme_stats}
    out: dict[str, Any] = {}
    for key in (
        "rank",
        "position",
        "show_rank",
        "real_rank",
        "origin_rank",
        "hot_value",
        "heat",
        "view_count",
        "play_count",
        "play_cnt",
        "publish_cnt",
        "avg_play_cnt",
        "digg_count",
        "like_count",
        "like_cnt",
        "comment_count",
        "share_count",
        "collect_count",
        "fans_cnt",
        "search_score",
        "score",
        "follow_cnt",
        "follow_rate",
        "like_rate",
        "forward_count",
        "read_count",
        "read_cnt",
        "replay_count",
        "replay_cnt",
        "fav_count",
    ):
        value = stats.get(key) if key in stats else source.get(key)
        if value not in (None, ""):
            out[key] = value
    return out


def _source_raw(row: TikHubSourceItem) -> dict[str, Any]:
    return dict(row.raw or {}) if isinstance(row.raw, dict) else {}


def _source_meta(row: TikHubSourceItem) -> dict[str, Any]:
    raw = _source_raw(row)
    meta = raw.get(_SOURCE_META_KEY)
    return dict(meta or {}) if isinstance(meta, dict) else {}


def _source_usage(row: TikHubSourceItem) -> list[dict[str, Any]]:
    raw = _source_raw(row)
    usage = raw.get(_SOURCE_USAGE_KEY)
    if not isinstance(usage, list):
        return []
    return [dict(item) for item in usage if isinstance(item, dict)]


def _source_used_for(row: TikHubSourceItem, task: str = "") -> bool:
    task_key = (task or "").strip()
    usage = _source_usage(row)
    if not task_key:
        return bool(usage)
    return any(str(item.get("task") or "") == task_key for item in usage)


def _merge_source_raw(raw_value: Any, meta: Optional[dict[str, Any]] = None, usage: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    raw = dict(raw_value or {}) if isinstance(raw_value, dict) else {"value": raw_value}
    if meta is not None:
        old_meta = raw.get(_SOURCE_META_KEY) if isinstance(raw.get(_SOURCE_META_KEY), dict) else {}
        raw[_SOURCE_META_KEY] = {**old_meta, **_jsonable(meta or {})}
    if usage is not None:
        raw[_SOURCE_USAGE_KEY] = _jsonable(usage)
    return raw


def _mark_source_rows_used(db: Session, rows: list[TikHubSourceItem], *, task: str, record_id: str) -> None:
    now_text = _utcnow().isoformat()
    for row in rows:
        usage = _source_usage(row)
        if any(str(item.get("task") or "") == task and str(item.get("record_id") or "") == record_id for item in usage):
            continue
        usage.append({"task": task, "record_id": record_id, "used_at": now_text})
        row.raw = _merge_source_raw(row.raw, usage=usage)
        row.updated_at = _utcnow()
    if rows:
        db.flush()


def _memory_payload_from_docs(docs: list[dict[str, Any]], *, content_limit: int = 2400) -> list[dict[str, Any]]:
    memories: list[dict[str, Any]] = []
    for doc in docs[:12]:
        if not isinstance(doc, dict):
            continue
        doc_id = doc.get("id") or doc.get("doc_id") or doc.get("memory_id") or doc.get("filename") or doc.get("name")
        memories.append(
            {
                "id": _clean_text(doc_id, 191),
                "title": _clean_text(doc.get("title") or doc.get("name") or doc.get("filename"), 120),
                "content": _clean_long_text(doc.get("content") or doc.get("content_text") or doc.get("text") or doc.get("summary") or doc.get("notes"), content_limit),
            }
        )
    return memories


def _item_brief(row: TikHubSourceItem, idx: int) -> dict[str, Any]:
    return {
        "序号": idx,
        "平台": row.platform,
        "来源": row.source_type,
        "标题": row.title or row.description or row.item_key,
        "作者": row.author_name or row.author_key or "",
        "发布时间": row.publish_time or "",
        "指标": row.metrics or {},
        "链接": row.public_url or "",
    }


def _draft_requirements(task: str, platform: str, count: int) -> str:
    task_key = (task or "").strip().lower()
    plat = (platform or "").strip().lower()
    current_year = _utcnow().year
    if task_key == "task1_industry":
        return (
            f"任务一：基于行业榜单/热门话题生成 {count} 条行业热门口播文案。"
            "每条要有标题、开场钩子、口播正文、转化/互动收口；不要生成图片提示词、配图提示或画面建议。"
            "暂不限制字数或口播时长，优先把观点、案例、行业判断讲透。"
            "必须写出一个具体业务场景或案例拆解：问题是什么、判断依据是什么、给用户的启发是什么。"
            "可以有评论互动收口，但要服务于口播观点，不要硬凑。"
            "如果本轮同步到了多个关键词的新数据，5 条内容方向必须明显有差异，尽量覆盖不同关键词、不同痛点、不同案例。"
            f"涉及年份时默认使用当前年份 {current_year} 年；除非数据源明确出现其他年份，不要写 2025 年等过去年份。"
            "语气要像真实短视频创作者，不要空泛鸡汤。"
        )
    if task_key == "task1_ip":
        return (
            f"任务一：基于同行最新作品和本地知识库生成 {count} 条个人专业 IP 口播文案。"
            "暂不限制字数或口播时长，优先写出深度。"
            "要体现专业判断、案例感和个人观点；每条至少包含一个具体场景、案例或反常识判断，并说明为什么。"
            "不能抄袭同行原文，只提炼选题结构和表达角度。"
            "不要生成图片提示词、配图提示或画面建议。"
            "可以有评论互动收口，但要服务于口播观点，不要硬凑。"
            f"涉及年份时默认使用当前年份 {current_year} 年；除非数据源明确出现其他年份，不要写 2025 年等过去年份。"
            "如果同行数据不足，用记忆资料补足到指定条数。"
        )
    if task_key == "task2_topics":
        return (
            f"任务二：生成 {count} 个朋友圈/短内容选题池。"
            "每个选题给出角度、适合人群、可用素材线索和推荐发布平台。"
            "选题要方便用户审核后继续出文案或出图。"
        )
    if task_key == "task2_moments":
        return (
            f"任务二：生成 {count} 条朋友圈文案。"
            "每条要自然、有生活场景、有专业可信度，适合个人 IP 日更；但表达风格要更像私域社群爆款发售体。"
            "排版必须规整：短句分行，2 到 4 行形成一个小段落，段落之间空一行；拒绝大段文字。"
            "允许并鼓励精准使用 Emoji，例如 💥、🔥、🎯、💯、😲、😎、🚀、👇、💸、🌟；每条正文建议 4 到 8 个，但不要堆到每一句都有。"
            "语言要短、狠、直给，用大白话打痛点、给结果、造紧迫感；围绕降本、增收、流量、获客、效率、赚钱这些老板关心的结果。"
            "结构建议：吸睛开场/反问；痛点或趋势；结合记忆资料里的产品/服务给出解决方案；最后用自然私域动作收口。"
            "朋友圈不要写“评论区告诉我/留言告诉我/你觉得呢”这类引导评论句，收尾要像真实朋友圈表达。"
            "正文和配图提示必须分开：body 只放朋友圈正文，不要把“配图提示/画面建议”写进 body。"
            "必须给出 3 条贴合该文案但创意明显不同的配图提示，放到 image_prompts 数组；image_prompt 可放第一条或整体摘要。"
            "3 条配图提示要分别从不同场景/主体/隐喻切入，不能只是换视角或换形容词；必须让 3 张图看起来是同主题的三套创意方案。"
            f"涉及年份时默认使用当前年份 {current_year} 年；除非数据源明确出现其他年份，不要写 2025 年等过去年份。"
        )
    if plat == "douyin":
        return f"生成 {count} 条抖音口播/标题文案，强开头、强节奏、适合评论互动。"
    if plat == "wechat_channels":
        return f"生成 {count} 条视频号文案，稳重可信，适合中高净值/熟人社交传播。"
    return f"生成 {count} 条可直接审核发布的中文内容草稿。"


def _normalize_drafts(obj: dict[str, Any], fallback_text: str, count: int) -> list[dict[str, str]]:
    raw_items = obj.get("items") or obj.get("drafts") or obj.get("scripts") or []
    if isinstance(raw_items, dict):
        raw_items = list(raw_items.values())
    drafts: list[dict[str, str]] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                text = _clean_long_text(item, 6000)
                if text:
                    drafts.append({"title": text[:40], "body": text, "image_prompt": "", "image_prompts": []})
                continue
            title = _clean_text(item.get("title") or item.get("选题") or item.get("headline"), 160)
            hook = _clean_long_text(item.get("hook") or item.get("开场") or "", 1000)
            body = _clean_long_text(item.get("body") or item.get("copy") or item.get("script") or item.get("正文"), 6000)
            cta = _clean_long_text(item.get("cta") or item.get("收口") or "", 1000)
            image_prompt = _clean_long_text(item.get("image_prompt") or item.get("配图提示") or item.get("visual_prompt"), 1600)
            image_prompts = _normalize_image_prompts(item, image_prompt)
            pieces = [x for x in (hook, body, cta) if x]
            full_text = "\n\n".join(pieces) if pieces else _clean_long_text(item.get("full_text") or item.get("text"), 6000)
            full_text = _strip_embedded_image_prompt(full_text, 6000)
            drafts.append(
                {
                    "title": title or (full_text[:40] if full_text else "未命名文案"),
                    "body": full_text or title or "",
                    "image_prompt": image_prompt or (image_prompts[0] if image_prompts else ""),
                    "image_prompts": image_prompts,
                }
            )
    if not drafts:
        text = fallback_text.strip()
        parts = [p.strip() for p in re.split(r"\n\s*(?:\d+[\.\、]|[-*])\s*", text) if p.strip()]
        if len(parts) <= 1:
            parts = [text] if text else []
        for part in parts[:count]:
            cleaned_part = _strip_embedded_image_prompt(part, 6000)
            drafts.append({"title": cleaned_part[:40], "body": cleaned_part, "image_prompt": "", "image_prompts": []})
    return drafts[:count]


def _normalize_item(raw: Any, *, user_id: int, query_id: str, platform: str, source_type: str, idx: int) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {"value": raw}
    aweme_info = _lookup(item, "data.aweme_info")
    if not isinstance(aweme_info, dict):
        aweme_info = item.get("aweme_info") if isinstance(item.get("aweme_info"), dict) else {}
    field_item = {**item, **aweme_info, "aweme_info": aweme_info} if aweme_info else item
    author = field_item.get("author") if isinstance(field_item.get("author"), dict) else {}
    if not author:
        author = field_item.get("user") if isinstance(field_item.get("user"), dict) else {}
    if not author:
        author = field_item.get("contact") if isinstance(field_item.get("contact"), dict) else {}
    if not author:
        nested_author = _lookup(field_item, "aweme_info.author")
        author = nested_author if isinstance(nested_author, dict) else {}
    title = _first(
        field_item,
        [
            "title",
            "item_title",
            "challenge_name",
            "desc",
            "description",
            "sentence",
            "word",
            "hotword",
            "key_word",
            "keyword",
            "name",
            "object_desc.description",
            "aweme_info.desc",
            "aweme_info.caption",
        ],
    )
    description = _first(field_item, ["description", "desc", "summary", "challenge_name", "object_desc.description", "aweme_info.desc"])
    item_key = _first(
        field_item,
        [
            "aweme_id",
            "id",
            "challenge_id",
            "item_id",
            "video_id",
            "sentence_id",
            "word_id",
            "mid",
            "object_id",
            "displayid",
            "aweme_info.aweme_id",
        ],
    )
    if item_key in (None, ""):
        basis = _first(field_item, ["share_url", "url", "title", "desc", "sentence", "word", "key_word", "keyword"]) or item
        item_key = _stable_hash({"platform": platform, "source_type": source_type, "idx": idx, "basis": basis})
    author_key = _first(
        author,
        ["sec_uid", "uid", "id", "short_id", "unique_id", "username", "finder_username", "nickname"],
    )
    author_name = _first(author, ["nickname", "name", "display_name", "unique_id", "short_id"]) or _first(field_item, ["nick_name", "author_name", "nickname"])
    publish_time = _first(field_item, ["create_time", "createtime", "publish_time", "timestamp", "aweme_info.create_time"])
    return {
        "user_id": user_id,
        "query_id": query_id,
        "platform": platform,
        "source_type": source_type,
        "item_key": _clean_text(item_key, 191) or _stable_hash(item),
        "author_key": _clean_text(author_key, 191) or None,
        "author_name": _clean_text(author_name, 255) or None,
        "title": _clean_long_text(title, 4000) or None,
        "description": _clean_long_text(description, 8000) or None,
        "public_url": _public_url(field_item) or None,
        "cover_url": _cover_url(field_item) or None,
        "publish_time": _clean_text(publish_time, 64) or None,
        "metrics": _metric_payload(field_item) or None,
        "raw": _jsonable(item),
    }


def _item_payload(row: TikHubSourceItem) -> dict[str, Any]:
    usage = _source_usage(row)
    return {
        "id": row.id,
        "query_id": row.query_id,
        "platform": row.platform,
        "source_type": row.source_type,
        "item_key": row.item_key,
        "author_key": row.author_key or "",
        "author_name": row.author_name or "",
        "title": row.title or "",
        "description": row.description or "",
        "public_url": row.public_url or "",
        "cover_url": row.cover_url or "",
        "publish_time": row.publish_time or "",
        "metrics": row.metrics or {},
        "source_meta": _source_meta(row),
        "usage": usage,
        "used_for": sorted({str(item.get("task") or "") for item in usage if item.get("task")}),
        "is_used": bool(usage),
        "is_new": bool(row.is_new),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _query_log_payload(row: TikHubQueryLog, *, include_raw: bool = False, items: Optional[list[TikHubSourceItem]] = None) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "user_id": row.user_id,
        "query_id": row.query_id,
        "platform": row.platform,
        "query_type": row.query_type,
        "method": row.method,
        "endpoint": row.endpoint,
        "request_params": row.request_params or {},
        "request_body": row.request_body or {},
        "status": row.status,
        "success": bool(row.success),
        "http_status": row.http_status,
        "tikhub_code": row.tikhub_code,
        "tikhub_request_id": row.tikhub_request_id or "",
        "cache_url": row.cache_url or "",
        "credits_charged": credits_json_float(row.credits_charged or 0),
        "latency_ms": row.latency_ms,
        "result_count": int(row.result_count or 0),
        "result_snapshot": row.result_snapshot or {},
        "error_message": row.error_message or "",
        "meta": row.meta or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if include_raw:
        payload["raw_response"] = row.raw_response or {}
    if items is not None:
        payload["items"] = [_item_payload(item) for item in items]
    return payload


def _competitor_payload(row: ContentCompetitorAccount) -> dict[str, Any]:
    return {
        "id": row.id,
        "platform": row.platform,
        "display_name": row.display_name or "",
        "account_key": row.account_key,
        "homepage_url": row.homepage_url or "",
        "industry_tags": row.industry_tags or "",
        "status": row.status,
        "last_seen_item_key": row.last_seen_item_key or "",
        "last_fetch_at": row.last_fetch_at.isoformat() if row.last_fetch_at else None,
        "meta": row.meta or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _avatar_url(raw: Any) -> str:
    value = _first(
        raw,
        [
            "avatar_thumb.url_list.0",
            "avatar_medium.url_list.0",
            "avatar_larger.url_list.0",
            "avatar_url",
            "avatar",
            "head_image_url",
        ],
    )
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = _first(value, ["url_list.0", "url", "uri"])
    return _clean_long_text(value, 4096)


def _normalize_douyin_user(raw: Any, idx: int) -> Optional[dict[str, Any]]:
    item = raw if isinstance(raw, dict) else {"value": raw}
    user = item.get("user_info") if isinstance(item.get("user_info"), dict) else {}
    if not user:
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
    if not user:
        user = item.get("author") if isinstance(item.get("author"), dict) else {}
    if not user:
        nested = _lookup(item, "data.user_info")
        user = nested if isinstance(nested, dict) else item

    sec_uid = _first(user, ["sec_uid", "sec_user_id", "secUid", "user_id"])
    if not sec_uid:
        sec_uid = _first(item, ["sec_uid", "sec_user_id", "user_id", "user_info.sec_uid", "data.user_info.sec_uid"])
    if not sec_uid:
        return None

    unique_id = _first(user, ["unique_id", "short_id", "custom_verify_id"])
    nickname = _first(user, ["nickname", "nick_name", "name", "display_name"]) or _first(item, ["nickname", "nick_name", "user_info.nickname"])
    follower_count = _first(user, ["follower_count", "followers_count", "fans_count", "fans_cnt", "total_favorited"])
    following_count = _first(user, ["following_count", "follow_count"])
    aweme_count = _first(user, ["aweme_count", "video_count", "works_count", "publish_cnt"])
    like_count = _first(user, ["like_cnt", "total_favorited", "favoriting_count"])
    signature = _first(user, ["signature", "desc", "description", "bio"])
    verify = _first(user, ["enterprise_verify_reason", "custom_verify", "verification_type"])
    homepage_url = _first(user, ["share_info.share_url", "homepage_url", "share_url"])
    if not homepage_url:
        homepage_url = f"https://www.douyin.com/user/{sec_uid}"

    return {
        "id": _clean_text(sec_uid, 191),
        "sec_user_id": _clean_text(sec_uid, 191),
        "sec_uid": _clean_text(sec_uid, 191),
        "uid": _clean_text(_first(user, ["uid", "id"]), 64),
        "nickname": _clean_text(nickname, 255),
        "display_name": _clean_text(nickname, 255) or _clean_text(unique_id, 255) or _clean_text(sec_uid, 80),
        "unique_id": _clean_text(unique_id, 128),
        "signature": _clean_long_text(signature, 1000),
        "avatar_url": _avatar_url(user),
        "homepage_url": _clean_long_text(homepage_url, 4096),
        "follower_count": follower_count,
        "following_count": following_count,
        "aweme_count": aweme_count,
        "like_count": like_count,
        "verify_info": _clean_text(verify, 255),
        "raw_index": idx,
        "raw": _jsonable(item),
    }


def _normalize_douyin_users_from_payload(payload: Any, limit: int = 20) -> tuple[list[dict[str, Any]], int]:
    raw_items = _collect_items(payload or {})
    seen: set[str] = set()
    users: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_items[:50]):
        item = _normalize_douyin_user(raw, idx)
        if not item:
            continue
        sec_uid = item["sec_user_id"]
        if sec_uid in seen:
            continue
        seen.add(sec_uid)
        users.append(item)
        if len(users) >= limit:
            break
    return users, len(raw_items)


def _normalize_wechat_channels_user(raw: Any, idx: int) -> Optional[dict[str, Any]]:
    item = raw if isinstance(raw, dict) else {"value": raw}
    user = item.get("finder_info") if isinstance(item.get("finder_info"), dict) else {}
    if not user:
        user = item.get("finderUser") if isinstance(item.get("finderUser"), dict) else {}
    if not user:
        user = item.get("user_info") if isinstance(item.get("user_info"), dict) else {}
    if not user:
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
    if not user:
        nested = _lookup(item, "data.finder_info")
        user = nested if isinstance(nested, dict) else item

    username = _first(
        user,
        [
            "username",
            "finder_username",
            "finderUserName",
            "user_name",
            "jumpInfo.userName",
            "noticeParam.finderUsername",
            "openid",
            "encrypted_username",
            "finder_info.username",
        ],
    )
    if not username:
        username = _first(
            item,
            [
                "username",
                "finder_username",
                "finderUserName",
                "user_name",
                "jumpInfo.userName",
                "noticeParam.finderUsername",
                "data.username",
            ],
        )
    if not username:
        return None

    nickname = _first(user, ["nickname", "nick_name", "display_name", "name", "title"]) or _first(item, ["nickname", "nick_name", "display_name", "title"])
    signature = _first(user, ["signature", "desc", "description", "bio"]) or _first(item, ["signature", "desc"])
    avatar = _first(user, ["avatar_url", "avatar", "head_image_url", "headImgUrl", "avatarUrl", "thumbUrl", "thumb_url"]) or _avatar_url(user)
    if isinstance(avatar, dict):
        avatar = _first(avatar, ["url", "url_list.0", "uri"])
    follower_count = _first(user, ["follower_count", "fans_count", "fans_cnt", "follow_cnt"]) or _first(item, ["follower_count", "fans_count", "fans_cnt"])
    works_count = _first(user, ["feed_count", "video_count", "works_count", "publish_cnt"]) or _first(item, ["feed_count", "video_count", "works_count"])
    homepage_url = _first(user, ["homepage_url", "finder_info_export.url", "share_url"]) or _first(item, ["homepage_url", "finder_info_export.url", "share_url"])
    verify = _first(user, ["verify_info", "authInfo", "auth_info", "certification"]) or _first(item, ["verify_info", "authInfo", "auth_info", "certification"])
    nickname_text = _strip_search_markup(nickname, 255)
    signature_text = _strip_search_markup(signature, 1000)

    return {
        "id": _clean_text(username, 191),
        "username": _clean_text(username, 191),
        "finder_username": _clean_text(username, 191),
        "nickname": nickname_text,
        "display_name": nickname_text or _clean_text(username, 80),
        "signature": signature_text,
        "avatar_url": _clean_long_text(avatar, 4096),
        "homepage_url": _clean_long_text(homepage_url, 4096),
        "follower_count": follower_count,
        "aweme_count": works_count,
        "verify_info": _strip_search_markup(verify, 255),
        "raw_index": idx,
        "raw": _jsonable(item),
    }


def _normalize_wechat_channels_users_from_payload(payload: Any, limit: int = 20) -> tuple[list[dict[str, Any]], int]:
    raw_items = _collect_items(payload or {})
    seen: set[str] = set()
    users: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_items[:80]):
        item = _normalize_wechat_channels_user(raw, idx)
        if not item:
            continue
        username = item["username"]
        if username in seen:
            continue
        seen.add(username)
        users.append(item)
        if len(users) >= limit:
            break
    return users, len(raw_items)


def _persist_items(
    db: Session,
    *,
    user_id: int,
    query_id: str,
    platform: str,
    source_type: str,
    raw_items: list[Any],
    item_meta: Optional[dict[str, Any]] = None,
) -> list[TikHubSourceItem]:
    saved: list[TikHubSourceItem] = []
    for idx, raw in enumerate(raw_items):
        data = _normalize_item(raw, user_id=user_id, query_id=query_id, platform=platform, source_type=source_type, idx=idx)
        if item_meta:
            data["raw"] = _merge_source_raw(data["raw"], meta=item_meta)
        row = (
            db.query(TikHubSourceItem)
            .filter(
                TikHubSourceItem.user_id == user_id,
                TikHubSourceItem.platform == platform,
                TikHubSourceItem.source_type == source_type,
                TikHubSourceItem.item_key == data["item_key"],
            )
            .first()
        )
        if row is None:
            row = TikHubSourceItem(**data)
            db.add(row)
        else:
            row.query_id = query_id
            row.author_key = data["author_key"]
            row.author_name = data["author_name"]
            row.title = data["title"]
            row.description = data["description"]
            row.public_url = data["public_url"]
            row.cover_url = data["cover_url"]
            row.publish_time = data["publish_time"]
            row.metrics = data["metrics"]
            existing_meta = _source_meta(row)
            merged_meta = item_meta if item_meta else existing_meta
            row.raw = _merge_source_raw(data["raw"], meta=merged_meta if merged_meta else None, usage=_source_usage(row))
            row.is_new = False
            row.updated_at = _utcnow()
        saved.append(row)
    return saved


async def _call_tikhub(query_type: str, params: dict[str, Any], body: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, str], int]:
    spec = _ENDPOINTS.get(query_type)
    if not spec:
        raise HTTPException(status_code=400, detail=f"不支持的 TikHub 查询类型：{query_type}")
    url = _tikhub_api_base() + spec["path"]
    headers = {
        "Authorization": f"Bearer {_tikhub_api_key()}",
        "Accept": "application/json",
        "User-Agent": "lobster-server-tikhub-proxy/1.0",
    }
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        if spec["method"] == "POST":
            resp = await client.post(url, headers={**headers, "Content-Type": "application/json"}, json=body)
        else:
            resp = await client.get(url, headers=headers, params=params)
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        payload = resp.json()
    except Exception:
        payload = {"text": resp.text[:20000]}
    return resp.status_code, _jsonable(payload), dict(resp.headers), latency_ms


async def _execute_query_with_retry(
    *,
    db: Session,
    current_user: User,
    query_type: str,
    params: dict[str, Any],
    body: dict[str, Any],
    save_items: bool = True,
    meta: Optional[dict[str, Any]] = None,
    attempts: int = 3,
) -> dict[str, Any]:
    last_result: dict[str, Any] = {}
    attempts = max(1, int(attempts or 1))
    for idx in range(attempts):
        try:
            last_result = await _execute_query(
                db=db,
                current_user=current_user,
                query_type=query_type,
                params=params,
                body=body,
                save_items=save_items,
                meta={**(meta or {}), "attempt": idx + 1, "attempts": attempts},
            )
        except HTTPException as exc:
            if idx >= attempts - 1 or not _retryable_http_exception(exc):
                raise
            await asyncio.sleep(_retry_delay(idx))
            continue
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if idx >= attempts - 1:
                raise HTTPException(status_code=502, detail=f"TikHub 同步失败：{exc}") from exc
            await asyncio.sleep(_retry_delay(idx))
            continue
        if last_result.get("ok") and int(last_result.get("raw_item_count") or 0) > 0:
            return last_result
        query = last_result.get("query") if isinstance(last_result.get("query"), dict) else {}
        error_message = query.get("error_message") or last_result.get("error_message") or ""
        if int(query.get("http_status") or 0) not in _RETRY_HTTP_STATUSES and not _is_retryable_detail(error_message):
            return last_result
        if idx < attempts - 1:
            await asyncio.sleep(0.8 * (idx + 1))
    return last_result


async def _execute_query(
    *,
    db: Session,
    current_user: User,
    query_type: str,
    params: dict[str, Any],
    body: dict[str, Any],
    save_items: bool = True,
    meta: Optional[dict[str, Any]] = None,
    include_raw_response: bool = False,
) -> dict[str, Any]:
    spec = _ENDPOINTS.get(query_type)
    if not spec:
        raise HTTPException(status_code=400, detail=f"不支持的 TikHub 查询类型：{query_type}")
    clean_params = _clean_mapping(params, set(spec.get("allowed_params") or set()))
    clean_body = _clean_mapping(body, set(spec.get("allowed_body") or set()))
    price = _query_price(query_type)
    balance = user_balance_decimal(current_user)
    if price > 0 and balance < price:
        raise HTTPException(status_code=402, detail=f"算力不足：本次 TikHub 查询需 {credits_json_float(price)}，当前余额 {credits_json_float(balance)}。")

    query_id = uuid.uuid4().hex
    log = TikHubQueryLog(
        user_id=current_user.id,
        query_id=query_id,
        platform=spec["platform"],
        query_type=query_type,
        method=spec["method"],
        endpoint=spec["path"],
        request_params=clean_params,
        request_body=clean_body,
        status="pending",
        success=False,
        credits_charged=quantize_credits(0),
        meta=meta or {},
    )
    db.add(log)
    db.flush()

    try:
        http_status, payload, headers, latency_ms = await _call_tikhub(query_type, clean_params, clean_body)
    except HTTPException:
        raise
    except Exception as exc:
        log.status = "error"
        log.error_message = str(exc)[:2000]
        log.updated_at = _utcnow()
        db.commit()
        raise HTTPException(status_code=502, detail=f"TikHub 查询失败：{str(exc)[:200]}")

    code = _response_code(payload)
    success_flag = _response_success_flag(payload)
    ok_code = code is None or code in {0, 1, 200}
    success = 200 <= int(http_status) < 300 and ok_code and success_flag is not False
    raw_items = _collect_items(payload)
    saved_items: list[TikHubSourceItem] = []
    if success and save_items and raw_items:
        saved_items = _persist_items(
            db,
            user_id=current_user.id,
            query_id=query_id,
            platform=spec["platform"],
            source_type=spec["source_type"],
            raw_items=raw_items,
            item_meta=meta or {},
        )
        db.flush()

    log.http_status = int(http_status)
    log.tikhub_code = code
    log.tikhub_request_id = _response_request_id(payload, httpx.Headers(headers)) or None
    log.cache_url = _response_cache_url(payload) or None
    log.latency_ms = latency_ms
    log.result_count = len(raw_items)
    log.result_snapshot = {
        "items": [_item_payload(item) for item in saved_items[:20]],
        "raw_item_count": len(raw_items),
    }
    log.raw_response = payload
    log.success = bool(success)
    log.status = "success" if success else "upstream_error"
    if not success:
        log.error_message = _response_message(payload) or f"TikHub HTTP {http_status}"

    balance_after = balance
    if success and price > 0:
        balance_after = quantize_credits(balance - price)
        current_user.credits = balance_after
        log.credits_charged = price
        append_credit_ledger(
            db,
            current_user.id,
            -price,
            "unit_deduct",
            balance_after,
            description=f"TikHub 数据查询扣费：{query_type}",
            ref_type="tikhub_query",
            ref_id=query_id,
            meta={
                "source": "tikhub",
                "query_type": query_type,
                "platform": spec["platform"],
                "deduct_credits": credits_json_float(price),
                "result_count": len(raw_items),
            },
        )

    db.commit()
    db.refresh(log)
    for item in saved_items:
        db.refresh(item)

    result = {
        "ok": bool(success),
        "query": _query_log_payload(log, include_raw=False, items=saved_items[:50]),
        "items": [_item_payload(item) for item in saved_items[:50]],
        "raw_item_count": len(raw_items),
        "balance_after": credits_json_float(balance_after),
    }
    if include_raw_response:
        result["raw_response"] = payload
    return result


def _keyword_payload(row: IPContentKeyword) -> dict[str, Any]:
    return {
        "id": row.id,
        "keyword": row.keyword,
        "display_name": row.display_name or row.keyword,
        "status": row.status,
        "last_fetch_at": row.last_fetch_at.isoformat() if row.last_fetch_at else None,
        "meta": row.meta or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _draft_record_payload(row: IPContentDraftRecord) -> dict[str, Any]:
    meta = row.meta or {}
    images = meta.get("images") if isinstance(meta, dict) and isinstance(meta.get("images"), list) else []
    image_prompts = meta.get("image_prompts") if isinstance(meta, dict) and isinstance(meta.get("image_prompts"), list) else []
    return {
        "id": row.id,
        "record_id": row.record_id,
        "task": row.task,
        "platform": row.platform,
        "title": row.title or "",
        "body": row.content or "",
        "content": row.content or "",
        "image_prompt": row.image_prompt or "",
        "image_prompts": image_prompts,
        "image_url": row.image_url or "",
        "image_asset_id": row.image_asset_id or "",
        "images": images,
        "selected": bool(row.selected),
        "source_item_ids": row.source_item_ids or [],
        "memory_doc_ids": row.memory_doc_ids or [],
        "meta": meta,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _clean_int_ids(values: Any, limit: int = 30) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    raw_values = values if isinstance(values, list) else []
    for raw in raw_values:
        try:
            val = int(raw)
        except Exception:
            continue
        if val <= 0 or val in seen:
            continue
        seen.add(val)
        out.append(val)
        if len(out) >= limit:
            break
    return out


def _template_payload(row: IPContentScheduleTemplate) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "keyword_ids": _clean_int_ids(row.keyword_ids, 50),
        "competitor_ids": _clean_int_ids(row.competitor_ids, 50),
        "memory_docs": row.memory_docs or [],
        "requirements": row.requirements or {},
        "status": row.status,
        "meta": row.meta or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _validate_template_refs(db: Session, user_id: int, keyword_ids: list[int], competitor_ids: list[int]) -> tuple[list[int], list[int]]:
    clean_keyword_ids = _clean_int_ids(keyword_ids, 50)
    clean_competitor_ids = _clean_int_ids(competitor_ids, 50)
    if clean_keyword_ids:
        found = {
            int(x)
            for (x,) in db.query(IPContentKeyword.id)
            .filter(IPContentKeyword.user_id == user_id, IPContentKeyword.id.in_(clean_keyword_ids))
            .all()
        }
        missing = [x for x in clean_keyword_ids if x not in found]
        if missing:
            raise HTTPException(status_code=400, detail=f"关键词不存在或不属于当前用户：{missing[:5]}")
    if clean_competitor_ids:
        found = {
            int(x)
            for (x,) in db.query(ContentCompetitorAccount.id)
            .filter(ContentCompetitorAccount.user_id == user_id, ContentCompetitorAccount.id.in_(clean_competitor_ids))
            .all()
        }
        missing = [x for x in clean_competitor_ids if x not in found]
        if missing:
            raise HTTPException(status_code=400, detail=f"同行账号不存在或不属于当前用户：{missing[:5]}")
    return clean_keyword_ids, clean_competitor_ids


def _requirements_text(requirements: dict[str, Any], *keys: str) -> str:
    if not isinstance(requirements, dict):
        return ""
    values: list[str] = []
    for key in keys:
        text = _clean_long_text(requirements.get(key), 2000)
        if text:
            values.append(text)
    return "\n".join(values).strip()


def _server_bearer_for_user(user: User) -> str:
    token = create_access_token(access_token_claims(user), expires_delta=timedelta(minutes=30))
    return f"Bearer {token}"


async def _sync_keyword_row(
    *,
    db: Session,
    current_user: User,
    row: IPContentKeyword,
    page_size: int = 20,
    date_window: int = 24,
) -> dict[str, Any]:
    result = await _execute_query_with_retry(
        db=db,
        current_user=current_user,
        query_type="douyin_search_video_v2",
        params={},
        body={
            "keyword": row.keyword,
            "cursor": 0,
            "sort_type": "0",
            "publish_time": "0",
            "filter_duration": "0",
            "content_type": "0",
            "search_id": "",
            "backtrace": "",
        },
        save_items=True,
        meta={"source": "keyword_video_sync", "keyword_id": row.id, "keyword": row.keyword},
        attempts=3,
    )
    video_result = result
    if not result.get("ok") or int(result.get("raw_item_count") or 0) <= 0:
        result = await _execute_query_with_retry(
            db=db,
            current_user=current_user,
            query_type="douyin_billboard_search",
            params={},
            body={"page_num": 1, "page_size": page_size, "date_window": date_window, "keyword": row.keyword, "tags": []},
            save_items=True,
            meta={"source": "keyword_sync_fallback", "keyword_id": row.id, "keyword": row.keyword},
            attempts=3,
        )
        result["video_detail_status"] = {
            "ok": bool(video_result.get("ok")),
            "raw_item_count": int(video_result.get("raw_item_count") or 0),
            "error_message": ((video_result.get("query") or {}).get("error_message") or ""),
            "query_id": ((video_result.get("query") or {}).get("query_id") or ""),
        }
    row.last_fetch_at = _utcnow()
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    result["keyword"] = _keyword_payload(row)
    return result


async def _sync_competitor_row(
    *,
    db: Session,
    current_user: User,
    row: ContentCompetitorAccount,
    count: int = 20,
    last_buffer: str = "",
) -> dict[str, Any]:
    if row.platform == "douyin":
        result = await _execute_query_with_retry(
            db=db,
            current_user=current_user,
            query_type="douyin_user_posts",
            params={"sec_user_id": row.account_key, "count": count, "sort_type": 0},
            body={},
            save_items=True,
            meta={"source": "competitor_sync", "competitor_account_id": row.id, "competitor_name": row.display_name or row.account_key},
            attempts=3,
        )
    elif row.platform == "wechat_channels":
        body = {"username": row.account_key}
        if last_buffer:
            body["last_buffer"] = last_buffer
        result = await _execute_query_with_retry(
            db=db,
            current_user=current_user,
            query_type="wechat_channels_home_page",
            params={},
            body=body,
            save_items=True,
            meta={"source": "competitor_sync", "competitor_account_id": row.id, "competitor_name": row.display_name or row.account_key},
            attempts=3,
        )
    else:
        raise HTTPException(status_code=400, detail="不支持的平台")
    if result.get("ok"):
        row.last_fetch_at = _utcnow()
    first_item = (result.get("items") or [{}])[0] if isinstance(result.get("items"), list) else {}
    if isinstance(first_item, dict) and first_item.get("item_key"):
        row.last_seen_item_key = _clean_text(first_item.get("item_key"), 191)
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    result["competitor"] = _competitor_payload(row)
    return result


def _select_keyword_source_rows(db: Session, user_id: int, keyword_ids: list[int], *, task: str, limit: int = 40) -> list[TikHubSourceItem]:
    source_rank = case(
        (TikHubSourceItem.source_type == "keyword_video", 0),
        (TikHubSourceItem.source_type == "billboard_video", 1),
        (TikHubSourceItem.source_type == "billboard_topic", 2),
        (TikHubSourceItem.source_type == "billboard_search", 3),
        else_=4,
    )
    rows = (
        db.query(TikHubSourceItem)
        .filter(TikHubSourceItem.user_id == user_id, TikHubSourceItem.platform == "douyin")
        .filter(TikHubSourceItem.source_type.in_(["keyword_video", "billboard_search", "billboard_topic", "billboard_video", "hot_search", "hot_total"]))
        .order_by(TikHubSourceItem.is_new.desc(), TikHubSourceItem.created_at.desc(), source_rank.asc(), TikHubSourceItem.id.desc())
        .limit(300)
        .all()
    )
    wanted = {int(x) for x in keyword_ids if str(x).isdigit()}
    buckets: dict[int, dict[str, list[TikHubSourceItem]]] = {}
    fallback_bucket = 0
    for row in rows:
        meta = _source_meta(row)
        keyword_id = int(meta.get("keyword_id") or 0)
        if wanted and keyword_id not in wanted:
            continue
        source_name = str(meta.get("source") or "")
        if source_name and source_name not in {"keyword_sync", "keyword_video_sync", "keyword_sync_fallback"}:
            continue
        bucket_id = keyword_id or fallback_bucket
        bucket = buckets.setdefault(bucket_id, {"fresh": [], "reused": []})
        if _source_used_for(row, task):
            bucket["reused"].append(row)
            continue
        bucket["fresh"].append(row)

    selected: list[TikHubSourceItem] = []
    seen: set[int] = set()

    def row_sort_value(row: TikHubSourceItem) -> float:
        dt = row.created_at or row.updated_at
        try:
            return float(dt.timestamp())
        except Exception:
            return 0.0

    def take_round(kind: str) -> None:
        nonlocal selected
        max_len = max((len(v[kind]) for v in buckets.values()), default=0)
        ordered_bucket_ids = sorted(
            buckets,
            key=lambda bid: (
                0 if bid in wanted else 1,
                -row_sort_value(buckets[bid][kind][0]) if buckets[bid][kind] else 0,
                bid,
            ),
        )
        for idx in range(max_len):
            for bucket_id in ordered_bucket_ids:
                bucket_rows = buckets[bucket_id][kind]
                if idx >= len(bucket_rows):
                    continue
                row = bucket_rows[idx]
                if row.id in seen:
                    continue
                seen.add(row.id)
                selected.append(row)
                if len(selected) >= limit:
                    return

    take_round("fresh")
    if len(selected) < limit:
        take_round("reused")
    return selected[:limit]


def _select_competitor_source_rows(db: Session, user_id: int, competitor_ids: list[int], *, task: str, limit: int = 40) -> list[TikHubSourceItem]:
    rows = (
        db.query(TikHubSourceItem)
        .filter(TikHubSourceItem.user_id == user_id, TikHubSourceItem.source_type.in_(["user_post", "home_page"]))
        .order_by(TikHubSourceItem.is_new.desc(), TikHubSourceItem.created_at.desc(), TikHubSourceItem.id.desc())
        .limit(300)
        .all()
    )
    wanted = {int(x) for x in competitor_ids if str(x).isdigit()}
    out: list[TikHubSourceItem] = []
    for row in rows:
        meta = _source_meta(row)
        if wanted and int(meta.get("competitor_account_id") or 0) not in wanted:
            continue
        source_name = str(meta.get("source") or "")
        if source_name and source_name != "competitor_sync":
            continue
        if _source_used_for(row, task):
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _keyword_seed_briefs(keywords: list[IPContentKeyword]) -> list[dict[str, Any]]:
    briefs: list[dict[str, Any]] = []
    for row in keywords:
        keyword = _clean_text(row.keyword, 191)
        display_name = _clean_text(row.display_name, 191) or keyword
        description = f"模板关键词：{keyword}"
        if display_name and display_name != keyword:
            description += f"；展示名：{display_name}"
        briefs.append(
            {
                "type": "template_keyword",
                "title": display_name or keyword,
                "description": description,
                "author": "",
            }
        )
    return briefs


def _competitor_seed_briefs(competitors: list[ContentCompetitorAccount]) -> list[dict[str, Any]]:
    briefs: list[dict[str, Any]] = []
    for row in competitors:
        parts = [
            f"平台：{_clean_text(row.platform, 64)}",
            f"账号：{_clean_text(row.display_name or row.account_key, 191)}",
        ]
        if row.industry_tags:
            parts.append(f"行业标签：{_clean_text(row.industry_tags, 500)}")
        signature = ""
        if isinstance(row.meta, dict):
            signature = _clean_long_text(row.meta.get("signature") or row.meta.get("verify_info") or "", 500)
        if signature:
            parts.append(f"账号简介：{signature}")
        briefs.append(
            {
                "type": "template_competitor",
                "title": _clean_text(row.display_name or row.account_key, 191),
                "description": "；".join([p for p in parts if p]),
                "author": _clean_text(row.account_key, 191),
            }
        )
    return briefs


async def _post_llm_with_retry(
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    attempts: int = 3,
) -> dict[str, Any]:
    attempts = max(1, int(attempts or 1))
    last_detail = ""
    for idx in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=150.0, trust_env=False) as client:
                resp = await client.post(f"{_internal_api_base()}/api/sutui-chat/completions", json=payload, headers=headers)
            try:
                data = resp.json() if resp.content else {}
            except Exception:
                data = {"text": resp.text[:20000]}
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_detail = str(exc)
            if idx >= attempts - 1:
                raise HTTPException(status_code=502, detail=f"文案生成失败：{last_detail}") from exc
            await asyncio.sleep(_retry_delay(idx))
            continue
        if resp.status_code < 400:
            return data
        detail = _response_message(data) or _clean_long_text(data, 800) or f"HTTP {resp.status_code}"
        last_detail = detail
        if resp.status_code not in _RETRY_HTTP_STATUSES and not _is_retryable_detail(detail):
            raise HTTPException(status_code=resp.status_code, detail=f"文案生成失败：{detail}")
        if idx >= attempts - 1:
            raise HTTPException(status_code=resp.status_code, detail=f"文案生成失败：{detail}")
        await asyncio.sleep(_retry_delay(idx))
    raise HTTPException(status_code=502, detail=f"文案生成失败：{last_detail or '上游无响应'}")


async def _call_ip_content_llm(
    *,
    request: Optional[Request] = None,
    auth_token: str = "",
    installation_id: str = "",
    task: str,
    platform: str,
    count: int,
    rows: list[TikHubSourceItem],
    memories: list[dict[str, Any]],
    extra_requirements: str,
    fallback_sources: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    count = max(1, min(int(count or 5), 20))
    fallback_sources = fallback_sources or []
    if not rows and not fallback_sources and not any((m.get("title") or m.get("content")) for m in memories):
        raise HTTPException(status_code=400, detail="请先同步关键词/同行数据，或选择至少一份记忆资料。")
    task_requirements = _draft_requirements(task, platform, count)
    source_briefs = [_item_brief(row, idx + 1) for idx, row in enumerate(rows[:30])]
    if not source_briefs and fallback_sources:
        for idx, item in enumerate(fallback_sources[:30], start=1):
            source_briefs.append(
                {
                    "idx": idx,
                    "type": item.get("type") or "template_seed",
                    "title": item.get("title") or "",
                    "description": item.get("description") or "",
                    "author": item.get("author") or "",
                    "publish_time": "",
                    "metrics": {},
                    "url": "",
                }
            )
    current_year = _utcnow().year
    memory_payload = [
        {"title": m.get("title") or "", "content": (m.get("content") or "")[:1800]}
        for m in memories
        if m.get("content") or m.get("title")
    ]
    wants_moments = _is_moments_task(task)
    wants_oral = _is_oral_task(task)
    json_schema = (
        "{\"items\":[{\"title\":\"\",\"hook\":\"\",\"body\":\"\",\"cta\":\"\",\"image_prompt\":\"\",\"image_prompts\":[\"\",\"\",\"\"]}]}"
        if wants_moments
        else "{\"items\":[{\"title\":\"\",\"hook\":\"\",\"body\":\"\",\"cta\":\"\"}]}"
    )
    format_rule = (
        "当前任务是朋友圈文案：body 只能写朋友圈正文，配图提示必须放到 image_prompt/image_prompts；"
        "严禁在朋友圈正文里写“评论区告诉我、留言告诉我、你觉得呢”等引导评论句。"
        "body 必须保留换行排版，像微信群/朋友圈可直接复制发布的短句爆款体：开场强、痛点快、结果明确、收口有动作；"
        "可以使用 Emoji，但要服务于阅读节奏和情绪，不要变成装饰堆砌。"
        if wants_moments
        else "当前任务是口播文案：只返回标题、开场钩子、口播正文和收口；严禁返回 image_prompt、image_prompts、配图提示、画面建议。"
    )
    system_prompt = (
        "你是中文短视频、朋友圈和个人专业 IP 内容策划。根据给定的数据源和记忆资料生成可审核、可直接发布的草稿。"
        "必须返回严格 JSON，不要 Markdown 代码块。格式："
        f"{json_schema}。"
        f"{format_rule}"
        "不要抄袭同行原文，不要编造资料里没有的硬数据；可以提炼热点结构、选题角度和表达策略。"
        "\ncore rule: 记忆资料是账号定位、行业事实、产品服务、专业判断和表达风格的底座；"
        "TikHub 的行业热门/同行作品是当前新选题和新内容来源。"
        "当 tikhub_sources 存在时，口播和朋友圈文案都必须先基于这些新数据提炼选题，"
        "再用 memory_docs 约束事实、专业口径、案例风格和表达边界，不能脱离记忆写成泛泛内容。"
        "当 tikhub_sources 不足以生成指定条数时，才用 memory_docs 补足数量，并保持事实克制。"
        "行业热门口播优先使用关键词/榜单数据；专业 IP 口播优先使用同行新作品；朋友圈文案也要优先承接最新数据里的选题或场景。"
        f"所有涉及年份的内容默认按当前年份 {current_year} 年表达；除非数据源原文明确提供其他年份，严禁默认写 2025 年。"
        "口播类文案暂不限制字数和时长，要写出深度、案例、具体场景和判断链路。"
        "口播类文案允许使用评论互动收口；朋友圈文案不要使用引导评论的表达。"
        "行业热门口播如果有多条关键词同步数据，必须主动做方向分散：不同条目对应不同热词、痛点、场景或案例，不能五条围绕同一个方向重复改写。"
        "只有生成朋友圈文案时，才需要返回 image_prompt/image_prompts；每条 items 必须额外返回 image_prompts 数组，数组内 3 条配图文案都要贴合同一条 body 的主题，但创意、主体、场景和表达隐喻必须明显不同。"
        "朋友圈 body 的排版必须直接可复制发布：短句、多换行、段落留白、重点句可用 Emoji 强化；不要输出一整段连续长文本。"
        "\n"
    )
    user_prompt = json.dumps(
        {
            "task": task,
            "requirements": task_requirements,
            "extra_requirements": _clean_long_text(extra_requirements, 4000),
            "count": count,
            "platform": platform,
            "current_year": current_year,
            "tikhub_sources": source_briefs,
            "memory_docs": memory_payload,
            "source_usage_rule": (
                "有 tikhub_sources 时，新数据是选题和表达角度的第一来源；每条草稿都应尽量对应一个热点、同行作品、热词或榜单条目。"
                "memory_docs 用于让 AI 更懂账号和业务：约束事实、口径、专业度、产品服务特点和表达风格。"
                "不能只照着记忆写，也不能只追热点而脱离记忆。"
            ),
            "format_rule": format_rule,
            "fallback_rule": "如果未使用过的新数据不足，才用记忆资料补足指定条数；补足内容也要延续账号定位和业务事实。",
        },
        ensure_ascii=False,
    )
    token = _clean_text(auth_token, 4096)
    if request is not None:
        token = token or (request.headers.get("Authorization") or request.headers.get("authorization") or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="缺少登录凭证")
    if not token.lower().startswith("bearer "):
        token = f"Bearer {token}"
    headers = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": token}
    xi = _clean_text(installation_id, 128)
    if request is not None:
        xi = xi or (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    if xi:
        headers["X-Installation-Id"] = xi
    payload = {
        "model": (os.environ.get("IP_CONTENT_STUDIO_MODEL") or "deepseek-chat").strip() or "deepseek-chat",
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        "stream": False,
        "temperature": 0.76,
    }
    data = await _post_llm_with_retry(payload=payload, headers=headers, attempts=3)
    try:
        text = str(data["choices"][0]["message"]["content"] or "")
    except Exception:
        text = json.dumps(data, ensure_ascii=False)
    drafts = _normalize_drafts(_extract_json_object(text), text, count)
    if wants_oral:
        for draft in drafts:
            draft["image_prompt"] = ""
            draft["image_prompts"] = []
    elif wants_moments:
        for draft in drafts:
            draft["body"] = _strip_moments_comment_bait(_strip_embedded_image_prompt(draft.get("body") or "", 6000), 6000)
    return {
        "requirements": task_requirements,
        "drafts": drafts,
        "source_items": [_item_payload(row) for row in rows],
        "memory_docs": memories,
        "extra_requirements": _clean_long_text(extra_requirements, 4000),
    }


def _save_draft_records(
    db: Session,
    *,
    current_user: User,
    task: str,
    platform: str,
    drafts: list[dict[str, str]],
    rows: list[TikHubSourceItem],
    memories: list[dict[str, Any]],
    extra_requirements: str,
    group_id: str,
) -> list[IPContentDraftRecord]:
    source_ids = [int(row.id) for row in rows]
    memory_ids = [m.get("id") for m in memories if m.get("id")]
    saved: list[IPContentDraftRecord] = []
    is_moments = _is_moments_task(task)
    is_oral = _is_oral_task(task)
    for draft in drafts:
        image_prompts = [p for p in (draft.get("image_prompts") or []) if isinstance(p, str) and p.strip()]
        image_prompt = _clean_long_text(draft.get("image_prompt"), 2000) or (image_prompts[0] if image_prompts else None)
        content = _clean_long_text(draft.get("body") or draft.get("content"), 8000) or None
        if is_oral:
            image_prompts = []
            image_prompt = None
        elif is_moments:
            content = _strip_moments_comment_bait(_strip_embedded_image_prompt(content or "", 8000), 8000) or content
        rec = IPContentDraftRecord(
            record_id=uuid.uuid4().hex,
            user_id=current_user.id,
            task=task,
            platform=platform or "douyin",
            title=_clean_long_text(draft.get("title"), 1000) or None,
            content=content,
            image_prompt=image_prompt,
            source_item_ids=source_ids,
            memory_doc_ids=memory_ids,
            meta={"group_id": group_id, "extra_requirements": _clean_long_text(extra_requirements, 4000), "image_prompts": image_prompts[:3]},
        )
        db.add(rec)
        saved.append(rec)
    _mark_source_rows_used(db, rows, task=task, record_id=group_id)
    db.commit()
    for rec in saved:
        db.refresh(rec)
    return saved


async def run_ip_content_daily_scheduled(
    *,
    db: Session,
    current_user: User,
    options: dict[str, Any],
    run_id: str = "",
) -> dict[str, Any]:
    opts = ScheduledDailyRunOptions(**(options or {}))
    template_payload: dict[str, Any] = {}
    template: Optional[IPContentScheduleTemplate] = None
    keyword_ids = _clean_int_ids(opts.keyword_ids, 50)
    competitor_ids = _clean_int_ids(opts.competitor_ids, 50)
    requirements = dict(opts.requirements or {})
    memory_docs_raw = list(opts.memory_docs or [])

    if opts.template_id:
        template = (
            db.query(IPContentScheduleTemplate)
            .filter(
                IPContentScheduleTemplate.user_id == current_user.id,
                IPContentScheduleTemplate.id == int(opts.template_id),
                IPContentScheduleTemplate.status == "active",
            )
            .first()
        )
        if template is None:
            raise HTTPException(status_code=404, detail="IP 日更模板不存在")
        template_payload = _template_payload(template)
        if not keyword_ids:
            keyword_ids = _clean_int_ids(template.keyword_ids, 50)
        if not competitor_ids:
            competitor_ids = _clean_int_ids(template.competitor_ids, 50)
        merged_requirements = dict(template.requirements or {})
        merged_requirements.update({k: v for k, v in requirements.items() if _clean_long_text(v, 1)})
        requirements = merged_requirements
        if not memory_docs_raw:
            memory_docs_raw = list(template.memory_docs or [])

    keyword_ids, competitor_ids = _validate_template_refs(db, current_user.id, keyword_ids, competitor_ids)
    keywords = (
        db.query(IPContentKeyword)
        .filter(IPContentKeyword.user_id == current_user.id, IPContentKeyword.status == "active")
        .filter(IPContentKeyword.id.in_(keyword_ids))
        .order_by(IPContentKeyword.created_at.desc(), IPContentKeyword.id.desc())
        .all()
        if keyword_ids
        else []
    )
    competitors = (
        db.query(ContentCompetitorAccount)
        .filter(ContentCompetitorAccount.user_id == current_user.id, ContentCompetitorAccount.status == "active")
        .filter(ContentCompetitorAccount.id.in_(competitor_ids))
        .order_by(ContentCompetitorAccount.created_at.desc(), ContentCompetitorAccount.id.desc())
        .all()
        if competitor_ids
        else []
    )
    memories = _memory_payload_from_docs(memory_docs_raw, content_limit=3200)
    if not keywords and not competitors and not memories:
        raise HTTPException(status_code=400, detail="请选择关键词、同行账号模板或记忆资料")

    sync_results: list[dict[str, Any]] = []
    if opts.sync_before:
        for row in keywords:
            try:
                sync_results.append(await _sync_keyword_row(db=db, current_user=current_user, row=row, page_size=20, date_window=24))
            except Exception as exc:
                sync_results.append(_sync_error_result(source="keyword_sync", row=row, exc=exc, attempts=3))
        for row in competitors:
            try:
                sync_results.append(await _sync_competitor_row(db=db, current_user=current_user, row=row, count=20))
            except Exception as exc:
                sync_results.append(_sync_error_result(source="competitor_sync", row=row, exc=exc, attempts=3))

    auth_token = _server_bearer_for_user(current_user)
    generated_groups: list[dict[str, Any]] = []

    async def generate_group(
        *,
        task_key: str,
        record_task: str,
        platform: str,
        rows: list[TikHubSourceItem],
        fallback_sources: list[dict[str, Any]],
        count: int,
        extra: str,
    ) -> None:
        group_id = uuid.uuid4().hex
        generated = await _call_ip_content_llm(
            auth_token=auth_token,
            installation_id=f"server-scheduled-{run_id or 'ip-content'}",
            task=task_key,
            platform=platform,
            count=count,
            rows=rows,
            memories=memories,
            extra_requirements=extra,
            fallback_sources=fallback_sources,
        )
        records = _save_draft_records(
            db,
            current_user=current_user,
            task=record_task,
            platform=platform,
            drafts=generated["drafts"],
            rows=rows,
            memories=memories,
            extra_requirements=extra,
            group_id=group_id,
        )
        generated_groups.append(
            {
                "task": record_task,
                "group_id": group_id,
                "count": len(records),
                "requirements": generated.get("requirements") or "",
                "records": [_draft_record_payload(row) for row in records],
                "source_items": generated.get("source_items") or [],
            }
        )

    industry_rows = _select_keyword_source_rows(db, current_user.id, keyword_ids, task="industry_hot_oral", limit=40)
    ip_rows = _select_competitor_source_rows(db, current_user.id, competitor_ids, task="professional_ip_oral", limit=40)
    moment_rows = _select_keyword_source_rows(db, current_user.id, keyword_ids, task="moments_candidate", limit=24)
    moment_rows.extend(_select_competitor_source_rows(db, current_user.id, competitor_ids, task="moments_candidate", limit=24))
    seen: set[int] = set()
    moment_rows = [row for row in moment_rows if not (row.id in seen or seen.add(row.id))][:40]
    keyword_fallback_sources = _keyword_seed_briefs(keywords)
    competitor_fallback_sources = _competitor_seed_briefs(competitors)
    moment_fallback_sources = (keyword_fallback_sources + competitor_fallback_sources)[:40]

    await generate_group(
        task_key="task1_industry",
        record_task="industry_hot_oral",
        platform="douyin",
        rows=industry_rows,
        fallback_sources=keyword_fallback_sources,
        count=min(max(int(opts.industry_count or 5), 1), 5),
        extra=_requirements_text(requirements, "oral", "industry_oral", "industry", "common"),
    )
    await generate_group(
        task_key="task1_ip",
        record_task="professional_ip_oral",
        platform="douyin",
        rows=ip_rows,
        fallback_sources=competitor_fallback_sources,
        count=min(max(int(opts.ip_count or 5), 1), 5),
        extra=_requirements_text(requirements, "oral", "ip_oral", "professional_ip", "common"),
    )
    await generate_group(
        task_key="task2_moments",
        record_task="moments_candidate",
        platform="wechat_moments",
        rows=moment_rows,
        fallback_sources=moment_fallback_sources,
        count=min(max(int(opts.moments_count or 20), 1), 20),
        extra=_requirements_text(requirements, "moments", "moments_copy", "image", "common"),
    )

    records_by_task = {group["task"]: group["records"] for group in generated_groups}
    return {
        "ok": True,
        "ip_content_daily": True,
        "template": template_payload,
        "keyword_ids": keyword_ids,
        "competitor_ids": competitor_ids,
        "memory_docs": memories,
        "requirements": requirements,
        "sync_results": sync_results,
        "groups": generated_groups,
        "records_by_task": records_by_task,
        "image_generation": {
            "manual": True,
            "note": "朋友圈图片不由定时任务自动生成；请在执行详情或 IP 日更工作台手动触发。",
        },
    }


@router.post("/api/ip-content/tikhub/query", summary="服务器代理 TikHub 查询，并记录结果与扣费")
async def query_tikhub(
    body: TikHubQueryBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _execute_query(
        db=db,
        current_user=current_user,
        query_type=(body.query_type or "").strip(),
        params=body.params or {},
        body=body.body or {},
        save_items=bool(body.save_items),
    )


@router.get("/api/ip-content/tikhub/records", summary="当前用户 TikHub 查询记录")
def list_my_tikhub_records(
    platform: str = "",
    query_type: str = "",
    q: str = "",
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(TikHubQueryLog).filter(TikHubQueryLog.user_id == current_user.id)
    if platform.strip():
        query = query.filter(TikHubQueryLog.platform == platform.strip())
    if query_type.strip():
        query = query.filter(TikHubQueryLog.query_type == query_type.strip())
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            or_(
                TikHubQueryLog.query_id.ilike(like),
                TikHubQueryLog.query_type.ilike(like),
                TikHubQueryLog.error_message.ilike(like),
            )
        )
    total = query.with_entities(func.count(TikHubQueryLog.id)).scalar() or 0
    rows = query.order_by(TikHubQueryLog.created_at.desc(), TikHubQueryLog.id.desc()).offset(offset).limit(limit).all()
    return {
        "items": [_query_log_payload(row) for row in rows],
        "pagination": {"total": int(total), "limit": int(limit), "offset": int(offset), "has_next": offset + limit < int(total)},
    }


@router.get("/api/ip-content/tikhub/records/{query_id}", summary="当前用户 TikHub 查询详情")
def get_my_tikhub_record(
    query_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(TikHubQueryLog).filter(TikHubQueryLog.user_id == current_user.id, TikHubQueryLog.query_id == query_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="查询记录不存在")
    items = (
        db.query(TikHubSourceItem)
        .filter(TikHubSourceItem.user_id == current_user.id, TikHubSourceItem.query_id == query_id)
        .order_by(TikHubSourceItem.created_at.desc(), TikHubSourceItem.id.desc())
        .limit(200)
        .all()
    )
    return _query_log_payload(row, include_raw=True, items=items)


@router.get("/api/ip-content/source-items", summary="当前用户已入库的数据源条目")
def list_my_source_items(
    platform: str = "",
    source_type: str = "",
    q: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(TikHubSourceItem).filter(TikHubSourceItem.user_id == current_user.id)
    if platform.strip():
        query = query.filter(TikHubSourceItem.platform == platform.strip())
    source_type_clean = source_type.strip()
    if source_type_clean in {"keyword", "billboard_search"}:
        query = query.filter(TikHubSourceItem.source_type.in_(["keyword_video", "billboard_search", "billboard_topic", "billboard_video", "hot_search", "hot_total"]))
    elif source_type_clean in {"user_post", "competitor"}:
        query = query.filter(TikHubSourceItem.source_type.in_(["user_post", "home_page"]))
    elif source_type_clean:
        query = query.filter(TikHubSourceItem.source_type == source_type_clean)
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            or_(
                TikHubSourceItem.title.ilike(like),
                TikHubSourceItem.description.ilike(like),
                TikHubSourceItem.author_name.ilike(like),
                TikHubSourceItem.item_key.ilike(like),
            )
        )
    total = query.with_entities(func.count(TikHubSourceItem.id)).scalar() or 0
    source_rank = case(
        (TikHubSourceItem.source_type == "keyword_video", 0),
        (TikHubSourceItem.source_type == "billboard_video", 1),
        (TikHubSourceItem.source_type == "billboard_topic", 2),
        (TikHubSourceItem.source_type == "billboard_search", 3),
        else_=4,
    )
    rows = query.order_by(source_rank.asc(), TikHubSourceItem.created_at.desc(), TikHubSourceItem.id.desc()).offset(offset).limit(limit).all()
    return {
        "items": [_item_payload(row) for row in rows],
        "pagination": {"total": int(total), "limit": int(limit), "offset": int(offset), "has_next": offset + limit < int(total)},
    }


@router.get("/api/ip-content/competitors", summary="同行账号列表")
def list_competitors(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ContentCompetitorAccount)
        .filter(ContentCompetitorAccount.user_id == current_user.id)
        .order_by(ContentCompetitorAccount.created_at.desc(), ContentCompetitorAccount.id.desc())
        .all()
    )
    return {"items": [_competitor_payload(row) for row in rows]}


@router.get("/api/ip-content/schedule-templates", summary="IP 日更定时任务模板列表")
def list_schedule_templates(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(IPContentScheduleTemplate)
        .filter(IPContentScheduleTemplate.user_id == current_user.id, IPContentScheduleTemplate.status == "active")
        .order_by(IPContentScheduleTemplate.updated_at.desc(), IPContentScheduleTemplate.id.desc())
        .all()
    )
    return {"items": [_template_payload(row) for row in rows]}


@router.post("/api/ip-content/schedule-templates", summary="保存 IP 日更定时任务模板")
def save_schedule_template(
    body: ScheduleTemplateBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    name = _clean_text(body.name, 160)
    if not name:
        raise HTTPException(status_code=400, detail="请填写模板名称")
    keyword_ids, competitor_ids = _validate_template_refs(db, current_user.id, body.keyword_ids, body.competitor_ids)
    row = IPContentScheduleTemplate(
        user_id=current_user.id,
        name=name,
        keyword_ids=keyword_ids,
        competitor_ids=competitor_ids,
        memory_docs=_memory_payload_from_docs(body.memory_docs, content_limit=3200),
        requirements=_jsonable(body.requirements or {}),
        meta=_jsonable(body.meta or {}),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名模板已经存在")
    db.refresh(row)
    return {"ok": True, "item": _template_payload(row)}


@router.patch("/api/ip-content/schedule-templates/{template_id}", summary="更新 IP 日更定时任务模板")
def update_schedule_template(
    template_id: int,
    body: ScheduleTemplateBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(IPContentScheduleTemplate)
        .filter(IPContentScheduleTemplate.user_id == current_user.id, IPContentScheduleTemplate.id == template_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    name = _clean_text(body.name, 160)
    if not name:
        raise HTTPException(status_code=400, detail="请填写模板名称")
    keyword_ids, competitor_ids = _validate_template_refs(db, current_user.id, body.keyword_ids, body.competitor_ids)
    row.name = name
    row.keyword_ids = keyword_ids
    row.competitor_ids = competitor_ids
    row.memory_docs = _memory_payload_from_docs(body.memory_docs, content_limit=3200)
    row.requirements = _jsonable(body.requirements or {})
    row.meta = _jsonable(body.meta or {})
    row.status = "active"
    row.updated_at = _utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名模板已经存在")
    db.refresh(row)
    return {"ok": True, "item": _template_payload(row)}


@router.delete("/api/ip-content/schedule-templates/{template_id}", summary="删除 IP 日更定时任务模板")
def delete_schedule_template(
    template_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(IPContentScheduleTemplate)
        .filter(IPContentScheduleTemplate.user_id == current_user.id, IPContentScheduleTemplate.id == template_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    row.status = "deleted"
    row.updated_at = _utcnow()
    db.commit()
    return {"ok": True}


@router.get("/api/ip-content/douyin/users/search", summary="按昵称或抖音号搜索抖音用户候选")
async def search_douyin_users(
    q: str = Query("", max_length=80),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    keyword = _clean_text(q, 80)
    if not keyword:
        raise HTTPException(status_code=400, detail="请填写抖音昵称或抖音号")
    result = await _execute_query(
        db=db,
        current_user=current_user,
        query_type="douyin_search_user_v2",
        params={},
        body={"keyword": keyword, "cursor": 0},
        save_items=False,
        meta={"source": "competitor_user_search", "keyword": keyword},
        include_raw_response=True,
    )
    users, raw_count = _normalize_douyin_users_from_payload(result.get("raw_response") or {})
    return {
        "ok": bool(result.get("ok")),
        "items": users,
        "raw_item_count": raw_count,
        "query": result.get("query") or {},
        "balance_after": result.get("balance_after"),
    }


@router.get("/api/ip-content/wechat-channels/users/search", summary="按昵称搜索视频号用户候选")
async def search_wechat_channels_users(
    q: str = Query("", max_length=80),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    keyword = _clean_text(q, 80)
    if not keyword:
        raise HTTPException(status_code=400, detail="请填写视频号昵称或 username")
    result = await _execute_query(
        db=db,
        current_user=current_user,
        query_type="wechat_channels_user_search_v2",
        params={"keywords": keyword, "page": 0},
        body={},
        save_items=False,
        meta={"source": "competitor_user_search", "keyword": keyword},
        include_raw_response=True,
    )
    users, raw_count = _normalize_wechat_channels_users_from_payload(result.get("raw_response") or {})
    return {
        "ok": bool(result.get("ok")),
        "items": users,
        "raw_item_count": raw_count,
        "query": result.get("query") or {},
        "balance_after": result.get("balance_after"),
    }


@router.post("/api/ip-content/competitors", summary="新增同行账号")
def add_competitor(
    body: CompetitorCreateBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    platform = body.platform.strip()
    if platform not in {"douyin", "wechat_channels"}:
        raise HTTPException(status_code=400, detail="platform 仅支持 douyin / wechat_channels")
    account_key = _clean_text(body.account_key, 191)
    if not account_key:
        raise HTTPException(status_code=400, detail="请填写同行账号标识")
    row = ContentCompetitorAccount(
        user_id=current_user.id,
        platform=platform,
        account_key=account_key,
        display_name=_clean_text(body.display_name, 255) or account_key,
        homepage_url=_clean_long_text(body.homepage_url, 4096) or None,
        industry_tags=_clean_long_text(body.industry_tags, 2000) or None,
        meta=_jsonable(body.meta or {}),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="该同行账号已经存在")
    db.refresh(row)
    return {"ok": True, "item": _competitor_payload(row)}


@router.delete("/api/ip-content/competitors/{account_id}", summary="删除同行账号")
def delete_competitor(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(ContentCompetitorAccount).filter(ContentCompetitorAccount.user_id == current_user.id, ContentCompetitorAccount.id == account_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="同行账号不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/api/ip-content/competitors/{account_id}/sync", summary="同步同行账号最新作品")
async def sync_competitor(
    account_id: int,
    body: CompetitorSyncBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(ContentCompetitorAccount).filter(ContentCompetitorAccount.user_id == current_user.id, ContentCompetitorAccount.id == account_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="同行账号不存在")
    return await _sync_competitor_row(db=db, current_user=current_user, row=row, count=body.count, last_buffer=body.last_buffer)


@router.post("/api/ip-content/drafts", summary="根据已入库数据源和记忆生成文案草稿")
async def generate_drafts(
    body: DraftRequestBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(TikHubSourceItem).filter(TikHubSourceItem.user_id == current_user.id)
    if body.item_ids:
        query = query.filter(TikHubSourceItem.id.in_([int(x) for x in body.item_ids if str(x).isdigit()]))
    elif body.query_ids:
        query = query.filter(TikHubSourceItem.query_id.in_([str(x) for x in body.query_ids if str(x).strip()]))
    rows = query.order_by(TikHubSourceItem.created_at.desc(), TikHubSourceItem.id.desc()).limit(40).all()
    memories = [
        {
            "title": _clean_text(doc.get("title") or doc.get("name") or doc.get("filename"), 120),
            "content": _clean_long_text(doc.get("content") or doc.get("content_text") or doc.get("text") or doc.get("summary") or doc.get("notes"), 2400),
        }
        for doc in body.memory_docs[:10]
        if isinstance(doc, dict)
    ]
    if not rows and not memories:
        raise HTTPException(status_code=400, detail="请先查询榜单/同行数据，或选择至少一份记忆资料")

    count = max(1, min(int(body.count or 5), 20))
    task_requirements = _draft_requirements(body.task, body.platform, count)
    source_briefs = [_item_brief(row, idx + 1) for idx, row in enumerate(rows[:30])]
    current_year = _utcnow().year
    memory_payload = [
        {"title": m["title"], "content": m["content"][:1800]}
        for m in memories
        if m.get("content") or m.get("title")
    ]
    system_prompt = (
        "你是中文短视频和个人专业 IP 内容策划。根据给定的数据源和记忆资料生成可审核的内容草稿。"
        "必须返回严格 JSON，不要 Markdown 代码块。格式："
        "{\"items\":[{\"title\":\"\",\"hook\":\"\",\"body\":\"\",\"cta\":\"\",\"image_prompt\":\"\",\"image_prompts\":[\"\",\"\",\"\"]}]}。"
        "不要抄袭同行原文，不要编造资料里没有的硬性数据；可以提炼热点结构、话题角度和表达策略。"
        f"所有涉及年份的内容默认按当前年份 {current_year} 年表达；除非数据源原文明确提供其他年份，严禁默认写 2025 年。"
        "口播类文案暂不限制字数和时长，要写出深度、案例、具体场景和判断链路。"
        "朋友圈文案的 body 只能写主文案，配图提示必须放到 image_prompt，不要把“配图提示/画面建议”混入 body。"
        "生成朋友圈文案时，每条 items 必须额外返回 image_prompts 数组，数组内 3 条配图文案都要贴合同一条 body 的主题，但创意、主体、场景和表达隐喻必须明显不同。"
    )
    user_prompt = json.dumps(
        {
            "任务要求": task_requirements,
            "补充要求": _clean_long_text(body.extra_requirements, 4000),
            "需要条数": count,
            "平台": body.platform,
            "当前年份": current_year,
            "TikHub数据源": source_briefs,
            "记忆资料": memory_payload,
        },
        ensure_ascii=False,
    )
    token = (request.headers.get("Authorization") or request.headers.get("authorization") or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="缺少登录凭证")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": token,
    }
    xi = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    if xi:
        headers["X-Installation-Id"] = xi
    payload = {
        "model": (os.environ.get("IP_CONTENT_STUDIO_MODEL") or "deepseek-chat").strip() or "deepseek-chat",
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        "stream": False,
        "temperature": 0.76,
    }
    data = await _post_llm_with_retry(payload=payload, headers=headers, attempts=3)
    try:
        text = str(data["choices"][0]["message"]["content"] or "")
    except Exception:
        text = json.dumps(data, ensure_ascii=False)
    drafts = _normalize_drafts(_extract_json_object(text), text, count)
    return {
        "ok": True,
        "task": body.task,
        "platform": body.platform,
        "count": len(drafts),
        "requirements": task_requirements,
        "drafts": drafts,
        "source_items": [_item_payload(row) for row in rows],
        "memory_docs": memories,
        "extra_requirements": _clean_long_text(body.extra_requirements, 4000),
    }


@router.get("/admin/api/ip-content/tikhub-records", summary="管理员/代理商查询 TikHub 查询记录")
def admin_list_tikhub_records(
    user_id: Optional[int] = None,
    platform: str = "",
    query_type: str = "",
    q: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    query = db.query(TikHubQueryLog)
    if user_id:
        _assert_can_manage_user(db, ctx, int(user_id), allow_agent_self=True)
        query = query.filter(TikHubQueryLog.user_id == int(user_id))
    elif ctx.role == "agent":
        visible_ids = _agent_visible_user_ids(db, int(ctx.user_id or 0))
        query = query.filter(TikHubQueryLog.user_id.in_(visible_ids)) if visible_ids else query.filter(False)
    if platform.strip():
        query = query.filter(TikHubQueryLog.platform == platform.strip())
    if query_type.strip():
        query = query.filter(TikHubQueryLog.query_type == query_type.strip())
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(or_(TikHubQueryLog.query_id.ilike(like), TikHubQueryLog.query_type.ilike(like), TikHubQueryLog.error_message.ilike(like)))
    total = query.with_entities(func.count(TikHubQueryLog.id)).scalar() or 0
    rows = query.order_by(TikHubQueryLog.created_at.desc(), TikHubQueryLog.id.desc()).offset(offset).limit(limit).all()
    user_ids = sorted({row.user_id for row in rows})
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    items = []
    for row in rows:
        payload = _query_log_payload(row)
        user = users.get(row.user_id)
        payload["user_account"] = ((user.email or "") if user else "").replace("@sms.lobster.local", "")
        items.append(payload)
    return {
        "items": items,
        "pagination": {"total": int(total), "limit": int(limit), "offset": int(offset), "has_next": offset + limit < int(total)},
    }


@router.get("/api/ip-content/keywords", summary="IP content keyword seeds")
def list_keywords(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(IPContentKeyword)
        .filter(IPContentKeyword.user_id == current_user.id)
        .order_by(IPContentKeyword.created_at.desc(), IPContentKeyword.id.desc())
        .all()
    )
    return {"items": [_keyword_payload(row) for row in rows]}


@router.post("/api/ip-content/keywords", summary="Add IP content keyword seed")
def add_keyword(
    body: KeywordCreateBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    keyword = _clean_text(body.keyword, 191)
    if not keyword:
        raise HTTPException(status_code=400, detail="请填写关键词")
    row = IPContentKeyword(
        user_id=current_user.id,
        keyword=keyword,
        display_name=_clean_text(body.display_name, 255) or keyword,
        meta=_jsonable(body.meta or {}),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="该关键词已经存在")
    db.refresh(row)
    return {"ok": True, "item": _keyword_payload(row)}


@router.delete("/api/ip-content/keywords/{keyword_id}", summary="Delete IP content keyword seed")
def delete_keyword(
    keyword_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(IPContentKeyword).filter(IPContentKeyword.user_id == current_user.id, IPContentKeyword.id == keyword_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="关键词不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/api/ip-content/keywords/{keyword_id}/sync", summary="Sync Douyin hot search list by keyword")
async def sync_keyword(
    keyword_id: int,
    body: KeywordSyncBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(IPContentKeyword).filter(IPContentKeyword.user_id == current_user.id, IPContentKeyword.id == keyword_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="关键词不存在")
    return await _sync_keyword_row(db=db, current_user=current_user, row=row, page_size=body.page_size, date_window=body.date_window)


@router.get("/api/ip-content/draft-records", summary="List IP content AI draft records")
def list_draft_records(
    task: str = "",
    limit: int = Query(80, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(IPContentDraftRecord).filter(IPContentDraftRecord.user_id == current_user.id)
    if task.strip():
        query = query.filter(IPContentDraftRecord.task == task.strip())
    total = query.with_entities(func.count(IPContentDraftRecord.id)).scalar() or 0
    rows = query.order_by(IPContentDraftRecord.created_at.desc(), IPContentDraftRecord.id.desc()).offset(offset).limit(limit).all()
    return {
        "items": [_draft_record_payload(row) for row in rows],
        "pagination": {"total": int(total), "limit": int(limit), "offset": int(offset), "has_next": offset + limit < int(total)},
    }


@router.post("/api/ip-content/draft-records/{record_id}/image", summary="Attach selected image to IP content draft record")
def attach_draft_record_image(
    record_id: str,
    body: DraftRecordImageBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(IPContentDraftRecord).filter(IPContentDraftRecord.user_id == current_user.id, IPContentDraftRecord.record_id == record_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="生成记录不存在")
    row.image_url = _clean_long_text(body.image_url, 4096) or row.image_url
    row.image_asset_id = _clean_text(body.image_asset_id, 128) or row.image_asset_id
    row.image_prompt = _clean_long_text(body.image_prompt, 2000) or row.image_prompt
    row.selected = bool(body.selected)
    meta = dict(row.meta or {})
    incoming_images = body.meta.get("images") if isinstance(body.meta, dict) else None
    if isinstance(incoming_images, list):
        cleaned_images = []
        for item in incoming_images[:12]:
            if not isinstance(item, dict):
                continue
            url = _clean_long_text(item.get("image_url") or item.get("url"), 4096)
            if not url:
                continue
            cleaned_images.append(
                {
                    "image_url": url,
                    "image_asset_id": _clean_text(item.get("image_asset_id") or item.get("asset_id"), 128),
                    "image_prompt": _clean_long_text(item.get("image_prompt") or "", 2000),
                    "generated_prompt": _clean_long_text(item.get("generated_prompt") or "", 4000),
                    "variant": _clean_text(item.get("variant") or "", 80),
                    "index": int(item.get("index") or len(cleaned_images) + 1),
                    "created_at": _clean_text(item.get("created_at") or _utcnow().isoformat(), 64),
                }
            )
        if cleaned_images:
            meta["images"] = cleaned_images
    if isinstance(body.meta, dict):
        batch_id = _clean_text(body.meta.get("image_batch_id"), 96)
        batch_created_at = _clean_text(body.meta.get("image_batch_created_at"), 64)
        incoming_prompts = body.meta.get("image_prompts")
        if isinstance(incoming_prompts, list):
            meta["image_prompts"] = [_clean_long_text(item, 1600) for item in incoming_prompts[:3] if _clean_long_text(item, 1600)]
        if batch_id:
            meta["image_batch_id"] = batch_id
        if batch_created_at:
            meta["image_batch_created_at"] = batch_created_at
    meta["image_update"] = _jsonable(body.meta or {})
    meta["image_updated_at"] = _utcnow().isoformat()
    row.meta = meta
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _draft_record_payload(row)}


@router.post("/api/ip-content/generate/industry-hot-oral", summary="Generate keyword-based Douyin industry oral scripts")
async def generate_industry_hot_oral(
    body: AutoDraftRequestBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    keyword_query = db.query(IPContentKeyword).filter(IPContentKeyword.user_id == current_user.id, IPContentKeyword.status == "active")
    if body.keyword_ids:
        keyword_query = keyword_query.filter(IPContentKeyword.id.in_([int(x) for x in body.keyword_ids if str(x).isdigit()]))
    keywords = keyword_query.order_by(IPContentKeyword.created_at.desc(), IPContentKeyword.id.desc()).limit(8).all()
    if not keywords:
        raise HTTPException(status_code=400, detail="请先在配置里添加至少一个行业关键词。")
    sync_results = []
    if body.sync_before:
        for row in keywords:
            try:
                sync_results.append(await _sync_keyword_row(db=db, current_user=current_user, row=row, page_size=20, date_window=24))
            except Exception as exc:
                sync_results.append(_sync_error_result(source="keyword_sync", row=row, exc=exc, attempts=3))
    rows = _select_keyword_source_rows(db, current_user.id, [row.id for row in keywords], task="industry_hot_oral", limit=40)
    memories = _memory_payload_from_docs(body.memory_docs)
    generated = await _call_ip_content_llm(
        request=request,
        task="task1_industry",
        platform="douyin",
        count=min(max(int(body.count or 5), 1), 5),
        rows=rows,
        memories=memories,
        extra_requirements=body.extra_requirements,
        fallback_sources=_keyword_seed_briefs(keywords),
    )
    group_id = uuid.uuid4().hex
    records = _save_draft_records(
        db,
        current_user=current_user,
        task="industry_hot_oral",
        platform="douyin",
        drafts=generated["drafts"],
        rows=rows,
        memories=memories,
        extra_requirements=body.extra_requirements,
        group_id=group_id,
    )
    return {"ok": True, "task": "industry_hot_oral", "records": [_draft_record_payload(row) for row in records], "sync_results": sync_results, **generated}


@router.post("/api/ip-content/generate/professional-ip-oral", summary="Generate competitor-based professional IP oral scripts")
async def generate_professional_ip_oral(
    body: AutoDraftRequestBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account_query = db.query(ContentCompetitorAccount).filter(ContentCompetitorAccount.user_id == current_user.id, ContentCompetitorAccount.status == "active")
    if body.competitor_ids:
        account_query = account_query.filter(ContentCompetitorAccount.id.in_([int(x) for x in body.competitor_ids if str(x).isdigit()]))
    accounts = account_query.order_by(ContentCompetitorAccount.created_at.desc(), ContentCompetitorAccount.id.desc()).limit(8).all()
    sync_results = []
    if body.sync_before:
        for row in accounts:
            try:
                sync_results.append(await _sync_competitor_row(db=db, current_user=current_user, row=row, count=20))
            except Exception as exc:
                sync_results.append(_sync_error_result(source="competitor_sync", row=row, exc=exc, attempts=3))
    rows = _select_competitor_source_rows(db, current_user.id, [row.id for row in accounts], task="professional_ip_oral", limit=40)
    memories = _memory_payload_from_docs(body.memory_docs)
    generated = await _call_ip_content_llm(
        request=request,
        task="task1_ip",
        platform="douyin",
        count=min(max(int(body.count or 5), 1), 5),
        rows=rows,
        memories=memories,
        extra_requirements=body.extra_requirements,
        fallback_sources=_competitor_seed_briefs(accounts),
    )
    group_id = uuid.uuid4().hex
    records = _save_draft_records(
        db,
        current_user=current_user,
        task="professional_ip_oral",
        platform="douyin",
        drafts=generated["drafts"],
        rows=rows,
        memories=memories,
        extra_requirements=body.extra_requirements,
        group_id=group_id,
    )
    return {"ok": True, "task": "professional_ip_oral", "records": [_draft_record_payload(row) for row in records], "sync_results": sync_results, **generated}


@router.post("/api/ip-content/generate/moments-candidates", summary="Generate 20 WeChat Moments copy candidates")
async def generate_moments_candidates(
    body: AutoDraftRequestBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    keyword_query = db.query(IPContentKeyword).filter(IPContentKeyword.user_id == current_user.id, IPContentKeyword.status == "active")
    if body.keyword_ids:
        keyword_query = keyword_query.filter(IPContentKeyword.id.in_([int(x) for x in body.keyword_ids if str(x).isdigit()]))
    keywords = keyword_query.order_by(IPContentKeyword.created_at.desc(), IPContentKeyword.id.desc()).limit(8).all()
    account_query = db.query(ContentCompetitorAccount).filter(ContentCompetitorAccount.user_id == current_user.id, ContentCompetitorAccount.status == "active")
    if body.competitor_ids:
        account_query = account_query.filter(ContentCompetitorAccount.id.in_([int(x) for x in body.competitor_ids if str(x).isdigit()]))
    accounts = account_query.order_by(ContentCompetitorAccount.created_at.desc(), ContentCompetitorAccount.id.desc()).limit(8).all()
    sync_results = []
    if body.sync_before:
        for row in keywords:
            try:
                sync_results.append(await _sync_keyword_row(db=db, current_user=current_user, row=row, page_size=20, date_window=24))
            except Exception as exc:
                sync_results.append(_sync_error_result(source="keyword_sync", row=row, exc=exc, attempts=3))
        for row in accounts:
            try:
                sync_results.append(await _sync_competitor_row(db=db, current_user=current_user, row=row, count=20))
            except Exception as exc:
                sync_results.append(_sync_error_result(source="competitor_sync", row=row, exc=exc, attempts=3))
    rows = _select_keyword_source_rows(db, current_user.id, [row.id for row in keywords], task="moments_candidate", limit=24)
    rows.extend(_select_competitor_source_rows(db, current_user.id, [row.id for row in accounts], task="moments_candidate", limit=24))
    seen: set[int] = set()
    rows = [row for row in rows if not (row.id in seen or seen.add(row.id))][:40]
    memories = _memory_payload_from_docs(body.memory_docs)
    requested_count = min(max(int(body.count or 20), 1), 20)
    generated = await _call_ip_content_llm(
        request=request,
        task="task2_moments",
        platform="wechat_moments",
        count=requested_count,
        rows=rows,
        memories=memories,
        extra_requirements=body.extra_requirements,
        fallback_sources=(_keyword_seed_briefs(keywords) + _competitor_seed_briefs(accounts))[:40],
    )
    group_id = _clean_group_id(body.group_id) or uuid.uuid4().hex
    records = _save_draft_records(
        db,
        current_user=current_user,
        task="moments_candidate",
        platform="wechat_moments",
        drafts=generated["drafts"],
        rows=rows,
        memories=memories,
        extra_requirements=body.extra_requirements,
        group_id=group_id,
    )
    return {"ok": True, "task": "moments_candidate", "records": [_draft_record_payload(row) for row in records], "sync_results": sync_results, **generated}
