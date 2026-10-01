"""客服模式：把百问百答交给 LLM（不做问题判断/隔离）。"""
from __future__ import annotations

from pathlib import Path

from backend.app.services import customer_service_faq as faq

ROOT = Path(__file__).resolve().parents[2]


def test_faq_file_is_shipped_with_server():
    text = faq.customer_service_faq_text()
    assert "客服百问百答" in text
    assert text.count("**Q") >= 100
    assert "同城爆款" in text


def test_wrap_and_strip_round_trip():
    wrapped = faq.with_customer_service_faq("图片怎么生成")
    assert wrapped.startswith(faq.FAQ_MARK_START)
    assert wrapped.endswith(faq.FAQ_MARK_END)
    assert faq.strip_customer_service_faq(wrapped) == "图片怎么生成"


def test_retrieval_picks_operation_steps_for_image_question():
    """问「图片怎么生成」必须命中操作步骤条目（Q105/Q106），且注入体量可控（防被下游截断）。"""
    hits = faq.retrieve_faq_entries("图片怎么生成")
    ids = [item["id"] for item in hits]
    assert "Q105" in ids and "Q106" in ids
    assert ids[0] in {"Q105", "Q106"}

    wrapped = faq.with_customer_service_faq("图片怎么生成")
    assert "Q105" in wrapped and "图片创作" in wrapped
    assert len(wrapped) < 6000
    assert "客服百问百答目录" in wrapped


def test_retrieval_picks_video_entries_and_falls_back():
    video_hits = faq.retrieve_faq_entries("同城爆款视频怎么生成")
    video_ids = [item["id"] for item in video_hits]
    assert "Q107" in video_ids

    # 完全无关的问题也有兜底（不会注入空内容）
    fallback = faq.retrieve_faq_entries("zzzzzzz")
    assert fallback


def test_strip_keeps_plain_text_and_handles_broken_marker():
    assert faq.strip_customer_service_faq("怎么生成图片") == "怎么生成图片"
    broken = faq.FAQ_MARK_START + "\n(没有结束标记) 怎么生成图片"
    assert faq.strip_customer_service_faq(broken) == broken


def test_endpoints_accept_duty_mode_and_use_two_stage_context():
    mastra = (ROOT / "backend/app/api/mastra_chat.py").read_text(encoding="utf-8")
    chat = (ROOT / "backend/app/api/chat.py").read_text(encoding="utf-8")
    h5 = (ROOT / "backend/app/api/h5_chat.py").read_text(encoding="utf-8")

    assert "duty_mode: str = Field(default=\"\", max_length=16)" in mastra
    # 两段式：先让检索 LLM 看目录挑章节，再把相关章节发给回答模型（带最近对话）
    assert "content = build_service_context(" in mastra
    assert "_recent_chat_text(db, owner.id, session.id)" in mastra
    assert "payload.message = await asyncio.to_thread(" in chat
    assert "build_service_context," in chat
    assert "strip_customer_service_faq(r.user_message)" in chat
    assert '"content": strip_customer_service_faq(row.content),' in h5


def test_two_stage_outline_and_chapters():
    chapters = faq.faq_chapter_list()
    assert len(chapters) >= 10
    outline = faq.faq_index_text()
    assert "操作步骤速查" in outline

    # 没带鉴权（LLM 不可用）时退化为关键词检索，仍必须命中操作步骤
    context = faq.build_service_context("图片怎么生成", history_text="", auth_header="")
    assert "Q105" in context and "Q106" in context
    assert len(context) < 8000
    assert "检索方式：keyword" in context


def test_context_strips_back_to_the_original_question():
    context = faq.build_service_context("H5 上同城爆款怎么做", history_text="用户：怎么生成图片\n助手：…", auth_header="")
    assert faq.strip_customer_service_faq(context) == "H5 上同城爆款怎么做"


def test_client_side_has_no_refusal_wrapper():
    client_chat = (ROOT.parent / "lobster_online/static/js/chat.js")
    if not client_chat.is_file():
        return
    js = client_chat.read_text(encoding="utf-8")
    assert "【客服模式】" not in js

def test_reply_instruction_forbids_labels_and_internal_notes():
    context = faq.build_service_context("图片怎么生成", history_text="", auth_header="")
    assert "只输出「可以直接复制发给客户」的那一段答复" in context
    assert "禁止写「【可直接发给客户】」「【内部提示】」" in context


def test_clean_service_reply_strips_labels_and_internal_part():
    raw = (
        "【可直接发给客户】\n"
        "您好，在客户端左侧「AI营销创作 → 图片创作」里写提示词就能生成图片哦～\n"
        "【内部提示】\n"
        "客户问的是图片生成，命中 Q105；建议补一条 H5 步骤。"
    )
    cleaned = faq.clean_service_reply(raw)
    assert cleaned == "您好，在客户端左侧「AI营销创作 → 图片创作」里写提示词就能生成图片哦～"
    assert "内部提示" not in cleaned
    assert "Q105" not in cleaned

    # 没标记时就原样返回
    assert faq.clean_service_reply("直接一段话") == "直接一段话"
