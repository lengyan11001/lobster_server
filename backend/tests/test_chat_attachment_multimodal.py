"""Online /chat 附件多模态注入（2026-10-16 修复“上传图片后模型说没收到图”）

背景：附件以前只以文本 URL 进 messages，指望模型自己调 image.understand；
2026-09-10 主模型切到 deepseek-flash 后不再稳定触发，模型于是回答“没收到图片”。
现在把图作为多模态 part 直给模型（服务端代理识别 has_images 并自动选视觉模型）。
"""
from __future__ import annotations

from backend.app.api import chat as chat_mod


def _payload(message, urls=None):
    return chat_mod.ChatRequest(message=message, attachment_image_urls=list(urls or []))


def test_image_attachment_becomes_multimodal_part():
    msg = chat_mod._build_user_message_with_attachments(
        _payload("理解一下这张图片", urls=["https://example.com/a.jpg"])
    )
    assert isinstance(msg["content"], list)
    assert msg["content"][0]["type"] == "text"
    assert msg["content"][1] == {"type": "image_url", "image_url": {"url": "https://example.com/a.jpg"}}


def test_video_attachment_is_not_injected_as_image_part():
    msg = chat_mod._build_user_message_with_attachments(
        _payload("看下这个", urls=["https://example.com/v.mp4", "https://example.com/a.PNG"])
    )
    types = [part["type"] for part in msg["content"]]
    assert types == ["text", "image_url"]
    assert msg["content"][1]["image_url"]["url"].endswith("a.PNG")


def test_without_attachment_message_stays_plain_text():
    msg = chat_mod._build_user_message_with_attachments(_payload("你好"))
    assert isinstance(msg["content"], str)


def test_hint_tells_model_not_to_call_understand_when_image_attached():
    msg = chat_mod._build_user_message_with_attachments(
        _payload("理解一下这张图片", urls=["https://example.com/a.jpg"])
    )
    text_part = msg["content"][0]["text"]
    assert "不要调用 image.understand" in text_part


def test_hint_keeps_tool_instruction_for_video_only_attachment():
    msg = chat_mod._build_user_message_with_attachments(
        _payload("理解一下这个视频", urls=["https://example.com/v.mp4"])
    )
    assert isinstance(msg["content"], str)
    assert "调用 image.understand" in msg["content"]
    assert "不要调用 image.understand" not in msg["content"]


def test_messages_have_image_parts_detects_multimodal_content():
    assert chat_mod._messages_have_image_parts(
        [{"role": "user", "content": [{"type": "text", "text": "x"}, {"type": "image_url", "image_url": {"url": "u"}}]}]
    ) is True
    assert chat_mod._messages_have_image_parts([{"role": "user", "content": "x"}]) is False
    assert chat_mod._messages_have_image_parts(None) is False
