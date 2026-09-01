"""Daily public Douyin data snapshots backed by TikHub.

This service is deliberately separate from the user-scoped TikHub proxy. It
does not bill a user and it never persists the upstream response body. Only a
small normalized snapshot is kept for the information desk.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.exc import IntegrityError

from ..models import DouyinPlatformSnapshot
from ..core.config import settings
from ..db import SessionLocal

logger = logging.getLogger(__name__)

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
COLLECTION_HOUR = 9
MAX_ITEMS_PER_SECTION = 20
MAX_STRING_LENGTH = 320
MAX_METRICS = 12
TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504}


# These are platform/public endpoints from https://docs.tikhub.io/llms.txt.
# Targeted account, video, DOU+, and logged-in creator analytics are kept out
# of the unattended daily job because they require a target or private data.
PUBLIC_DAILY_ENDPOINTS: tuple[dict[str, Any], ...] = (
    {"key": "hot_search", "title": "实时热搜", "category": "热搜", "method": "GET", "path": "/api/v1/douyin/app/v3/fetch_hot_search_list", "params": {"board_type": "0", "board_sub_type": ""}},
    {"key": "hot_search_seeding", "title": "种草热榜", "category": "热搜", "method": "GET", "path": "/api/v1/douyin/app/v3/fetch_hot_search_list", "params": {"board_type": "2", "board_sub_type": "seeding"}},
    {"key": "hot_search_entertainment", "title": "娱乐热榜", "category": "热搜", "method": "GET", "path": "/api/v1/douyin/app/v3/fetch_hot_search_list", "params": {"board_type": "2", "board_sub_type": "2"}},
    {"key": "hot_search_social", "title": "社会热榜", "category": "热搜", "method": "GET", "path": "/api/v1/douyin/app/v3/fetch_hot_search_list", "params": {"board_type": "2", "board_sub_type": "4"}},
    {"key": "hot_search_challenge", "title": "挑战热榜", "category": "热搜", "method": "GET", "path": "/api/v1/douyin/app/v3/fetch_hot_search_list", "params": {"board_type": "2", "board_sub_type": "hotspot_challenge"}},
    {"key": "live_hot_search", "title": "直播热搜", "category": "热搜", "method": "GET", "path": "/api/v1/douyin/app/v3/fetch_live_hot_search_list"},
    {"key": "music_hot_search", "title": "音乐热榜", "category": "音乐", "method": "GET", "path": "/api/v1/douyin/app/v3/fetch_music_hot_search_list"},
    {"key": "hot_rise", "title": "上升热点", "category": "热点榜", "method": "GET", "path": "/api/v1/douyin/billboard/fetch_hot_rise_list", "params": {"page": 1, "page_size": 20, "order": "rank_diff"}},
    {"key": "hot_city", "title": "同城热点", "category": "热点榜", "method": "GET", "path": "/api/v1/douyin/billboard/fetch_hot_city_list", "params": {"page": 1, "page_size": 20, "order": "rank"}},
    {"key": "hot_challenge", "title": "挑战热榜", "category": "热点榜", "method": "GET", "path": "/api/v1/douyin/billboard/fetch_hot_challenge_list", "params": {"page": 1, "page_size": 20}},
    {"key": "hot_total", "title": "热点总榜", "category": "热点榜", "method": "GET", "path": "/api/v1/douyin/billboard/fetch_hot_total_list", "params": {"page": 1, "page_size": 20, "type": "range"}},
    {"key": "hot_video", "title": "视频热榜", "category": "内容榜", "method": "POST", "path": "/api/v1/douyin/billboard/fetch_hot_total_video_list", "body": {"page": 1, "page_size": 20}},
    {"key": "low_fan_video", "title": "低粉爆款", "category": "内容榜", "method": "POST", "path": "/api/v1/douyin/billboard/fetch_hot_total_low_fan_list", "body": {"page": 1, "page_size": 20}},
    {"key": "high_play_video", "title": "高完播率", "category": "内容榜", "method": "POST", "path": "/api/v1/douyin/billboard/fetch_hot_total_high_play_list", "body": {"page": 1, "page_size": 20}},
    {"key": "high_like_video", "title": "高点赞率", "category": "内容榜", "method": "POST", "path": "/api/v1/douyin/billboard/fetch_hot_total_high_like_list", "body": {"page": 1, "page_size": 20}},
    {"key": "high_fan_video", "title": "高涨粉率", "category": "内容榜", "method": "POST", "path": "/api/v1/douyin/billboard/fetch_hot_total_high_fan_list", "body": {"page": 1, "page_size": 20}},
    {"key": "hot_topic", "title": "话题热榜", "category": "话题", "method": "POST", "path": "/api/v1/douyin/billboard/fetch_hot_total_topic_list", "body": {"page": 1, "page_size": 20}},
    {"key": "rising_topic", "title": "飙升话题", "category": "话题", "method": "POST", "path": "/api/v1/douyin/billboard/fetch_hot_total_high_topic_list", "body": {"page": 1, "page_size": 20}},
    {"key": "hot_search_words", "title": "搜索热榜", "category": "搜索", "method": "POST", "path": "/api/v1/douyin/billboard/fetch_hot_total_search_list", "body": {"page_num": 1, "page_size": 20}},
    {"key": "rising_search_words", "title": "飙升搜索", "category": "搜索", "method": "POST", "path": "/api/v1/douyin/billboard/fetch_hot_total_high_search_list", "body": {"page_num": 1, "page_size": 20}},
    {"key": "hot_content_words", "title": "热门内容词", "category": "搜索", "method": "POST", "path": "/api/v1/douyin/billboard/fetch_hot_total_hot_word_list", "body": {"page": 1, "page_size": 20}},
    {"key": "hot_accounts", "title": "热门账号", "category": "账号榜", "method": "POST", "path": "/api/v1/douyin/billboard/fetch_hot_account_list", "body": {"date_window": 24, "page_num": 1, "page_size": 20, "query_tag": None}},
    {"key": "hot_calendar", "title": "热点活动日历", "category": "热点榜", "method": "POST", "path": "/api/v1/douyin/billboard/fetch_hot_calendar_list", "body": {"city_code": "", "category_code": ""}},
    {"key": "hot_category", "title": "热点榜分类", "category": "平台字典", "method": "GET", "path": "/api/v1/douyin/billboard/fetch_hot_category_list", "params": {"billboard_type": "rise", "keyword": ""}},
    {"key": "hot_city_dictionary", "title": "城市字典", "category": "平台字典", "method": "GET", "path": "/api/v1/douyin/billboard/fetch_city_list"},
    {"key": "brand_hot_categories", "title": "品牌热榜分类", "category": "品牌", "method": "GET", "path": "/api/v1/douyin/app/v3/fetch_brand_hot_search_list"},
    {"key": "current_hot_topic", "title": "实时热点排行", "category": "指数", "method": "GET", "path": "/api/v1/douyin/index/fetch_current_hot_topic"},
    {"key": "hot_words", "title": "热门关键词", "category": "指数", "method": "GET", "path": "/api/v1/douyin/index/fetch_hot_words"},
    {"key": "hot_trend_words", "title": "热门推荐词", "category": "指数", "method": "GET", "path": "/api/v1/douyin/index/fetch_hot_trend_word"},
    {"key": "index_valid_dates", "title": "指数有效日期", "category": "平台字典", "method": "GET", "path": "/api/v1/douyin/index/fetch_all_valid_date"},
    {"key": "content_valid_dates", "title": "创作指南有效日期", "category": "平台字典", "method": "GET", "path": "/api/v1/douyin/index/fetch_content_valid_date"},
    {"key": "publish_trend", "title": "内容发布趋势", "category": "创作指南", "method": "GET", "path": "/api/v1/douyin/index/fetch_content_publish_trend"},
    {"key": "creator_video_board", "title": "创作者热门视频", "category": "创作者中心", "method": "GET", "path": "/api/v1/douyin/creator/fetch_creator_material_center_billboard", "params": {"billboard_tag": 0, "order_key": 1, "time_filter": 1}},
    {"key": "creator_hot_spot", "title": "创作热点", "category": "创作者中心", "method": "GET", "path": "/api/v1/douyin/creator/fetch_creator_hot_spot_billboard", "params": {"billboard_tag": "0", "hot_search_type": 1}},
    {"key": "creator_hot_topic", "title": "创作热门话题", "category": "创作者中心", "method": "GET", "path": "/api/v1/douyin/creator/fetch_creator_hot_topic_billboard", "params": {"billboard_tag": 0, "order_key": 1, "time_filter": 1}},
    {"key": "creator_hot_music", "title": "创作热门音乐", "category": "创作者中心", "method": "GET", "path": "/api/v1/douyin/creator/fetch_creator_hot_music_billboard", "params": {"billboard_tag": 0, "order_key": 1, "time_filter": 1}},
    {"key": "xingtu_catalog", "title": "星图榜单分类", "category": "星图", "method": "GET", "path": "/api/v1/douyin/xingtu_v2/get_ranking_list_catalog", "params": {"biz_scene": "douyin_flow_split_video_author_ranks"}},
    {"key": "xingtu_trend_guide", "title": "星图内容趋势", "category": "星图", "method": "GET", "path": "/api/v1/douyin/xingtu_v2/get_content_trend_guide"},
    {"key": "xingtu_case_categories", "title": "星图优秀行业", "category": "星图", "method": "GET", "path": "/api/v1/douyin/xingtu_v2/get_excellent_case_category_list", "params": {"platform_source": 1}},
    {"key": "xingtu_ip_industries", "title": "星图 IP 行业", "category": "星图", "method": "GET", "path": "/api/v1/douyin/xingtu_v2/get_ip_activity_industry_list"},
)


# Keep the unattended request set aligned with the current OpenAPI contract.
# Some endpoints accept empty requests, while the billboard endpoints require
# pagination fields even though the fields are not user-specific.
_ENDPOINT_RUNTIME_OVERRIDES = {
    "hot_rise": {"params": {"page": 1, "page_size": 20, "order": "rank_diff"}},
    "hot_city": {"params": {"page": 1, "page_size": 20, "order": "rank"}},
    "hot_challenge": {"params": {"page": 1, "page_size": 20}},
    "hot_total": {"params": {"page": 1, "page_size": 20, "type": "range"}},
    "hot_video": {"body": {"page": 1, "page_size": 20, "date_window": 24, "sub_type": 1001, "keyword": "", "tags": []}},
    "low_fan_video": {"body": {"page": 1, "page_size": 20, "date_window": 24, "keyword": "", "tags": []}},
    "high_play_video": {"body": {"page": 1, "page_size": 20, "date_window": 24, "keyword": "", "tags": []}},
    "high_like_video": {"body": {"page": 1, "page_size": 20, "date_window": 24, "keyword": "", "tags": []}},
    "high_fan_video": {"body": {"page": 1, "page_size": 20, "date_window": 24, "keyword": "", "tags": []}},
    "hot_topic": {"body": {"page": 1, "page_size": 20, "date_window": 24, "keyword": "", "tags": []}},
    "rising_topic": {"body": {"page": 1, "page_size": 20, "date_window": 24, "keyword": "", "tags": []}},
    "hot_search_words": {"body": {"page_num": 1, "page_size": 20, "date_window": 24, "keyword": "抖音"}},
    "rising_search_words": {"body": {"page_num": 1, "page_size": 20, "date_window": 24, "keyword": "抖音"}},
    "hot_content_words": {"body": {"page_num": 1, "page_size": 20, "date_window": 24, "keyword": "抖音"}},
    "hot_accounts": {"body": {"date_window": 24, "page_num": 1, "page_size": 20, "query_tag": None}},
    "hot_calendar": {"body": {"city_code": "", "category_code": ""}},
    "hot_category": {"params": {"billboard_type": "rise", "keyword": ""}},
    "creator_video_board": {"params": {"billboard_tag": 0, "order_key": 1, "time_filter": 1}},
    "creator_hot_spot": {"params": {"billboard_tag": "0", "hot_search_type": 1}},
    "creator_hot_topic": {"params": {"billboard_tag": 0, "order_key": 1, "time_filter": 1}},
    "creator_hot_music": {"params": {"billboard_tag": 0, "order_key": 1, "time_filter": 1}},
    "xingtu_catalog": {"params": {"biz_scene": "douyin_flow_split_video_author_ranks"}},
    "xingtu_case_categories": {"params": {"platform_source": 1}},
}
for _endpoint in PUBLIC_DAILY_ENDPOINTS:
    _endpoint.update(_ENDPOINT_RUNTIME_OVERRIDES.get(_endpoint["key"], {}))

# Content publish trend requires a valid content tag and date range. It stays
# in the provider catalog for future parameterized expansion, but is not
# called by the no-user daily snapshot job.
DAILY_COLLECTION_ENDPOINTS = tuple(
    endpoint for endpoint in PUBLIC_DAILY_ENDPOINTS if endpoint["key"] != "publish_trend"
)


def _tikhub_base() -> str:
    base = str(getattr(settings, "tikhub_api_base", "") or os.environ.get("TIKHUB_API_BASE") or "").strip()
    if base == "https://api.tikhub.dev":
        base = "https://api.tikhub.io"
    return (base or "https://api.tikhub.io").rstrip("/")


def _tikhub_key() -> str:
    return str(getattr(settings, "tikhub_api_key", None) or os.environ.get("TIKHUB_API_KEY") or "").strip()


def _clip_string(value: Any, limit: int = MAX_STRING_LENGTH) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()[:limit]


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        raw = str(value).replace(",", "").strip()
        if not raw:
            return None
        return float(raw) if "." in raw else int(raw)
    except (TypeError, ValueError):
        return None


def _first_value(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            if isinstance(value, dict):
                for nested_key in ("name", "nickname", "title", "url", "value"):
                    nested = value.get(nested_key)
                    if nested not in (None, ""):
                        return nested
            return value
    return None


def _extract_items(value: Any, depth: int = 0) -> list[Any]:
    if depth > 4 or value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw or raw[0] not in "[{":
            return []
        try:
            return _extract_items(json.loads(raw), depth + 1)
        except (TypeError, ValueError):
            return []
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return []
    preferred_keys = ("items", "list", "data", "result", "results", "records", "aweme_list", "hot_list", "data_list")
    for key in preferred_keys:
        nested = value.get(key)
        if isinstance(nested, list):
            return nested
        found = _extract_items(nested, depth + 1)
        if found:
            return found
    for nested in value.values():
        found = _extract_items(nested, depth + 1)
        if found:
            return found
    return []


def _compact_item(value: Any, index: int) -> dict[str, Any] | None:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        label = _clip_string(value)
        return {"rank": index + 1, "title": label} if label else None
    if not isinstance(value, dict):
        return None
    item: dict[str, Any] = {"rank": index + 1}
    field_aliases = {
        "id": ("id", "aweme_id", "item_id", "video_id", "topic_id", "music_id", "uid"),
        "title": ("title", "desc", "description", "name", "word", "keyword", "topic_name", "music_name"),
        "author": ("author", "author_name", "nickname", "username", "user_name", "sec_uid"),
        "url": ("url", "share_url", "web_url", "video_url", "homepage_url", "play_url"),
        "cover_url": ("cover_url", "cover", "cover_image", "image_url", "avatar_url", "origin_cover"),
    }
    for output_key, aliases in field_aliases.items():
        value_found = _first_value(value, aliases)
        if isinstance(value_found, (str, int, float)) and not isinstance(value_found, bool):
            if output_key == "id":
                item[output_key] = _clip_string(value_found, 128)
            elif output_key in {"url", "cover_url"}:
                text = _clip_string(value_found, 1000)
                if text.startswith(("http://", "https://")):
                    item[output_key] = text
            else:
                text = _clip_string(value_found)
                if text:
                    item[output_key] = text
    metric_aliases = ("play_count", "view_count", "digg_count", "like_count", "comment_count", "share_count", "collect_count", "follower_count", "fans_count", "hot_value", "score", "rank_value")
    metrics: dict[str, int | float] = {}
    for key in metric_aliases:
        number = _number(value.get(key))
        if number is not None:
            metrics[key] = number
    for nested_key in ("statistics", "stats", "metrics", "data"):
        nested = value.get(nested_key)
        if isinstance(nested, dict):
            for key in metric_aliases:
                if key not in metrics:
                    number = _number(nested.get(key))
                    if number is not None:
                        metrics[key] = number
    if metrics:
        item["metrics"] = dict(list(metrics.items())[:MAX_METRICS])
    if len(item) == 1:
        return None
    return item


def _compact_items(payload: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, value in enumerate(_extract_items(payload)[:MAX_ITEMS_PER_SECTION]):
        item = _compact_item(value, index)
        if item:
            items.append(item)
    return items


def _payload_message(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("message", "msg", "error", "detail", "error_message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:500]
            if isinstance(value, dict):
                nested = _payload_message(value)
                if nested:
                    return nested
    return ""


def _payload_success(payload: Any, status_code: int) -> bool:
    if not 200 <= status_code < 300:
        return False
    if not isinstance(payload, dict):
        return True
    if payload.get("ok") is False or payload.get("success") is False:
        return False
    code = payload.get("code")
    if code is not None:
        try:
            return int(code) in {0, 1, 200}
        except (TypeError, ValueError):
            return True
    return True


def _endpoint_request(endpoint: dict[str, Any], snapshot_date: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return request params/body without mutating the shared endpoint catalog."""
    params = dict(endpoint.get("params") or {})
    body = dict(endpoint.get("body") or {})
    if endpoint.get("key") == "hot_total":
        day = str(snapshot_date or _local_today().isoformat()).replace("-", "")
        params.update({"start_date": day, "end_date": day})
    if endpoint.get("key") == "hot_category":
        day = str(snapshot_date or _local_today().isoformat()).replace("-", "")
        params.update({"start_date": day, "end_date": day})
    if endpoint.get("key") == "hot_calendar":
        day = str(snapshot_date or _local_today().isoformat())
        try:
            day_start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=SHANGHAI_TZ)
        except ValueError:
            day_start = datetime.now(SHANGHAI_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1) - timedelta(seconds=1)
        body.update({"start_date": int(day_start.timestamp()), "end_date": int(day_end.timestamp())})
    return params, body


async def _fetch_endpoint(
    client: httpx.AsyncClient,
    endpoint: dict[str, Any],
    semaphore: asyncio.Semaphore,
    snapshot_date: str | None = None,
) -> dict[str, Any]:
    async with semaphore:
        started = time.perf_counter()
        last_error = ""
        status_code = 0
        for attempt in range(2):
            try:
                params, body = _endpoint_request(endpoint, snapshot_date)
                if endpoint["method"] == "POST":
                    response = await client.post(_tikhub_base() + endpoint["path"], json=body)
                else:
                    response = await client.get(_tikhub_base() + endpoint["path"], params=params)
                status_code = int(response.status_code)
                try:
                    payload = response.json()
                except Exception:
                    payload = {}
                if _payload_success(payload, status_code):
                    return {
                        "key": endpoint["key"],
                        "title": endpoint["title"],
                        "category": endpoint["category"],
                        "path": endpoint["path"],
                        "status": "success",
                        "http_status": status_code,
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                        "item_count": len(_compact_items(payload)),
                        "items": _compact_items(payload),
                    }
                last_error = _payload_message(payload) or f"TikHub HTTP {status_code}"
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = str(exc)[:500]
            if attempt == 0 and (status_code in TRANSIENT_STATUSES or not status_code):
                await asyncio.sleep(0.8)
                continue
            break
        return {
            "key": endpoint["key"],
            "title": endpoint["title"],
            "category": endpoint["category"],
            "path": endpoint["path"],
            "status": "failed",
            "http_status": status_code or None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "item_count": 0,
            "items": [],
            "error": last_error or "TikHub request failed",
        }


def _local_today() -> date:
    return datetime.now(SHANGHAI_TZ).date()


def _next_collection_at(now: datetime | None = None) -> datetime:
    current = now or datetime.now(SHANGHAI_TZ)
    target = current.replace(hour=COLLECTION_HOUR, minute=0, second=0, microsecond=0)
    if current >= target:
        target += timedelta(days=1)
    return target


def _catalog_payload() -> list[dict[str, Any]]:
    daily_keys = {item["key"] for item in DAILY_COLLECTION_ENDPOINTS}
    return [
        {
            "key": item["key"],
            "title": item["title"],
            "category": item["category"],
            "path": item["path"],
            "daily": item["key"] in daily_keys,
            "requires_parameters": item["key"] not in daily_keys,
        }
        for item in PUBLIC_DAILY_ENDPOINTS
    ]


def _snapshot_payload(row: DouyinPlatformSnapshot | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "snapshot_date": row.snapshot_date,
        "fetched_at": row.fetched_at.isoformat() + "Z" if row.fetched_at else None,
        "status": row.status,
        "summary": row.summary or {},
        "sections": row.sections or [],
        "endpoint_status": row.endpoint_status or [],
        "error_message": row.error_message or "",
    }


async def collect_douyin_platform_snapshot(snapshot_date: str | None = None, force: bool = False) -> dict[str, Any]:
    """Fetch and persist one public snapshot. The caller is the singleton worker."""
    if not _tikhub_key():
        logger.warning("[douyin-information-desk] TIKHUB_API_KEY is not configured")
        return {"ok": False, "status": "not_configured", "error_message": "TIKHUB_API_KEY is not configured"}
    day = snapshot_date or _local_today().isoformat()
    db = SessionLocal()
    try:
        row = db.query(DouyinPlatformSnapshot).filter(DouyinPlatformSnapshot.snapshot_date == day).first()
        if row is not None and row.status == "success" and not force:
            return {"ok": True, "snapshot": _snapshot_payload(row), "skipped": True}
        if row is None:
            row = DouyinPlatformSnapshot(snapshot_date=day, status="running", summary={})
            db.add(row)
        else:
            row.status = "running"
            row.error_message = None
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.query(DouyinPlatformSnapshot).filter(DouyinPlatformSnapshot.snapshot_date == day).first()
        if row is not None and row.status == "success" and not force:
            result = {"ok": True, "snapshot": _snapshot_payload(row), "skipped": True}
            db.close()
            return result
        raise
    finally:
        db.close()

    headers = {
        "Authorization": f"Bearer {_tikhub_key()}",
        "Accept": "application/json",
        "User-Agent": "lobster-douyin-information-desk/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0), headers=headers, trust_env=False) as client:
            semaphore = asyncio.Semaphore(4)
            results = await asyncio.gather(
                *(_fetch_endpoint(client, endpoint, semaphore, day) for endpoint in DAILY_COLLECTION_ENDPOINTS)
            )
    except Exception as exc:
        logger.exception("[douyin-information-desk] collection crashed day=%s", day)
        results = [{"key": "collector", "title": "采集器", "category": "系统", "path": "", "status": "failed", "http_status": None, "latency_ms": 0, "item_count": 0, "items": [], "error": str(exc)[:500]}]

    success_count = sum(1 for result in results if result.get("status") == "success")
    item_count = sum(int(result.get("item_count") or 0) for result in results)
    overall = "success" if success_count == len(results) else ("partial" if success_count else "failed")
    endpoint_status = [
        {key: value for key, value in result.items() if key not in {"items"}}
        for result in results
    ]
    sections = [
        {
            "key": result["key"],
            "title": result["title"],
            "category": result["category"],
            "items": result.get("items") or [],
            "error": result.get("error") or "",
        }
        for result in results
        if result.get("status") == "success" or result.get("error")
    ]
    summary = {
        "endpoint_count": len(results),
        "success_count": success_count,
        "failed_count": len(results) - success_count,
        "item_count": item_count,
        "source": "TikHub Douyin public APIs",
    }
    errors = [str(result.get("error") or "") for result in results if result.get("error")]
    db = SessionLocal()
    try:
        row = db.query(DouyinPlatformSnapshot).filter(DouyinPlatformSnapshot.snapshot_date == day).first()
        if row is None:
            row = DouyinPlatformSnapshot(snapshot_date=day)
            db.add(row)
        row.fetched_at = datetime.utcnow()
        row.status = overall
        row.summary = summary
        row.sections = sections
        row.endpoint_status = endpoint_status
        row.error_message = "; ".join(errors)[:2000] or None
        db.commit()
        payload = _snapshot_payload(row)
    finally:
        db.close()
    logger.info("[douyin-information-desk] snapshot day=%s status=%s endpoints=%s/%s items=%s", day, overall, success_count, len(results), item_count)
    return {"ok": overall != "failed", "snapshot": payload}


def _collection_due(db: Any, day: str, now_utc: datetime) -> bool:
    row = db.query(DouyinPlatformSnapshot).filter(DouyinPlatformSnapshot.snapshot_date == day).first()
    if row is None:
        return True
    if row.status == "success":
        return False
    updated = row.updated_at or row.fetched_at or row.created_at
    return not updated or now_utc - updated >= timedelta(minutes=30)


async def douyin_platform_information_desk_background_loop(interval_seconds: float = 60.0) -> None:
    """Singleton worker loop: one run after 09:00 Asia/Shanghai per day."""
    while True:
        now_local = datetime.now(SHANGHAI_TZ)
        if now_local.hour >= COLLECTION_HOUR:
            day = now_local.date().isoformat()
            db = SessionLocal()
            try:
                due = _collection_due(db, day, datetime.utcnow())
            finally:
                db.close()
            if due:
                try:
                    await collect_douyin_platform_snapshot(day)
                except Exception:
                    logger.exception("[douyin-information-desk] scheduled collection failed day=%s", day)
        await asyncio.sleep(max(15.0, float(interval_seconds)))


def information_desk_response(db: Any) -> dict[str, Any]:
    row = db.query(DouyinPlatformSnapshot).order_by(DouyinPlatformSnapshot.snapshot_date.desc()).first()
    return {
        "ok": True,
        "snapshot": _snapshot_payload(row),
        "last_fetched_at": row.fetched_at.isoformat() + "Z" if row and row.fetched_at else None,
        "next_collection_at": _next_collection_at().isoformat(),
        "catalog": _catalog_payload(),
    }
