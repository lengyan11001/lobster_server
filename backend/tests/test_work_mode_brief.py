"""工作模式：预注入「简要目录」给模型（少一次模型往返）= A 方案。

用户口径（2026-10-01）：工作模式参照客服模式，先把简要信息给 LLM；不用灰度，默认开。
"""
from __future__ import annotations

from pathlib import Path

from backend.app.services import work_mode_brief as brief

ROOT = Path(__file__).resolve().parents[2]


def test_block_contains_capabilities_and_memory(monkeypatch):
    monkeypatch.delenv("WORK_MODE_BRIEF_CONTEXT", raising=False)
    monkeypatch.setattr(brief, "_capability_lines", lambda db: ["- image.generate：文生图", "- video.generate：图生视频"])
    monkeypatch.setattr(brief, "_memory_lines", lambda db, user_id, installation_id="": ["- mem_abc：产品资料"])

    block = brief.build_brief_block(object(), 31)
    assert block.startswith(brief.WORK_BRIEF_START)
    assert block.endswith(brief.WORK_BRIEF_END)
    assert "image.generate" in block and "video.generate" in block
    assert "mem_abc" in block
    assert "listSystemCapabilities" in block and "readPersonalMemoryDocument" in block


def test_env_switch_can_disable(monkeypatch):
    monkeypatch.setenv("WORK_MODE_BRIEF_CONTEXT", "0")
    assert brief.build_brief_block(object(), 31) == ""


def test_prepend_and_strip_round_trip(monkeypatch):
    monkeypatch.delenv("WORK_MODE_BRIEF_CONTEXT", raising=False)
    monkeypatch.setattr(brief, "_capability_lines", lambda db: ["- image.generate：文生图"])
    monkeypatch.setattr(brief, "_memory_lines", lambda db, user_id, installation_id="": [])

    wrapped = brief.maybe_prepend_brief("帮我做张图", object(), 31)
    assert wrapped.startswith(brief.WORK_BRIEF_START)
    assert wrapped.endswith("帮我做张图")
    assert brief.strip_work_brief(wrapped) == "帮我做张图"

    # 已经注入过就不重复注入
    assert brief.maybe_prepend_brief(wrapped, object(), 31) == wrapped
    # 没注入过就原样返回
    assert brief.strip_work_brief("普通消息") == "普通消息"


def test_empty_index_does_not_inject(monkeypatch):
    monkeypatch.delenv("WORK_MODE_BRIEF_CONTEXT", raising=False)
    monkeypatch.setattr(brief, "_capability_lines", lambda db: [])
    monkeypatch.setattr(brief, "_memory_lines", lambda db, user_id, installation_id="": [])
    assert brief.build_brief_block(object(), 31) == ""
    assert brief.maybe_prepend_brief("你好", object(), 31) == "你好"


def test_endpoints_use_brief_context():
    mastra = (ROOT / "backend/app/api/mastra_chat.py").read_text(encoding="utf-8")
    h5 = (ROOT / "backend/app/api/h5_chat.py").read_text(encoding="utf-8")
    chat = (ROOT / "backend/app/api/chat.py").read_text(encoding="utf-8")

    assert "content = maybe_prepend_brief(content, db, owner.id" in mastra
    assert "strip_work_brief(strip_customer_service_faq(row.content))" in h5
    assert "strip_work_brief(strip_customer_service_faq(r.user_message))" in chat
