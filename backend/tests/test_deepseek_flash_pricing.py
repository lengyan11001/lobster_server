from datetime import datetime

from backend.app.services import sutui_pricing


def _beijing(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=sutui_pricing.ZoneInfo("Asia/Shanghai"))


def test_deepseek_flash_pricing_period_boundaries():
    # 2026-09-10 is Thursday.
    assert sutui_pricing._deepseek_flash_pricing_period(_beijing(2026, 9, 10, 8, 59)) == "off_peak"
    assert sutui_pricing._deepseek_flash_pricing_period(_beijing(2026, 9, 10, 9, 0)) == "peak"
    assert sutui_pricing._deepseek_flash_pricing_period(_beijing(2026, 9, 10, 11, 59)) == "peak"
    assert sutui_pricing._deepseek_flash_pricing_period(_beijing(2026, 9, 10, 12, 0)) == "off_peak"
    assert sutui_pricing._deepseek_flash_pricing_period(_beijing(2026, 9, 10, 14, 0)) == "peak"
    assert sutui_pricing._deepseek_flash_pricing_period(_beijing(2026, 9, 10, 17, 59)) == "peak"
    assert sutui_pricing._deepseek_flash_pricing_period(_beijing(2026, 9, 10, 18, 0)) == "off_peak"


def test_deepseek_flash_weekend_is_off_peak():
    # 2026-09-12 is Saturday, including hours that are peak on weekdays.
    assert sutui_pricing._deepseek_flash_pricing_period(_beijing(2026, 9, 12, 10, 0)) == "off_peak"
    assert sutui_pricing._deepseek_flash_pricing_period(_beijing(2026, 9, 12, 15, 0)) == "off_peak"


def test_deepseek_flash_usage_uses_current_period_rates(monkeypatch):
    usage = {
        "prompt_cache_hit_tokens": 1_000_000,
        "prompt_cache_miss_tokens": 1_000_000,
        "completion_tokens": 1_000_000,
    }
    monkeypatch.setattr(sutui_pricing, "_deepseek_flash_pricing_period", lambda now=None: "peak")
    assert sutui_pricing.credits_from_direct_api_usage("deepseek-flash", usage) == 1004

    monkeypatch.setattr(sutui_pricing, "_deepseek_flash_pricing_period", lambda now=None: "off_peak")
    assert sutui_pricing.credits_from_direct_api_usage("deepseek-flash", usage) == 502
