from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .admin import AdminContext, _agent_visible_user_ids, _assert_can_manage_user, _verify_admin_token
from .auth import get_current_user
from ..core.config import settings
from ..db import get_db
from ..models import ContentCompetitorAccount, TikHubQueryLog, TikHubSourceItem, User
from ..services.credit_ledger import append_credit_ledger
from ..services.credits_amount import credits_json_float, quantize_credits, user_balance_decimal

router = APIRouter()


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


class DraftRequestBody(BaseModel):
    task: str
    platform: str = ""
    memory_docs: list[dict[str, Any]] = Field(default_factory=list)
    query_ids: list[str] = Field(default_factory=list)
    item_ids: list[int] = Field(default_factory=list)
    extra_requirements: str = ""
    count: int = Field(5, ge=1, le=20)


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
        "objs",
        "data_list",
    ):
        value = node.get(key)
        if isinstance(value, list):
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
            "cover.url_list.0",
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
        "like_cnt",
        "comment_count",
        "share_count",
        "collect_count",
        "fans_cnt",
        "score",
        "follow_cnt",
        "follow_rate",
        "like_rate",
        "forward_count",
    ):
        value = stats.get(key) if key in stats else source.get(key)
        if value not in (None, ""):
            out[key] = value
    return out


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
    if task_key == "task1_industry":
        return (
            f"任务一：基于行业榜单/热门话题生成 {count} 条行业热门口播文案。"
            "每条要有标题、开场钩子、口播正文、转化/互动收口、可配画面建议。"
            "口播时长控制在 30-60 秒，语气要像真实短视频创作者，不要空泛鸡汤。"
        )
    if task_key == "task1_ip":
        return (
            f"任务一：基于同行最新作品和本地知识库生成 {count} 条个人专业 IP 口播文案。"
            "要体现专业判断、案例感和个人观点；不能抄袭同行原文，只提炼选题结构和表达角度。"
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
            "每条要自然、有生活场景、有专业可信度，适合个人 IP 日更；避免广告腔。"
            "长度 120-280 字，给出可选配图提示。"
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
                    drafts.append({"title": text[:40], "body": text, "image_prompt": ""})
                continue
            title = _clean_text(item.get("title") or item.get("选题") or item.get("headline"), 160)
            hook = _clean_long_text(item.get("hook") or item.get("开场") or "", 1000)
            body = _clean_long_text(item.get("body") or item.get("copy") or item.get("script") or item.get("正文"), 6000)
            cta = _clean_long_text(item.get("cta") or item.get("收口") or "", 1000)
            image_prompt = _clean_long_text(item.get("image_prompt") or item.get("配图提示") or item.get("visual_prompt"), 1600)
            pieces = [x for x in (hook, body, cta) if x]
            full_text = "\n\n".join(pieces) if pieces else _clean_long_text(item.get("full_text") or item.get("text"), 6000)
            drafts.append(
                {
                    "title": title or (full_text[:40] if full_text else "未命名文案"),
                    "body": full_text or title or "",
                    "image_prompt": image_prompt,
                }
            )
    if not drafts:
        text = fallback_text.strip()
        parts = [p.strip() for p in re.split(r"\n\s*(?:\d+[\.\、]|[-*])\s*", text) if p.strip()]
        if len(parts) <= 1:
            parts = [text] if text else []
        for part in parts[:count]:
            drafts.append({"title": part[:40], "body": part, "image_prompt": ""})
    return drafts[:count]


def _normalize_item(raw: Any, *, user_id: int, query_id: str, platform: str, source_type: str, idx: int) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {"value": raw}
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    if not author:
        author = item.get("user") if isinstance(item.get("user"), dict) else {}
    if not author:
        nested_author = _lookup(item, "aweme_info.author")
        author = nested_author if isinstance(nested_author, dict) else {}
    title = _first(
        item,
        [
            "title",
            "item_title",
            "challenge_name",
            "desc",
            "description",
            "sentence",
            "word",
            "hotword",
            "keyword",
            "name",
            "aweme_info.desc",
            "aweme_info.caption",
        ],
    )
    description = _first(item, ["description", "desc", "summary", "challenge_name", "aweme_info.desc"])
    item_key = _first(
        item,
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
            "aweme_info.aweme_id",
        ],
    )
    if item_key in (None, ""):
        basis = _first(item, ["share_url", "url", "title", "desc", "sentence", "word"]) or item
        item_key = _stable_hash({"platform": platform, "source_type": source_type, "idx": idx, "basis": basis})
    author_key = _first(
        author,
        ["sec_uid", "uid", "id", "short_id", "unique_id", "username", "finder_username", "nickname"],
    )
    author_name = _first(author, ["nickname", "name", "display_name", "unique_id", "short_id"]) or _first(item, ["nick_name", "author_name"])
    publish_time = _first(item, ["create_time", "publish_time", "timestamp", "aweme_info.create_time"])
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
        "public_url": _public_url(item) or None,
        "cover_url": _cover_url(item) or None,
        "publish_time": _clean_text(publish_time, 64) or None,
        "metrics": _metric_payload(item) or None,
        "raw": _jsonable(item),
    }


def _item_payload(row: TikHubSourceItem) -> dict[str, Any]:
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


def _persist_items(
    db: Session,
    *,
    user_id: int,
    query_id: str,
    platform: str,
    source_type: str,
    raw_items: list[Any],
) -> list[TikHubSourceItem]:
    saved: list[TikHubSourceItem] = []
    for idx, raw in enumerate(raw_items):
        data = _normalize_item(raw, user_id=user_id, query_id=query_id, platform=platform, source_type=source_type, idx=idx)
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
            row.raw = data["raw"]
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


async def _execute_query(
    *,
    db: Session,
    current_user: User,
    query_type: str,
    params: dict[str, Any],
    body: dict[str, Any],
    save_items: bool = True,
    meta: Optional[dict[str, Any]] = None,
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

    return {
        "ok": bool(success),
        "query": _query_log_payload(log, include_raw=False, items=saved_items[:50]),
        "items": [_item_payload(item) for item in saved_items[:50]],
        "raw_item_count": len(raw_items),
        "balance_after": credits_json_float(balance_after),
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
    if source_type.strip():
        query = query.filter(TikHubSourceItem.source_type == source_type.strip())
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
    rows = query.order_by(TikHubSourceItem.created_at.desc(), TikHubSourceItem.id.desc()).offset(offset).limit(limit).all()
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
    if row.platform == "douyin":
        result = await _execute_query(
            db=db,
            current_user=current_user,
            query_type="douyin_user_posts",
            params={"sec_user_id": row.account_key, "count": body.count, "sort_type": 0},
            body={},
            save_items=True,
            meta={"competitor_account_id": row.id},
        )
    elif row.platform == "wechat_channels":
        result = await _execute_query(
            db=db,
            current_user=current_user,
            query_type="wechat_channels_home_page",
            params={},
            body={"username": row.account_key, "last_buffer": body.last_buffer or ""},
            save_items=True,
            meta={"competitor_account_id": row.id},
        )
    else:
        raise HTTPException(status_code=400, detail="不支持的平台")
    row.last_fetch_at = _utcnow()
    first_item = (result.get("items") or [{}])[0] if isinstance(result.get("items"), list) else {}
    if isinstance(first_item, dict) and first_item.get("item_key"):
        row.last_seen_item_key = _clean_text(first_item.get("item_key"), 191)
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    result["competitor"] = _competitor_payload(row)
    return result


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
    memory_payload = [
        {"title": m["title"], "content": m["content"][:1800]}
        for m in memories
        if m.get("content") or m.get("title")
    ]
    system_prompt = (
        "你是中文短视频和个人专业 IP 内容策划。根据给定的数据源和记忆资料生成可审核的内容草稿。"
        "必须返回严格 JSON，不要 Markdown 代码块。格式："
        "{\"items\":[{\"title\":\"\",\"hook\":\"\",\"body\":\"\",\"cta\":\"\",\"image_prompt\":\"\"}]}。"
        "不要抄袭同行原文，不要编造资料里没有的硬性数据；可以提炼热点结构、话题角度和表达策略。"
    )
    user_prompt = json.dumps(
        {
            "任务要求": task_requirements,
            "补充要求": _clean_long_text(body.extra_requirements, 4000),
            "需要条数": count,
            "平台": body.platform,
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
    async with httpx.AsyncClient(timeout=150.0, trust_env=False) as client:
        resp = await client.post(f"{_internal_api_base()}/api/sutui-chat/completions", json=payload, headers=headers)
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        detail = _response_message(data) or _clean_long_text(data, 800) or f"HTTP {resp.status_code}"
        raise HTTPException(status_code=resp.status_code, detail=f"文案生成失败：{detail}")
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
