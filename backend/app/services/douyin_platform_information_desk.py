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
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.exc import IntegrityError

from ..models import DouyinPlatformSnapshot
from ..core.config import settings
from ..db import SessionLocal
from .tikhub_pricing import price_breakdown as tikhub_price_breakdown

logger = logging.getLogger(__name__)

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
COLLECTION_HOUR = 9
MAX_ITEMS_PER_SECTION = 20
MAX_STRING_LENGTH = 320
MAX_METRICS = 12
TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504}

_PREFERRED_ITEM_KEYS = (
    "items",
    "objs",
    "item_list",
    "music_list",
    "word_list",
    "trending_list",
    "category_list",
    "user_list",
    "authors",
    "aweme_list",
    "hot_list",
    "data_list",
    "records",
    "results",
    "list",
    "catalog",
    "data",
)
_NON_ITEM_KEYS = {
    "extra",
    "log_pb",
    "base_resp",
    "BaseResp",
    "page",
    "pagination",
    "trends",
    "fans_trends",
    "url_list",
    "banner_url",
    "banner_dark",
    "banner_light",
    "word_cover",
    "cover_hd",
    "cover_large",
    "cover_medium",
    "cover_thumb",
}


# These are platform/public endpoints from https://docs.tikhub.io/llms.txt.
# Targeted account, video, DOU+, and logged-in creator analytics are kept out
# of the unattended daily job because they require a target or private data.
_ALL_PUBLIC_ENDPOINTS: tuple[dict[str, Any], ...] = (
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


# 信息台只保留两个榜：热门榜（热点榜）与内容榜单。
# 其余分类（热搜 / 音乐 / 话题 / 搜索 / 账号榜 / 创作者中心 / 星图 …）不再请求 TikHub。
INFORMATION_DESK_CATEGORIES: tuple[str, ...] = ("内容榜", "热点榜")   # 内容榜排前面
PUBLIC_DAILY_ENDPOINTS: tuple[dict[str, Any], ...] = tuple(
    endpoint for endpoint in _ALL_PUBLIC_ENDPOINTS if endpoint["category"] in INFORMATION_DESK_CATEGORIES
)
INFORMATION_DESK_ENDPOINT_KEYS: frozenset[str] = frozenset(item["key"] for item in PUBLIC_DAILY_ENDPOINTS)


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


def _nested_value(value: dict[str, Any], kind: str) -> Any:
    if kind in {"url", "cover_url"}:
        nested_keys = ("url", "url_list", "share_url", "item_url", "web_url", "homepage_url", "cover_hd", "cover_large", "cover_medium", "cover_thumb", "avatar_uri", "uri")
    elif kind == "id":
        nested_keys = ("id", "id_str", "uid", "user_id", "author_id", "code", "value")
    elif kind == "title":
        nested_keys = ("title", "item_title", "name", "display_name", "label", "word", "keyword", "desc", "description")
    else:
        nested_keys = ("author", "author_name", "nick_name", "nickname", "username", "name", "display_name")
    for key in nested_keys:
        nested = value.get(key)
        if isinstance(nested, dict):
            nested = _nested_value(nested, kind)
        if isinstance(nested, list):
            nested = next((entry for entry in nested if isinstance(entry, str) and entry.strip()), None)
        if nested not in (None, "", [], {}):
            return nested
    return None


def _first_value(item: dict[str, Any], keys: tuple[str, ...], kind: str = "") -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            if isinstance(value, dict):
                value = _nested_value(value, kind)
                if value in (None, "", [], {}):
                    continue
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
    # TikHub wraps different APIs with different semantic list names. Prefer
    # those names before walking arbitrary nested values, otherwise a banner's
    # three image URLs can be mistaken for the actual brand category list.
    for key in _PREFERRED_ITEM_KEYS:
        if key not in value:
            continue
        nested = value.get(key)
        if isinstance(nested, list):
            return nested
        found = _extract_items(nested, depth + 1)
        if found:
            return found
    for key, nested in value.items():
        if key in _NON_ITEM_KEYS or key.endswith("_url"):
            continue
        found = _extract_items(nested, depth + 1)
        if found:
            return found
    # Index/date and dictionary endpoints legitimately return a flat object
    # rather than a list. Turn only scalar fields at the data level into rows.
    if depth <= 1:
        scalar_rows = [
            {"key": key, "value": nested}
            for key, nested in value.items()
            if key not in _NON_ITEM_KEYS and not isinstance(nested, (dict, list)) and nested not in (None, "")
        ]
        if scalar_rows:
            return scalar_rows
    return []


def _stable_public_url(source_key: str | None, item: dict[str, Any], compact: dict[str, Any]) -> str:
    item_id = str(compact.get("id") or "").strip()
    if not item_id:
        return ""
    if source_key in {"hot_video", "low_fan_video", "high_play_video", "high_like_video", "high_fan_video", "creator_video_board", "creator_hot_spot", "creator_hot_topic", "creator_hot_music"}:
        return f"https://www.douyin.com/video/{quote(item_id, safe='')}"
    if source_key in {"hot_accounts", "xingtu_ranking"}:
        return f"https://www.douyin.com/user/{quote(item_id, safe='')}"
    if source_key in {"music_hot_search"}:
        return f"https://www.douyin.com/music/{quote(item_id, safe='')}"
    if source_key in {"hot_topic", "rising_topic", "hot_rise", "hot_city", "hot_challenge", "hot_total"}:
        keyword = str(compact.get("title") or "").strip()
        if keyword:
            return f"https://www.douyin.com/search/{quote(keyword, safe='')}?type=general"
    return ""


def _compact_item(value: Any, index: int, source_key: str | None = None) -> dict[str, Any] | None:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        label = _clip_string(value)
        return {"rank": index + 1, "title": label} if label else None
    if not isinstance(value, dict):
        return None
    item: dict[str, Any] = {"rank": index + 1}
    field_aliases = {
        "id": ("id", "aweme_id", "item_id", "video_id", "challenge_id", "topic_id", "music_id", "sentence_id", "uid", "user_id", "author_id", "code", "music_info", "attribute_datas"),
        "title": ("title", "item_title", "desc", "description", "sentence", "challenge_name", "topic_name", "music_name", "display_name", "name", "word", "keyword", "label", "music_info"),
        "author": ("author", "author_name", "nick_name", "nickname", "username", "user_name", "owner_nickname", "sec_uid", "music_info", "attribute_datas"),
        "url": ("url", "item_url", "share_url", "web_url", "video_url", "homepage_url", "author_link", "play_url"),
        "cover_url": ("item_cover_url", "cover_url", "cover", "cover_image", "image_url", "avatar_url", "origin_cover", "first_item_cover_url", "word_cover", "music_info", "attribute_datas"),
    }
    for output_key, aliases in field_aliases.items():
        value_found = _first_value(value, aliases, output_key)
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
    if "key" in value and "value" in value and value.get("value") not in (None, "", [], {}):
        item["id"] = _clip_string(value.get("key"), 128)
        item["title"] = _clip_string(value.get("key"))
        if isinstance(value.get("value"), (str, int, float)) and not isinstance(value.get("value"), bool):
            item["value"] = _clip_string(value.get("value"))
    metric_aliases = ("play_count", "view_count", "digg_count", "like_count", "comment_count", "share_count", "collect_count", "follower_count", "fans_count", "fans_cnt", "new_like_cnt", "new_fans_cnt", "hot_value", "hot_score", "heat", "score", "rank_value", "rank_diff", "play_cnt", "like_cnt", "comment_cnt", "share_cnt", "collect_cnt", "follow_cnt", "publish_cnt", "video_count", "avg_play_cnt", "duration", "follow_rate", "like_rate")
    metrics: dict[str, int | float] = {}
    for key in metric_aliases:
        number = _number(value.get(key))
        if number is not None:
            metrics[key] = number
    for nested_key in ("statistics", "stats", "metrics", "data", "attribute_datas"):
        nested = value.get(nested_key)
        if isinstance(nested, dict):
            for key in metric_aliases:
                if key not in metrics:
                    number = _number(nested.get(key))
                    if number is not None:
                        metrics[key] = number
    if metrics:
        item["metrics"] = dict(list(metrics.items())[:MAX_METRICS])
    if not item.get("title"):
        author = str(item.get("author") or "").strip()
        if source_key == "hot_accounts" and author:
            item["title"] = author
        elif source_key in {"hot_video", "low_fan_video", "high_play_video", "high_like_video", "high_fan_video"} and author:
            item["title"] = f"{author} 的热门视频"
    detail_parts = []
    for key in ("qualifier", "qualifier_id", "period", "date", "version", "sentence_tag_name"):
        detail = value.get(key)
        if detail not in (None, "", [], {}) and not isinstance(detail, (dict, list)):
            detail_parts.append(f"{key}={_clip_string(detail, 80)}")
    if detail_parts:
        item["detail"] = " · ".join(detail_parts)
    stable_url = _stable_public_url(source_key, value, item)
    if stable_url:
        item["url"] = stable_url
    if len(item) == 1:
        return None
    return item


def _compact_items(payload: Any, source_key: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, value in enumerate(_extract_items(payload)[:MAX_ITEMS_PER_SECTION]):
        item = _compact_item(value, index, source_key)
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
        # The billboard publishes a completed daily snapshot after the local
        # day rolls over. At 09:00 the previous local day is the latest
        # available range; querying today returns a valid empty response.
        raw_day = str(snapshot_date or _local_today().isoformat())
        try:
            day = (date.fromisoformat(raw_day) - timedelta(days=1)).isoformat().replace("-", "")
        except ValueError:
            day = str(_local_today() - timedelta(days=1)).replace("-", "")
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
        pricing = tikhub_price_breakdown(endpoint["key"], endpoint["path"])
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
                        "item_count": len(_compact_items(payload, endpoint["key"])),
                        "items": _compact_items(payload, endpoint["key"]),
                        "attempts": attempt + 1,
                        "provider_cost_usd": pricing["provider_cost_usd"],
                        "provider_cost_usd_total": round(float(pricing["provider_cost_usd"] or 0) * (attempt + 1), 6),
                        "estimated_lobster_credits": round(float(pricing["lobster_credits"] or 0) * (attempt + 1), 4),
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
            "attempts": 2,
            "provider_cost_usd": pricing["provider_cost_usd"],
            "provider_cost_usd_total": round(float(pricing["provider_cost_usd"] or 0) * 2, 6),
            "estimated_lobster_credits": round(float(pricing["lobster_credits"] or 0) * 2, 4),
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
    order = {name: index for index, name in enumerate(INFORMATION_DESK_CATEGORIES)}
    sections = sorted(
        (section for section in (row.sections or [])
         if str((section or {}).get("category") or "") in INFORMATION_DESK_CATEGORIES),
        key=lambda section: order.get(str((section or {}).get("category") or ""), len(order)),
    )
    endpoint_status = [
        item for item in (row.endpoint_status or [])
        if str((item or {}).get("key") or "") in INFORMATION_DESK_ENDPOINT_KEYS
    ]
    summary = dict(row.summary or {})
    if "endpoint_count" in summary or "success_count" in summary:
        summary["endpoint_count"] = len(PUBLIC_DAILY_ENDPOINTS)
        summary["success_count"] = sum(
            1 for item in endpoint_status if str((item or {}).get("status") or "") in ("ok", "success")
        )
    return {
        "id": row.id,
        "snapshot_date": row.snapshot_date,
        "fetched_at": row.fetched_at.isoformat() + "Z" if row.fetched_at else None,
        "status": row.status,
        "summary": summary,
        "sections": sections,
        "endpoint_status": endpoint_status,
        "error_message": row.error_message or "",
    }


def _match_text(item: dict[str, Any]) -> str:
    """条目里可搜的文本：标题 / 作者 / 描述 / 指标值。"""
    parts: list[str] = []
    for key in ("title", "name", "author", "detail", "value", "keyword"):
        value = item.get(key)
        if value:
            parts.append(str(value))
    metrics = item.get("metrics")
    if isinstance(metrics, dict):
        parts.extend(f"{k} {v}" for k, v in metrics.items())
    return " ".join(parts).lower()


SEARCH_DEFAULT_DAYS = 7


# 关键词实时搜抖音：拿用户输入的关键词直接问 TikHub（fetch_video_search_v2，0.01 USD/次）。
# 2026-10-11 实测：body 里 cursor 是 integer，sort_type/publish_time/filter_duration/content_type 是 string；
# 作品藏在 data.business_data[].data.aweme_info。
TIKHUB_VIDEO_SEARCH_PATH = "/api/v1/douyin/search/fetch_video_search_v2"
VIDEO_SEARCH_MAX_KEYWORDS = 3          # 每个关键词 1 次 TikHub 请求（0.01 USD），最多 3 个词
VIDEO_SEARCH_LIMIT = 60


def tikhub_video_search_price() -> dict[str, Any]:
    """关键词搜视频的单价（算力/次），给接口层做扣费与余额预检。"""
    return tikhub_price_breakdown("douyin_video_search", TIKHUB_VIDEO_SEARCH_PATH)


def _video_search_cards(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    cards = data.get("business_data") if isinstance(data, dict) else None
    if isinstance(cards, list):
        return [card for card in cards if isinstance(card, dict)]
    return [row for row in _extract_items(payload) if isinstance(row, dict)]


def _video_search_awemes(payload: Any) -> list[dict[str, Any]]:
    """搜索结果是「卡片」列表：作品在 card['data']['aweme_info']（图文/直播卡没有 aweme_info）。"""
    out: list[dict[str, Any]] = []
    for card in _video_search_cards(payload):
        node = card.get("data") if isinstance(card.get("data"), dict) else card
        if not isinstance(node, dict):
            continue
        aweme = node.get("aweme_info")
        if isinstance(aweme, dict):
            out.append(aweme)
            continue
        for key in ("aweme_list", "item_list", "items", "list"):
            rows = node.get(key)
            if isinstance(rows, list):
                out.extend(row for row in rows if isinstance(row, dict))
    return out


def _search_result_item(aweme: dict[str, Any], index: int, keyword: str) -> dict[str, Any] | None:
    """TikHub 搜索到的作品 → 信息台卡片同款结构（id 就是 aweme_id，可以直接跟创）。"""
    item_id = str(aweme.get("aweme_id") or aweme.get("awemeId") or aweme.get("item_id") or "").strip()[:128]
    author = aweme.get("author") if isinstance(aweme.get("author"), dict) else {}
    video = aweme.get("video") if isinstance(aweme.get("video"), dict) else {}
    stats = aweme.get("statistics") if isinstance(aweme.get("statistics"), dict) else {}
    title = _clip_string(aweme.get("desc") or aweme.get("title") or "")
    nickname = _clip_string(author.get("nickname") or author.get("nick_name") or author.get("unique_id") or "")
    if not title and nickname:
        title = f"{nickname} 的视频"
    cover = ""
    for node in (video.get("cover"), video.get("origin_cover"), video.get("dynamic_cover"), aweme.get("cover")):
        if not isinstance(node, dict):
            continue
        urls = node.get("url_list")
        if isinstance(urls, list) and urls:
            candidate = _clip_string(urls[0], 1000)
            if candidate.startswith(("http://", "https://")):
                cover = candidate
                break
    metrics: dict[str, int | float] = {}
    for key in ("play_count", "digg_count", "comment_count", "collect_count", "share_count"):
        number = _number(stats.get(key))
        if number not in (None, 0):   # 抖音搜索结果的播放量基本是 0，展示「播放 0」是噪音
            metrics[key] = number
    duration = _number(video.get("duration"))
    if duration is not None:
        metrics["duration_ms"] = duration
    created = _number(aweme.get("create_time"))
    item: dict[str, Any] = {"rank": index + 1, "section_title": "关键词搜索", "detail": f"关键词={keyword}"}
    if item_id:
        item["id"] = item_id
        item["item_id"] = item_id
        item["url"] = f"https://www.douyin.com/video/{quote(item_id, safe='')}"
    if title:
        item["title"] = title
    if nickname:
        item["author"] = nickname
    if cover:
        item["cover_url"] = cover
    if metrics:
        item["metrics"] = dict(list(metrics.items())[:MAX_METRICS])
    if created:
        try:
            item["published_at"] = datetime.fromtimestamp(created, SHANGHAI_TZ).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    item["keyword"] = keyword
    if not item.get("id") and not item.get("title"):
        return None          # 没有作品 id 也没有文案的卡片直接丢，别展示空壳
    return item


async def search_tikhub_douyin_videos(
    keywords: list[str],
    *,
    limit: int = VIDEO_SEARCH_LIMIT,
    sort_type: str = "0",
    publish_time: str = "0",
) -> dict[str, Any]:
    """拿用户关键词去 TikHub 实时搜抖音视频（每个词 1 次请求，0.01 USD/次）。

    2026-10-11：信息台搜索原来只翻服务端当天快照，用户搜「探店」=0 条；
    现在默认实时搜抖音，命中结果自带 aweme_id，可以直接「跟创」。
    """
    words = [str(word or "").strip() for word in (keywords or [])]
    words = [word for word in dict.fromkeys(words) if word][:VIDEO_SEARCH_MAX_KEYWORDS]
    pricing = tikhub_video_search_price()
    unit_cost = float(pricing.get("provider_cost_usd") or 0)
    result: dict[str, Any] = {
        "source": "tikhub",
        "endpoint": TIKHUB_VIDEO_SEARCH_PATH,
        "keywords": words,
        "items": [],
        "count": 0,
        "calls": 0,
        "errors": [],
        "paging": {},
        "provider_cost_usd": 0.0,
        "credits_per_call": float(pricing.get("lobster_credits") or 0),
    }
    if not words:
        result["errors"] = ["没有可用关键词"]
        return result
    key = _tikhub_key()
    if not key:
        result["errors"] = ["服务端未配置 TIKHUB_API_KEY"]
        return result
    max_items = max(1, min(200, int(limit or VIDEO_SEARCH_LIMIT)))
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0), headers=headers, trust_env=False) as client:
        for keyword in words:
            body = {
                "keyword": keyword,
                "cursor": 0,
                "sort_type": str(sort_type or "0"),
                "publish_time": str(publish_time or "0"),
                "filter_duration": "0",
                "content_type": "0",
                "search_id": "",
                "backtrace": "",
            }
            try:
                response = await client.post(_tikhub_base() + TIKHUB_VIDEO_SEARCH_PATH, json=body)
                status_code = int(response.status_code)
                payload = response.json()
            except Exception as exc:  # noqa: BLE001 上游异常不该让整个搜索 500
                result["calls"] += 1
                result["provider_cost_usd"] = round(unit_cost * result["calls"], 6)
                result["errors"].append(f"{keyword}: {type(exc).__name__} {str(exc)[:120]}")
                continue
            result["calls"] += 1
            result["provider_cost_usd"] = round(unit_cost * result["calls"], 6)
            if not _payload_success(payload, status_code):
                result["errors"].append(f"{keyword}: {_payload_message(payload) or ('TikHub HTTP %s' % status_code)}")
                continue
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict):
                result["paging"][keyword] = {
                    "cursor": data.get("cursor"),
                    "has_more": data.get("has_more"),
                    "search_id": data.get("search_id"),
                    "backtrace": data.get("backtrace"),
                }
            for aweme in _video_search_awemes(payload):
                item = _search_result_item(aweme, len(items), keyword)
                if not item:
                    continue
                dedupe = str(item.get("id") or item.get("title") or "")
                if dedupe and dedupe in seen:
                    continue
                if dedupe:
                    seen.add(dedupe)
                items.append(item)
                if len(items) >= max_items:
                    break
            if len(items) >= max_items:
                break
    result["items"] = items
    result["count"] = len(items)
    return result


def _item_dedupe_key(item: dict[str, Any]) -> str:
    for key in ("item_id", "aweme_id", "id", "url", "share_url"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return str(item.get("title") or item.get("name") or "")[:60]


def _search_fallback(snapshot: dict[str, Any], limit: int) -> dict[str, Any]:
    """一个词都没命中时给的兜底：当天最热的总榜 + 可点的推荐词，避免用户看到一片空白。"""
    top: list[dict[str, Any]] = []
    suggestions: list[str] = []
    for section in snapshot.get("sections") or []:
        key = str((section or {}).get("key") or "")
        items = (section or {}).get("items") or []
        if key in ("hot_total", "hot_video", "hot_rise"):
            top.extend(item for item in items if isinstance(item, dict))
        if key in ("hot_search_words", "hot_content_words", "hot_topic", "rising_search_words", "hot_challenge"):
            for item in items:
                if not isinstance(item, dict):
                    continue
                word = str(item.get("title") or item.get("name") or item.get("keyword") or "").strip()
                if word and word not in suggestions:
                    suggestions.append(word)
    return {"items": top[:limit], "suggestions": suggestions[:12]}


def search_information_desk(db: Any, keywords: list[str], limit: int = 60,
                            days: int = SEARCH_DEFAULT_DAYS) -> dict[str, Any]:
    """在最近 N 天的榜单快照里按关键词搜（本地检索，不额外请求 TikHub）。

    2026-10-11 改动：原来只搜「当天」快照（一天约 165 条），很多关键词直接 0 结果
    （生产实测「探店」=0、「火锅」=1）；改为默认检索最近 7 天，同一作品只保留最近一天的那条，
    仍然一个都没命中时返回当天最热的总榜（fallback_items）和推荐词（suggestions）。
    """
    words = [str(word or "").strip().lower() for word in (keywords or [])]
    words = [word for word in dict.fromkeys(words) if word]
    span = max(1, min(30, int(days or SEARCH_DEFAULT_DAYS)))
    rows = (
        db.query(DouyinPlatformSnapshot)
        .filter(DouyinPlatformSnapshot.status == "success")
        .order_by(DouyinPlatformSnapshot.snapshot_date.desc(), DouyinPlatformSnapshot.id.desc())
        .limit(span)
        .all()
    )
    if not rows or not words:
        return {"keywords": words, "snapshot_date": None, "days": span, "count": 0, "items": []}
    latest = rows[0]
    latest_payload = _snapshot_payload(latest) or {}
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        snapshot = _snapshot_payload(row)
        if not snapshot:
            continue
        day = str(snapshot.get("snapshot_date") or getattr(row, "snapshot_date", "") or "")
        for section in snapshot.get("sections") or []:
            for item in (section or {}).get("items") or []:
                if not isinstance(item, dict):
                    continue
                haystack = _match_text(item)
                if not haystack:
                    continue
                matched = [word for word in words if word in haystack]
                if not matched:
                    continue
                dedupe = _item_dedupe_key(item)
                if dedupe and dedupe in seen:
                    continue  # 同一作品多天都在榜：只留最近那一天
                if dedupe:
                    seen.add(dedupe)
                hits.append({
                    **item,
                    "snapshot_date": day,
                    "section_key": (section or {}).get("key") or "",
                    "section_title": (section or {}).get("title") or "",
                    "category": (section or {}).get("category") or "",
                    "matched": matched,
                })
    hits.sort(key=lambda row_: (-len(row_.get("matched") or []), str(row_.get("category") or ""), row_.get("rank") or 999))
    result: dict[str, Any] = {
        "keywords": words,
        "snapshot_date": latest_payload.get("snapshot_date") or str(getattr(latest, "snapshot_date", "") or ""),
        "days": span,
        "count": len(hits),
        "items": hits[: max(1, min(200, int(limit or 60)))],
    }
    if not hits:
        fallback = _search_fallback(latest_payload, min(20, max(1, int(limit or 60))))
        result["fallback_items"] = fallback["items"]
        result["suggestions"] = fallback["suggestions"]
        result["fallback_reason"] = "最近 %d 天的榜单里没有匹配「%s」的内容" % (span, "、".join(words))
    return result


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
        # This worker is platform-level (not charged to an end user), but the
        # provider cost is kept in the snapshot so operations can reconcile it.
        "provider_cost_usd": round(sum(float(result.get("provider_cost_usd_total") or result.get("provider_cost_usd") or 0) for result in results), 6),
        "estimated_lobster_credits": round(sum(float(result.get("estimated_lobster_credits") or 0) for result in results), 4),
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
