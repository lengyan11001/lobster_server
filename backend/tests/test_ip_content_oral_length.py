"""IP 口播文案长度：默认不限；带 target_seconds 时必须写进提示词。"""
from backend.app.api.ip_content_studio import _draft_requirements
from backend.app.api.ip_content_studio import _synced_within_24h as ch_synced


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


def test_manual_target_chars_reaches_prompt():
    text = _draft_requirements("task1_industry", "douyin", 1, 0, 150)
    assert "暂不限制" not in text
    assert "135 到 165 字" in text
    assert "目标 150 字" in text


def test_target_chars_wins_over_seconds():
    text = _draft_requirements("task1_ip", "douyin", 1, 60, 80)
    assert "72 到 88 字" in text
    assert "60 秒" not in text


from datetime import datetime, timedelta  # noqa: E402


def test_synced_within_24h_skips_recent():
    now = datetime(2026, 11, 11, 12, 0, 0)
    assert ch_synced(now - timedelta(hours=3), now) is True
    assert ch_synced(now - timedelta(hours=25), now) is False
    assert ch_synced(None, now) is False


def test_trim_oral_script_to_max_chars():
    from backend.app.api.ip_content_studio import _trim_oral_script

    text = "第一句占位内容，用来测试截断。" * 20
    out = _trim_oral_script(text, 60)
    assert len("".join(out.split())) <= 60
    assert out[-1] in "。！？；，"
    assert _trim_oral_script("短句，不动。", 60) == "短句，不动。"
