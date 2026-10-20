# -*- coding: utf-8 -*-
"""TikTok 关键词搜索必须走 app/v3 接口并能解析 search_item_list（2026-10-20）。

线上事故：/api/v1/tiktok/web/fetch_search_video 需要网页 cookie，我们没传，
TikHub 117 次调用全部 400，用户 31（18823851682）的采集直接失败。
现在改用 app/v3/fetch_video_search_result，返回结构是
    data.search_item_list = [{"aweme_info": {...}}, ...]
这里锁住接口映射和解析。
"""
from __future__ import annotations

from backend.app.api import ip_content_studio as studio
from backend.app.services import tikhub_pricing


def test_tiktok_search_video_uses_app_v3_endpoint():
    spec = studio._ENDPOINTS["tiktok_search_video"]
    assert spec["path"] == "/api/v1/tiktok/app/v3/fetch_video_search_result"
    assert "keyword" in spec["allowed_params"]
    assert "region" in spec["allowed_params"]
    assert "cookie" not in spec["allowed_params"]
    assert spec.get("default_params", {}).get("region") == "US"


def test_new_tiktok_endpoint_has_price():
    cost = tikhub_pricing.endpoint_cost_usd("/api/v1/tiktok/app/v3/fetch_video_search_result")
    assert cost is not None and cost > 0


def test_collect_items_unwraps_search_item_list():
    payload = {
        "code": 200,
        "data": {
            "aweme_list": [],
            "search_item_list": [
                {"aweme_info": {"aweme_id": "1", "desc": "a"}},
                {"aweme_info": {"aweme_id": "2", "desc": "b"}},
            ],
        },
    }
    items = studio._collect_items(payload)
    assert [item["aweme_id"] for item in items] == ["1", "2"]


def test_collect_items_skips_empty_aweme_list():
    payload = {"data": {"aweme_list": [], "item_list": [{"aweme_id": "9"}]}}
    items = studio._collect_items(payload)
    assert items and items[0]["aweme_id"] == "9"
