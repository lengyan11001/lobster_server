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
    wrapped = faq.with_customer_service_faq("怎么生成图片")
    assert wrapped.startswith(faq.FAQ_MARK_START)
    assert "客服百问百答" in wrapped
    assert wrapped.endswith("用户问题：怎么生成图片")
    assert faq.strip_customer_service_faq(wrapped) == "怎么生成图片"


def test_strip_keeps_plain_text_and_handles_broken_marker():
    assert faq.strip_customer_service_faq("怎么生成图片") == "怎么生成图片"
    broken = faq.FAQ_MARK_START + "\n(没有结束标记) 怎么生成图片"
    assert faq.strip_customer_service_faq(broken) == broken


def test_endpoints_accept_duty_mode_and_inject_faq():
    mastra = (ROOT / "backend/app/api/mastra_chat.py").read_text(encoding="utf-8")
    chat = (ROOT / "backend/app/api/chat.py").read_text(encoding="utf-8")
    h5 = (ROOT / "backend/app/api/h5_chat.py").read_text(encoding="utf-8")

    assert "duty_mode: str = Field(default=\"\", max_length=16)" in mastra
    assert "content = with_customer_service_faq(content)" in mastra
    assert 'payload.message = with_customer_service_faq(payload.message)' in chat
    assert "strip_customer_service_faq(r.user_message)" in chat
    assert '"content": strip_customer_service_faq(row.content),' in h5


def test_client_side_has_no_refusal_wrapper():
    client_chat = (ROOT.parent / "lobster_online/static/js/chat.js")
    if not client_chat.is_file():
        return
    js = client_chat.read_text(encoding="utf-8")
    assert "【客服模式】" not in js
