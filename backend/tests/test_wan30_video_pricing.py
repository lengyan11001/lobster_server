"""wan3.0（万相3.0）计费口径护栏。

口径（2026-09-18 确认）：
  · 用到 wan 时按 **1 秒 120 积分**收费（基础 60 积分/秒 × 全局倍率 2）；
  · 其他模型保持原有计费逻辑（grok-imagine-video-1.5 320/次、grok-video-3 160/次、gpt-image-2 60/张 …）。

本测试直接读仓库根目录的 ``comfly_pricing.json``（与线上 /opt/lobster-server/comfly_pricing.json
逐字节一致，sha256 752f6cd5…），所以谁改价、或改了全局倍率，这里会立刻红，改价必须显式改测试。
"""
from __future__ import annotations

import pytest

from mcp.comfly_upstream import _load_pricing, estimate_comfly_credits, lookup_comfly_model

_GLOBAL_MULTIPLIER_ENV_KEYS = ("USER_PRICE_MULTIPLIER", "COMFLY_USER_PRICE_MULTIPLIER")


@pytest.fixture(autouse=True)
def _pin_global_multiplier(monkeypatch):
    """固定走 JSON 里的全局倍率，避免本机/CI 环境变量让断言漂移。"""
    for key in _GLOBAL_MULTIPLIER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_wan30_entry_is_per_second_with_global_multiplier():
    entry = lookup_comfly_model("wan3.0-video")
    assert entry, "comfly_pricing.json 必须保留 wan3.0-video 条目"
    assert entry["price_type"] == "per_second"
    assert float(entry["price_per_unit"]) == 60
    # 条目本身不配倍率，统一走全局；否则会和全局倍率打架
    assert not entry.get("user_price_multiplier")
    assert float(_load_pricing().get("user_price_multiplier_default")) == 2


@pytest.mark.parametrize(
    "seconds,expected",
    [(5, 600), (10, 1200), (15, 1800), (30, 3600)],
)
def test_wan30_user_price_is_120_credits_per_second(seconds, expected):
    assert estimate_comfly_credits("wan3.0-video", {"duration": seconds}, for_user=True) == expected


def test_wan30_purchase_price_is_60_credits_per_second():
    """采购价口径（我们付给 DashScope 的折算）：60 积分/秒。"""
    assert estimate_comfly_credits("wan3.0-video", {"duration": 10}, for_user=False) == 600


def test_wan30_duration_defaults_to_5_seconds():
    """客户端没传秒数时按 5 秒估（与 estimate 内部兜底一致），避免出现 0 积分。"""
    assert estimate_comfly_credits("wan3.0-video", {}, for_user=True) == 600


def test_other_video_models_keep_original_pricing():
    assert estimate_comfly_credits("grok-imagine-video-1.5", {"duration": 10}, for_user=True) == 320
    assert estimate_comfly_credits("grok-video-3", {"duration": 10}, for_user=True) == 160


def test_image_models_keep_original_pricing():
    assert estimate_comfly_credits("gpt-image-2", {"num_images": 1}, for_user=True) == 60
    assert estimate_comfly_credits("nano-banana-2", {"num_images": 1}, for_user=True) == 84
