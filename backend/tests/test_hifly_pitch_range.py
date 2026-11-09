"""回归：HiFly 自带音色的「语调」范围是 0.1~2，客户端默认 0 不能直接报错。

2026-11-09 用户 13922883600（uid 235）做数字人：voice 是 HiFly 预设音色
（voice_row=False），客户端带 pitch=0，create-by-tts 走 0.1~2 校验后直接
400「语调必须在 0.1 到 2 之间」。这里把 0 当默认 1.0，越界夹紧。
"""
from __future__ import annotations

from backend.app.api.hifly_assets import _hifly_pitch_text


def test_default_zero_becomes_one():
    assert _hifly_pitch_text("0") == "1"
    assert _hifly_pitch_text(0) == "1"
    assert _hifly_pitch_text(None) == "1"


def test_valid_value_is_kept():
    assert _hifly_pitch_text("1.5") == "1.5"
    assert _hifly_pitch_text("0.1") == "0.1"
    assert _hifly_pitch_text("2") == "2"


def test_out_of_range_is_clamped():
    assert _hifly_pitch_text("-12") == "1"
    assert _hifly_pitch_text("0.05") == "0.1"
    assert _hifly_pitch_text("5") == "2"
    assert _hifly_pitch_text("abc") == "1"
