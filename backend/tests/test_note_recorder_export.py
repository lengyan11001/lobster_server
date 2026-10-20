from __future__ import annotations
from datetime import datetime
from types import SimpleNamespace

from backend.app.api.h5_recorder import _markdown_text, _srt_clock, _srt_text


def _row(**over):
    base = dict(
        id=1, file_name="a.webm", display_name="客户拜访", source_type="device",
        recorded_at=datetime(2026, 10, 20, 10, 30), created_at=datetime(2026, 10, 20, 10, 31),
        summary_text="聊了报价", key_points=["报价", "下周"],
        analysis={"todos": [{"title": "发报价", "owner": "小王", "due": "周五"}],
                  "outline": [{"title": "报价", "children": ["折扣"]}]},
        transcript_segments=[{"speaker": "A", "text": "你好", "start_ms": 0, "end_ms": 1500},
                             {"speaker": "B", "text": "报价多少", "start_ms": 1500, "end_ms": 3000}],
        transcript_text="", translated_text="Hello", translate_lang="English",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_srt_clock():
    assert _srt_clock(0) == "00:00:00,000"
    assert _srt_clock(3661000) == "01:01:01,000"


def test_srt_text_has_speaker_and_time():
    text = _srt_text(_row())
    assert "00:00:00,000 --> 00:00:01,500" in text
    assert "A：你好" in text
    assert "B：报价多少" in text


def test_markdown_has_todos_and_translation():
    text = _markdown_text(_row())
    assert "## 待办" in text and "发报价" in text and "小王" in text
    assert "## 译文（English）" in text and "Hello" in text
    assert "## 逐字稿" in text
