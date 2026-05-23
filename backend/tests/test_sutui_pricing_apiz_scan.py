from __future__ import annotations

from backend.app.services.sutui_pricing import estimate_credits_from_pricing
from backend.app.services.credits_amount import quantize_credits


def test_grok_video_per_second_from_apiz_docs():
    pricing = {
        "base_price": None,
        "price_type": "per_second",
        "per_second": 20,
        "examples": [
            {"description": "5秒", "price": 100},
            {"description": "10秒", "price": 200},
            {"description": "15秒", "price": 300},
        ],
    }

    assert estimate_credits_from_pricing(pricing, {"duration": 5}) == 100


def test_gpt_image_2_price_factors_quality_size_matrix():
    pricing = {
        "base_price": None,
        "price_type": "quality_size_matrix",
        "price_factors": [
            "1K: 2 / 8 / 32（low / medium / high）",
            "2K: 2 / 12 / 46（low / medium / high）",
            "4K: 4 / 22 / 82（low / medium / high）",
        ],
        "examples": [],
    }

    assert estimate_credits_from_pricing(pricing, {"resolution": "1K", "quality": "high"}) == 32
    assert estimate_credits_from_pricing(pricing, {"resolution": "2K", "quality": "medium", "num_images": 2}) == 24


def test_z_image_resolution_ratio_matrix_and_prompt_expansion():
    pricing = {
        "base_price": None,
        "price_type": "resolution_ratio_matrix",
        "examples": [
            {"description": "生成 4 张：单张价格 x 4", "price": 8},
            {"description": "提示词扩展：每次请求 +1 积分", "price": 3},
        ],
        "price_matrix": {
            "columns": ["分辨率", "1:1", "16:9", "9:16", "4:3", "3:4"],
            "rows": [
                ["1k", 3, 2, 2, 2, 2],
                ["2k", 9, 8, 8, 7, 7],
            ],
        },
    }

    assert estimate_credits_from_pricing(pricing, {"resolution": "1k", "image_size": "4:3"}) == 2
    assert estimate_credits_from_pricing(pricing, {"resolution": "2k", "image_size": "16:9", "num_images": 2}) == 16
    assert estimate_credits_from_pricing(
        pricing,
        {"resolution": "2k", "image_size": "16:9", "enable_prompt_expansion": True},
    ) == 9


def test_duration_price_uses_apiz_examples_not_base_as_per_second():
    pricing = {
        "base_price": 485.0,
        "price_type": "duration_price",
        "price_description": "按时长计费：480p: 43积分/s / 720p: 97积分/s",
        "examples": [
            {"description": "4秒 480p", "price": 172},
            {"description": "5秒 480p", "price": 215},
            {"description": "4秒 720p", "price": 388},
            {"description": "5秒 720p", "price": 485},
        ],
    }

    assert estimate_credits_from_pricing(pricing, {"duration": 5, "resolution": "480p"}) == 215
    assert estimate_credits_from_pricing(pricing, {"duration": 5, "resolution": "720p"}) == 485


def test_matrix_examples_parse_resolution_plus_duration():
    pricing = {
        "base_price": 480,
        "price_type": "matrix",
        "examples": [
            {"description": "720p + 4", "price": 480},
            {"description": "720p + 8", "price": 960},
            {"description": "1080p + 4", "price": 800},
            {"description": "1080p + 8", "price": 1600},
        ],
    }

    assert estimate_credits_from_pricing(pricing, {"duration": 4, "resolution": "1080p"}) == 800
    assert estimate_credits_from_pricing(pricing, {"duration": 8, "resolution": "720p"}) == 960


def test_per_second_actual_duration_uses_base_as_rate():
    pricing = {
        "base_price": 5,
        "price_type": "per_second_actual_duration",
        "examples": [
            {"description": "5秒视频", "price": 25},
            {"description": "10秒视频", "price": 50},
        ],
    }

    assert estimate_credits_from_pricing(pricing, {"duration": 5}) == 25


def test_fixed_plus_addons_from_apiz_docs():
    pricing = {
        "base_price": 150,
        "price_type": "fixed_plus_addons",
        "examples": [
            {"description": "单张正面图", "price": 150},
            {"description": "正面图 + PBR", "price": 210},
            {"description": "多视角输入", "price": 210},
            {"description": "PBR + 多视角 + 自定义面数", "price": 330},
        ],
    }

    assert estimate_credits_from_pricing(pricing, {}) == 150
    assert estimate_credits_from_pricing(pricing, {"enable_pbr": True}) == 210
    assert estimate_credits_from_pricing(
        pricing,
        {"enable_pbr": True, "back_image_url": "https://example.test/back.png", "face_count": 40000},
    ) == 330


def test_resolution_quantity_examples_and_web_search():
    pricing = {
        "base_price": 32,
        "price_type": "resolution_quantity",
        "examples": [
            {"description": "0.5K 生成 1 张", "price": 24},
            {"description": "1K 生成 1 张", "price": 32},
            {"description": "2K 生成 1 张", "price": 48},
            {"description": "4K 生成 1 张", "price": 64},
            {"description": "1K + web search", "price": 38},
        ],
    }

    assert estimate_credits_from_pricing(pricing, {"resolution": "4K"}) == 64
    assert estimate_credits_from_pricing(pricing, {"resolution": "1K", "enable_web_search": True}) == 38


def test_defaults_skip_visible_if_resolution_for_dynamic_model():
    pricing = {
        "base_price": 200,
        "price_type": "dynamic_per_second",
        "_param_defaults": {
            "model": "seedance2.0_fast_direct",
            "duration": 4,
        },
        "examples": [
            {"description": "fast 4秒", "price": 200},
            {"description": "标准 10秒", "price": 700},
            {"description": "fast vip 4秒", "price": 320},
            {"description": "标准 vip 720p 4秒", "price": 480},
            {"description": "标准 vip 1080p 4秒", "price": 800},
        ],
    }

    assert estimate_credits_from_pricing(pricing, {}) == 200


def test_explicit_ignored_resolution_does_not_override_dynamic_model_tier():
    pricing = {
        "base_price": 200,
        "price_type": "dynamic_per_second",
        "examples": [
            {"description": "fast 4秒", "price": 200},
            {"description": "标准 10秒", "price": 700},
            {"description": "fast vip 4秒", "price": 320},
            {"description": "标准 vip 720p 4秒", "price": 480},
            {"description": "标准 vip 1080p 4秒", "price": 800},
        ],
    }

    assert estimate_credits_from_pricing(
        pricing,
        {"model": "seedance2.0_fast_direct", "resolution": "720p", "duration": 4},
    ) == 200


def test_llm_market_usage_pricing(monkeypatch):
    from backend.app.services import sutui_pricing

    monkeypatch.setattr(
        sutui_pricing,
        "fetch_llm_market_pricing",
        lambda model_id: {
            "pricing_mode": "token",
            "input_price_credits_per_1m": 1000,
            "output_price_credits_per_1m": 4000,
        } if model_id == "gpt-4o" else None,
    )

    credits = sutui_pricing.credits_from_llm_market_usage(
        "gpt-4o",
        {"prompt_tokens": 1000, "completion_tokens": 500},
    )
    assert credits == quantize_credits("3.0")


def test_comfly_estimate_uses_global_user_multiplier(monkeypatch):
    from mcp import comfly_upstream

    monkeypatch.setenv("USER_PRICE_MULTIPLIER", "2")
    monkeypatch.delenv("COMFLY_USER_PRICE_MULTIPLIER", raising=False)
    monkeypatch.setattr(
        comfly_upstream,
        "_load_pricing",
        lambda: {
            "user_price_multiplier_default": 3,
            "models": {
                "test-comfly-model": {
                    "enabled": True,
                    "price_type": "per_call",
                    "price_per_unit": 100,
                }
            },
        },
    )

    assert comfly_upstream.estimate_comfly_credits("test-comfly-model", {}, for_user=True) == 200
