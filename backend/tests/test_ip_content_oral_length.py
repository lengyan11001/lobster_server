"""IP 口播文案长度：默认不限；带时长/字数时把硬性规则写进提示词（不截断）。"""
from datetime import datetime, timedelta

from backend.app.api import ip_content_studio as studio
from backend.app.api.ip_content_studio import _draft_requirements
from backend.app.api.ip_content_studio import _synced_within_24h as ch_synced


def test_oral_prompt_unlimited_by_default():
    text = _draft_requirements("task1_industry", "douyin", 1)
    assert "暂不限制字数或口播时长" in text


def test_oral_prompt_uses_target_seconds():
    text = _draft_requirements("task1_industry", "douyin", 1, 25)
    assert "暂不限制" not in text
    assert "90 到 115 字" in text
    assert "硬性字数限制" in text


def test_professional_oral_prompt_uses_target_seconds():
    text = _draft_requirements("task1_ip", "douyin", 1, 40)
    assert "暂不限制" not in text
    assert "144 到 184 字" in text


def test_manual_target_chars_reaches_prompt():
    text = _draft_requirements("task1_industry", "douyin", 1, 0, 150)
    assert "暂不限制" not in text
    assert "135 到 172 字" in text


def test_target_chars_wins_over_seconds():
    text = _draft_requirements("task1_ip", "douyin", 1, 60, 80)
    assert "72 到 92 字" in text
    assert "60 秒" not in text


def test_manual_style_zero_seconds_is_unlimited():
    text = _draft_requirements("task1_ip", "douyin", 1, 0)
    assert "暂不限制字数或口播时长" in text


def test_synced_within_24h_skips_recent():
    now = datetime(2026, 11, 11, 12, 0, 0)
    assert ch_synced(now - timedelta(hours=3), now) is True
    assert ch_synced(now - timedelta(hours=25), now) is False
    assert ch_synced(None, now) is False


def test_no_truncation_helper():
    assert not hasattr(studio, "_trim_oral_script")


def test_oral_body_length_counts_non_space():
    assert studio._oral_body_length({"body": "你好 世界\n再见"}) == 6
    assert studio._oral_body_length({}) == 0
