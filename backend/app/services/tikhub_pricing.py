"""TiKHub endpoint pricing and Lobster-credit conversion.

TiKHub publishes endpoint prices in USD.  Lobster balances are RMB cents-like
credits (100 credits = CNY 1), so every paid TiKHub request must be converted
before it is deducted.  Keep this table deliberately explicit: an unknown
endpoint must never silently fall back to the old one-credit price, otherwise a
new expensive endpoint could be called at a loss.

The values below were read from TiKHub's ``get_all_endpoints_info`` endpoint on
2026-09-02.  ``TIKHUB_USD_TO_CNY_RATE`` can be raised by deployment operators
when the exchange rate moves; the default 7.5 is intentionally conservative.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal, ROUND_CEILING
from typing import Any, Optional

from ..core.config import settings
from .credits_amount import quantize_credits


_PRICE_TABLE_AS_OF = "2026-09-02"
_DEFAULT_USD_TO_CNY = Decimal("7.5")
_DEFAULT_MIN_CREDITS = Decimal("1")
_CREDIT_QUANT = Decimal("0.0001")

# Endpoint prices returned by TiKHub.  Paths not present here are intentionally
# treated as unknown instead of being charged at the legacy 1-credit default.
_ENDPOINT_COST_USD: dict[str, Decimal] = {
    # TiKHub's 0.01 USD endpoints used by Douyin search and WeChat Channels.
    "/api/v1/douyin/search/fetch_video_search_v2": Decimal("0.01"),
    "/api/v1/douyin/search/fetch_user_search": Decimal("0.01"),
    "/api/v1/wechat_channels/fetch_hot_words": Decimal("0.01"),
    "/api/v1/wechat_channels/fetch_search_latest": Decimal("0.01"),
    "/api/v1/wechat_channels/fetch_user_search": Decimal("0.01"),
    "/api/v1/wechat_channels/fetch_search_ordinary": Decimal("0.01"),
    "/api/v1/wechat_search/v2/fetch_search": Decimal("0.01"),
    "/api/v1/wechat_channels/v2/fetch_user_videos": Decimal("0.01"),
    "/api/v1/wechat_channels/v2/fetch_channel_id_to_username": Decimal("0.01"),
    "/api/v1/wechat_channels/v2/fetch_video_detail": Decimal("0.01"),
    # LinkedIn Web V2 prices are materially higher than the legacy default.
    "/api/v1/linkedin/web_v2/get_company_profile": Decimal("0.008"),
    "/api/v1/linkedin/web_v2/get_user_profile": Decimal("0.008"),
    "/api/v1/linkedin/web_v2/get_user_posts": Decimal("0.05"),
    "/api/v1/linkedin/web_v2/get_company_posts": Decimal("0.05"),
    "/api/v1/linkedin/web_v2/get_post_detail": Decimal("0.001"),
    "/api/v1/linkedin/web_v2/get_post_comments": Decimal("0.05"),
    "/api/v1/linkedin/web_v2/search_jobs": Decimal("0.05"),
    # Alibaba public-signal endpoints and all remaining current proxy/table
    # endpoints are 0.001 USD according to the same TiKHub catalog.
    "/api/v1/tiktok/shop/web/fetch_search_products_list": Decimal("0.001"),
    "/api/v1/instagram/v1/fetch_search": Decimal("0.001"),
    "/api/v1/twitter/web/fetch_search_timeline": Decimal("0.001"),
    "/api/v1/twitter/web/fetch_latest_post_comments": Decimal("0.001"),
    "/api/v1/twitter/web/fetch_trending": Decimal("0.001"),
    "/api/v1/twitter/web/fetch_user_followers": Decimal("0.001"),
    "/api/v1/twitter/web/fetch_user_followings": Decimal("0.001"),
    "/api/v1/twitter/web/fetch_user_post_tweet": Decimal("0.001"),
    "/api/v1/twitter/web/fetch_user_profile": Decimal("0.001"),
    "/api/v1/reddit/app/fetch_dynamic_search": Decimal("0.001"),
    "/api/v1/reddit/app/fetch_search_typeahead": Decimal("0.001"),
    "/api/v1/reddit/app/fetch_post_details": Decimal("0.001"),
    "/api/v1/reddit/app/fetch_post_comments": Decimal("0.001"),
    "/api/v1/reddit/app/fetch_user_profile": Decimal("0.001"),
    "/api/v1/reddit/app/fetch_user_posts": Decimal("0.001"),
    "/api/v1/reddit/app/fetch_user_comments": Decimal("0.001"),
    "/api/v1/reddit/app/fetch_subreddit_feed": Decimal("0.001"),
    "/api/v1/tiktok/web/fetch_search_video": Decimal("0.001"),
    "/api/v1/tiktok/web/fetch_search_user": Decimal("0.001"),
    "/api/v1/tiktok/web/fetch_user_profile": Decimal("0.001"),
    "/api/v1/tiktok/web/fetch_user_post": Decimal("0.001"),
    "/api/v1/tiktok/web/fetch_post_comment": Decimal("0.001"),
    "/api/v1/douyin/app/v3/fetch_hot_search_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_total_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_total_video_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_total_topic_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_total_search_list": Decimal("0.001"),
    "/api/v1/douyin/creator/fetch_user_search": Decimal("0.001"),
    "/api/v1/douyin/search/fetch_user_search_v2": Decimal("0.001"),
    "/api/v1/douyin/app/v3/fetch_user_post_videos": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_rise_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_city_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_challenge_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_total_low_fan_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_total_high_play_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_total_high_like_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_total_high_fan_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_total_high_topic_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_total_high_search_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_total_hot_word_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_account_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_calendar_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_hot_category_list": Decimal("0.001"),
    "/api/v1/douyin/billboard/fetch_city_list": Decimal("0.001"),
    "/api/v1/douyin/app/v3/fetch_live_hot_search_list": Decimal("0.001"),
    "/api/v1/douyin/app/v3/fetch_music_hot_search_list": Decimal("0.001"),
    "/api/v1/douyin/app/v3/fetch_brand_hot_search_list": Decimal("0.001"),
    "/api/v1/douyin/index/fetch_current_hot_topic": Decimal("0.001"),
    "/api/v1/douyin/index/fetch_hot_words": Decimal("0.001"),
    "/api/v1/douyin/index/fetch_hot_trend_word": Decimal("0.001"),
    "/api/v1/douyin/index/fetch_all_valid_date": Decimal("0.001"),
    "/api/v1/douyin/index/fetch_content_valid_date": Decimal("0.001"),
    "/api/v1/douyin/index/fetch_content_publish_trend": Decimal("0.001"),
    "/api/v1/douyin/creator/fetch_creator_material_center_billboard": Decimal("0.001"),
    "/api/v1/douyin/creator/fetch_creator_hot_spot_billboard": Decimal("0.001"),
    "/api/v1/douyin/creator/fetch_creator_hot_topic_billboard": Decimal("0.001"),
    "/api/v1/douyin/creator/fetch_creator_hot_music_billboard": Decimal("0.001"),
    "/api/v1/douyin/xingtu_v2/get_ranking_list_catalog": Decimal("0.001"),
    "/api/v1/douyin/xingtu_v2/get_content_trend_guide": Decimal("0.001"),
    "/api/v1/douyin/xingtu_v2/get_excellent_case_category_list": Decimal("0.001"),
    "/api/v1/douyin/xingtu_v2/get_ip_activity_industry_list": Decimal("0.001"),
}


def _configured_rate() -> Decimal:
    raw: Any = getattr(settings, "tikhub_usd_to_cny_rate", None)
    if raw in (None, ""):
        raw = os.environ.get("TIKHUB_USD_TO_CNY_RATE")
    try:
        value = Decimal(str(raw or _DEFAULT_USD_TO_CNY))
    except Exception:
        value = _DEFAULT_USD_TO_CNY
    # Do not allow an accidental zero/negative rate to make paid calls free.
    return value if value > 0 else _DEFAULT_USD_TO_CNY


def _configured_minimum() -> Decimal:
    raw: Any = getattr(settings, "tikhub_query_unit_credits", None)
    if raw in (None, ""):
        raw = os.environ.get("TIKHUB_QUERY_UNIT_CREDITS")
    try:
        value = Decimal(str(raw or _DEFAULT_MIN_CREDITS))
    except Exception:
        value = _DEFAULT_MIN_CREDITS
    return value if value > 0 else _DEFAULT_MIN_CREDITS


def _configured_overrides() -> dict[str, Decimal]:
    """Read optional JSON path→USD overrides without exposing secrets.

    This is useful if TiKHub changes a price before a code release. Invalid
    entries are ignored and the checked-in table remains the source of truth.
    """

    raw = getattr(settings, "tikhub_endpoint_costs_json", None) or os.environ.get("TIKHUB_ENDPOINT_COSTS_JSON")
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, Decimal] = {}
    for path, value in data.items():
        path_text = str(path or "").strip()
        if not path_text.startswith("/"):
            continue
        try:
            cost = Decimal(str(value))
        except Exception:
            continue
        if cost >= 0:
            out[path_text] = cost
    return out


def endpoint_cost_usd(endpoint_path: str) -> Optional[Decimal]:
    """Return the known TiKHub USD unit cost for an endpoint path."""

    path = str(endpoint_path or "").strip()
    if not path:
        return None
    override = _configured_overrides().get(path)
    if override is not None:
        return override
    return _ENDPOINT_COST_USD.get(path)


def _ceil_credits(value: Decimal) -> Decimal:
    """Round up to the ledger precision so conversion can never undercharge."""

    if value <= 0:
        return Decimal("0.0000")
    units = (value / _CREDIT_QUANT).to_integral_value(rounding=ROUND_CEILING)
    return quantize_credits(units * _CREDIT_QUANT)


def query_price(
    query_type: str,
    *,
    endpoint_path: str = "",
    require_known: bool = False,
) -> Decimal:
    """Return Lobster credits for one successful TiKHub request.

    ``require_known`` is available to callers that need fail-closed behavior
    for a newly added endpoint.  Current callers use paths from the audited
    table; the small 0.001-USD default covers ordinary catalog endpoints while
    the configured minimum preserves the historic 1-credit floor.
    """

    cost_usd = endpoint_cost_usd(endpoint_path)
    if cost_usd is None:
        if require_known:
            raise ValueError(f"未配置 TiKHub 端点价格：{query_type or endpoint_path}")
        return quantize_credits(_configured_minimum())
    if cost_usd == 0:
        return Decimal("0.0000")
    converted = cost_usd * _configured_rate() * Decimal("100")
    # Keep at least the configured legacy floor for the 0.001 USD endpoints;
    # expensive endpoints are charged strictly by their published price.
    return _ceil_credits(max(converted, _configured_minimum()))


def price_breakdown(query_type: str, endpoint_path: str) -> dict[str, Any]:
    """Serializable pricing details for audit logs and diagnostics."""

    cost_usd = endpoint_cost_usd(endpoint_path)
    credits = query_price(query_type, endpoint_path=endpoint_path)
    rate = _configured_rate()
    return {
        "query_type": str(query_type or ""),
        "endpoint": str(endpoint_path or ""),
        "provider_cost_usd": float(cost_usd) if cost_usd is not None else None,
        "usd_to_cny_rate": float(rate),
        "credits_per_cny": 100,
        "lobster_credits": float(credits),
        "price_table_as_of": _PRICE_TABLE_AS_OF,
    }


def known_price_table() -> dict[str, float]:
    """Expose a copy for tests/admin diagnostics without mutable state."""

    table = dict(_ENDPOINT_COST_USD)
    table.update(_configured_overrides())
    return {key: float(value) for key, value in table.items()}
