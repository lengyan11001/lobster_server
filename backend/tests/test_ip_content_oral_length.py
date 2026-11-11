"""IP 口播文案长度：默认不限；带 target_seconds 时必须写进提示词。"""
from backend.app.api.ip_content_studio import _draft_requirements


def test_oral_prompt_unlimited_by_default():
    text = _draft_requirements("task1_industry", "douyin", 1)
    assert "暂不限制字数或口播时长" in text


def test_oral_prompt_uses_target_seconds():
    text = _draft_requirements("task1_industry", "douyin", 1, 25)
    assert "暂不限制" not in text
    assert "90 到 110 字" in text
    assert "不得超过 110 字" in text
    assert "25 秒" in text


def test_professional_oral_prompt_uses_target_seconds():
    text = _draft_requirements("task1_ip", "douyin", 1, 40)
    assert "暂不限制" not in text
    assert "144 到 176 字" in text


def test_manual_style_zero_seconds_is_unlimited():
    text = _draft_requirements("task1_ip", "douyin", 1, 0)
    assert "暂不限制字数或口播时长" in text
