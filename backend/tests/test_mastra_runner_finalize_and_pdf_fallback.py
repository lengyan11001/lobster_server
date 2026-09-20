"""对话助手两处硬伤的回归测试（2026-09-20）。

1. runner：模型最后只回工具调用/占位句时，不能把已经流式给用户的正文丢掉，
   也不能再让用户「请再说一次你要执行的任务」。
2. 素材解析：设计稿导出的 PDF 没有文字层，必须退化成「页面转图片 + 图像理解」，
   而不是直接 400（用户 116 的 X72 产品资料就是这样卡住的）。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from backend.app.api import h5_personal_settings
from backend.app.services import mastra_chat_runner, model_reply_profiles


# ── runner：流式正文不能被占位句覆盖 ──

def test_placeholder_reply_is_recognised():
    assert mastra_chat_runner._is_placeholder_reply(model_reply_profiles.DEFAULT_FALLBACK_TEXT) is True
    assert mastra_chat_runner._is_placeholder_reply("已经把 X72 的 5G 卖点整理成脚本大纲。") is False


def test_usable_streamed_reply_keeps_real_answer():
    parts = ["我先核对资料，", "确认 5G 卖点后再写脚本。下面是完整脚本结构：1 基本信息 2 分镜表 3 三秒钩子。"]
    text = mastra_chat_runner._usable_streamed_reply(parts)
    assert "分镜表" in text
    assert len(text) >= 30


def test_usable_streamed_reply_drops_placeholder_and_short_text():
    assert mastra_chat_runner._usable_streamed_reply([model_reply_profiles.DEFAULT_FALLBACK_TEXT]) == ""
    assert mastra_chat_runner._usable_streamed_reply(["好的。"]) == ""
    # 正文里夹着占位句时，占位句要被清掉、正文留下
    mixed = f"{model_reply_profiles.DEFAULT_FALLBACK_TEXT}这份 PDF 没有文字层，请发文字版或截图给我，我再写脚本。"
    cleaned = mastra_chat_runner._usable_streamed_reply([mixed])
    assert "PDF" in cleaned and model_reply_profiles.DEFAULT_FALLBACK_TEXT not in cleaned


def test_empty_reply_notice_is_actionable():
    notice = mastra_chat_runner._EMPTY_REPLY_NOTICE
    assert "请再说一次" not in notice
    assert "继续" in notice


# ── PDF 没有文字层 → 页面转图片走图像理解 ──

def test_document_text_with_pdf_vision_uses_page_images(monkeypatch):
    visual_blocks: list[dict] = []
    pages = [("X72-p1.png", b"\x89PNG-1"), ("X72-p2.png", b"\x89PNG-2")]

    def _raise(*_args, **_kwargs):
        raise HTTPException(status_code=400, detail="文件没有可写入记忆库的文本内容。")

    monkeypatch.setattr(h5_personal_settings, "_file_to_text", _raise)
    monkeypatch.setattr(
        h5_personal_settings,
        "_run_personal_source_io",
        lambda func, *args, **kwargs: asyncio.sleep(0, result=func(*args, **kwargs)),
    )
    monkeypatch.setattr(h5_personal_settings, "_pdf_page_images", lambda *_a, **_k: pages)

    text = asyncio.run(
        h5_personal_settings._document_text_with_pdf_vision("X72.pdf", ".pdf", b"%PDF-1.4", visual_blocks)
    )
    assert "没有可提取的文字层" in text
    assert len(visual_blocks) == 2
    assert visual_blocks[0]["type"] == "image_url"


def test_document_text_with_pdf_vision_reports_reason_when_no_pages(monkeypatch):
    visual_blocks: list[dict] = []

    def _raise(*_args, **_kwargs):
        raise HTTPException(status_code=400, detail="文件没有可写入记忆库的文本内容。")

    monkeypatch.setattr(h5_personal_settings, "_file_to_text", _raise)
    monkeypatch.setattr(
        h5_personal_settings,
        "_run_personal_source_io",
        lambda func, *args, **kwargs: asyncio.sleep(0, result=func(*args, **kwargs)),
    )
    monkeypatch.setattr(h5_personal_settings, "_pdf_page_images", lambda *_a, **_k: [])

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            h5_personal_settings._document_text_with_pdf_vision("X72.pdf", ".pdf", b"%PDF-1.4", visual_blocks)
        )
    assert excinfo.value.status_code == 400
    assert "文字版或截图" in str(excinfo.value.detail)


def test_normal_text_document_is_untouched(monkeypatch):
    monkeypatch.setattr(h5_personal_settings, "_file_to_text", lambda *_a, **_k: "正常文字资料")
    monkeypatch.setattr(
        h5_personal_settings,
        "_run_personal_source_io",
        lambda func, *args, **kwargs: asyncio.sleep(0, result=func(*args, **kwargs)),
    )
    text = asyncio.run(
        h5_personal_settings._document_text_with_pdf_vision("note.txt", ".txt", b"hello", [])
    )
    assert text == "正常文字资料"
