"""热门视频跟创（原「抖音信息台做同款」）：模式提示词 + 视频来源校验。

2026-10-05 需求：信息台改名「热门视频跟创」，新增两种模式（复刻特效 / 复刻单人动作），
不同模式用不同提示词；视频来源支持用户粘贴链接或上传本地视频。
"""
from __future__ import annotations

import asyncio

from backend.app.services.douyin_imitation_video import (
    MODE_PROMPTS,
    mode_label,
    normalize_mode,
    prepare_imitation,
    prompt_for_mode,
)


def test_mode_prompts_are_distinct_and_normalized():
    assert set(MODE_PROMPTS) == {"person_swap", "effect_copy", "action_copy"}
    assert normalize_mode("effect_copy") == "effect_copy"
    assert normalize_mode("ACTION_COPY") == "action_copy"
    assert normalize_mode("谁也不是") == "person_swap"

    assert prompt_for_mode("effect_copy") == "参考视频的特效，将这个特效应用到图片中女人身上，场景为街边。"
    assert prompt_for_mode("action_copy") == "让图片中的人物模仿视频中的人的动作。"
    assert prompt_for_mode("person_swap") == MODE_PROMPTS["person_swap"]
    # 用户自己填了就用他的
    assert prompt_for_mode("effect_copy", "  我的提示词  ") == "我的提示词"

    assert mode_label("effect_copy") == "复刻特效"
    assert mode_label("action_copy") == "复刻单人动作"
    assert mode_label(None) == "复刻人物（换人）"


def test_prepare_imitation_needs_a_video_source():
    result = asyncio.run(prepare_imitation("https://cdn.example.com/a.jpg", "", "", mode="effect_copy"))
    assert result["ok"] is False
    assert "视频" in result["error"]


def test_prepare_imitation_rejects_bad_video_and_image_urls():
    result = asyncio.run(
        prepare_imitation("https://cdn.example.com/a.jpg", "", "", video_url="ftp://cdn.example.com/a.mp4")
    )
    assert result["ok"] is False
    assert "视频地址无效" in result["error"]

    result = asyncio.run(prepare_imitation("not-a-url", "123456", ""))
    assert result["ok"] is False
    assert "参考图" in result["error"]
