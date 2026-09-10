"""按模型区分的回复处理档案：不同模型互不干扰。"""
from __future__ import annotations

import importlib


def _profiles_module():
    from backend.app.services import model_reply_profiles as module

    importlib.reload(module)
    return module


def test_default_profile_keeps_history_and_ignores_deepseek_markup():
    module = _profiles_module()
    profile = module.profile_for("openai/gpt-5.6-sol")

    assert profile.id == "default"
    assert profile.guard_stream_fake_tools is False
    # 历史行为：DSML 标记仍会被清理
    assert module.fake_tool_hit("<\uff5cDSML\uff5c>tool_calls", profile) is True
    # DeepSeek 专有的 XML 假工具调用不归默认档案管
    text = "<listSystemCapabilities><query>文章</query></listSystemCapabilities>"
    assert module.fake_tool_hit(text, profile) is False
    message = {"role": "assistant", "content": text}
    assert module.sanitize_message_content(message, profile) is False
    assert message["content"] == text


def test_deepseek_profile_cleans_xml_fake_tool_calls():
    module = _profiles_module()
    profile = module.profile_for("deepseek-v4-flash")

    assert profile.id == "deepseek"
    assert profile.guard_stream_fake_tools is True
    text = "好的\n<listSystemCapabilities><query>文章 链接 内容提取</query></listSystemCapabilities>\n接入deepseek"
    assert module.fake_tool_hit(text, profile) is True
    message = {"role": "assistant", "content": text, "reasoning_content": "先想一下"}
    assert module.sanitize_message_content(message, profile) is True
    assert "<listSystemCapabilities>" not in message["content"]
    assert "接入deepseek" in message["content"]
    assert "reasoning_content" not in message


def test_only_fake_tool_text_becomes_fallback():
    module = _profiles_module()
    profile = module.profile_for("deepseek-chat")
    message = {"role": "assistant", "content": "<dispatchOnlineTask><capability_id>douyin</capability_id></dispatchOnlineTask>"}

    assert module.sanitize_message_content(message, profile) is True
    assert message["content"] == profile.fake_tool_fallback_text


def test_env_override_adds_a_profile_without_touching_others(monkeypatch):
    monkeypatch.setenv(
        "SUTUI_CHAT_MODEL_PROFILES_JSON",
        '[{"id":"glm","match":["glm-*"],"extra_fake_tool_patterns":["<glm_tool\\\\b"],"guard_stream_fake_tools":true}]',
    )
    module = _profiles_module()

    glm_profile = module.profile_for("glm-4-plus")
    assert glm_profile.id == "glm"
    assert module.fake_tool_hit("<glm_tool>", glm_profile) is True
    assert module.fake_tool_hit("<glm_tool>", module.profile_for("gpt-5.6-sol")) is False
    assert module.profile_for("deepseek-reasoner").id == "deepseek"


def test_model_profile_map_pins_a_model(monkeypatch):
    monkeypatch.setenv("SUTUI_CHAT_MODEL_PROFILE_MAP_JSON", '{"my-vendor-model":"deepseek"}')
    module = _profiles_module()

    profile = module.profile_for("my-vendor-model")
    assert profile.id == "deepseek"
    assert module.profile_for("other-vendor-model").id == "default"


def test_stream_guard_drops_split_fake_tool_block():
    module = _profiles_module()
    guard = module.guard_for("deepseek-v4-flash")

    assert guard.feed("<listSystem") == ""
    assert guard.feed("Capabilities><query>读文章</query></listSystemCapabilities>") == ""
    assert guard.feed("接入deepseek") == "接入deepseek"
    assert guard.flush() == ""


def test_stream_guard_passes_normal_text_immediately():
    module = _profiles_module()
    guard = module.guard_for("deepseek-v4-flash")

    assert guard.feed("你好，") == "你好，"
    assert guard.feed("我来处理") == "我来处理"
    assert guard.flush() == ""


def test_stream_guard_is_disabled_for_other_models():
    module = _profiles_module()
    guard = module.guard_for("openai/gpt-5.6-sol")

    text = "<listSystemCapabilities><query>文章</query></listSystemCapabilities>"
    assert guard.feed(text) == text
    assert guard.enabled is False


def test_stream_guard_drops_block_that_follows_normal_text():
    module = _profiles_module()
    guard = module.guard_for("deepseek-v4-flash")

    # 模型先吐普通文字，再吐假工具块（本次线上就是这样）
    assert guard.feed("\u8c03\n") == "\u8c03\n"
    assert guard.feed("<listSystemCapabilities>") == ""
    assert guard.feed("<query>\u6587\u7ae0</query>") == ""
    assert guard.feed("</listSystemCapabilities>") == ""
    assert guard.feed("\u597d\u7684") == "\u597d\u7684"
    assert guard.flush() == ""


def test_stream_guard_keeps_unrelated_angle_text():
    module = _profiles_module()
    guard = module.guard_for("deepseek-chat")

    assert guard.feed("\u4ef7\u683c<100 \u5143") == "\u4ef7\u683c<100 \u5143"
    assert guard.feed("<div>\u6b63\u5e38HTML") == "<div>\u6b63\u5e38HTML"
    assert guard.flush() == ""


def test_stream_guard_drops_unclosed_block_at_flush():
    module = _profiles_module()
    guard = module.guard_for("deepseek-chat")

    assert guard.feed("\u8c03\n") == "\u8c03\n"
    assert guard.feed("<dispatchOnlineTask><capability_id>douyin") == ""
    assert guard.flush() == ""
    assert guard.dropped is True


def test_nonstream_cleanup_drops_unclosed_tool_tag():
    module = _profiles_module()
    profile = module.profile_for("deepseek-chat")
    message = {
        "role": "assistant",
        "content": "\u8c03\n<dispatchOnlineTask><capability_id>douyin",
    }

    assert module.sanitize_message_content(message, profile) is True
    assert "dispatchOnlineTask" not in message["content"]


def test_nonstream_apply_cleans_only_matching_profile():
    module = _profiles_module()
    deepseek = module.profile_for("deepseek-v4-flash")
    default = module.profile_for("gpt-5.6-sol")
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "<getOnlineTaskStatus><task_id>1</task_id></getOnlineTaskStatus>",
                }
            }
        ]
    }

    assert module.apply_to_completion(payload, deepseek) is True
    assert payload["choices"][0]["message"]["content"] == deepseek.fake_tool_fallback_text

    payload2 = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "<getOnlineTaskStatus><task_id>1</task_id></getOnlineTaskStatus>",
                }
            }
        ]
    }
    assert module.apply_to_completion(payload2, default) is False
    assert payload2["choices"][0]["message"]["content"].startswith("<getOnlineTaskStatus>")


def test_proxy_nonstream_cleanup_is_per_model():
    from backend.app.api import sutui_chat_proxy as proxy

    fake = "<listSystemCapabilities><query>文章</query></listSystemCapabilities>"
    deepseek_payload = {"choices": [{"message": {"role": "assistant", "content": fake}}]}
    gpt_payload = {"choices": [{"message": {"role": "assistant", "content": fake}}]}

    assert proxy._strip_fake_tool_text_from_response(deepseek_payload, proxy._model_profile("deepseek-v4-flash")) is True
    assert deepseek_payload["choices"][0]["message"]["content"] == proxy._model_profile("deepseek-v4-flash").fake_tool_fallback_text

    # 别的模型（默认档）不受 DeepSeek 规则影响
    assert proxy._strip_fake_tool_text_from_response(gpt_payload, proxy._model_profile("openai/gpt-5.6-sol")) is False
    assert gpt_payload["choices"][0]["message"]["content"] == fake


def test_proxy_stream_event_guard_drops_split_fake_tool_text():
    from backend.app.api import sutui_chat_proxy as proxy

    guard = proxy._model_profiles.guard_for("deepseek-chat")
    first = proxy._guard_sse_event(
        b'data: {"choices":[{"delta":{"content":"<listSystem"}}]}',
        guard,
    )
    second = proxy._guard_sse_event(
        b'data: {"choices":[{"delta":{"content":"Capabilities><query>x</query></listSystemCapabilities>"}}]}',
        guard,
    )
    third = proxy._guard_sse_event(
        b'data: {"choices":[{"delta":{"content":"\\u597d\\u7684"}}]}',
        guard,
    )

    assert b"listSystem" not in first
    assert b"listSystem" not in second
    # 正常文本原样放行（可能是转义形式，取决于上游编码）
    assert third.strip().startswith(b"data:")
    assert b"listSystem" not in third


def test_proxy_tool_round_limit_is_configurable_per_model(monkeypatch):
    from backend.app.api import sutui_chat_proxy as proxy

    body = {
        "model": "deepseek-chat",
        "tools": [{"type": "function", "function": {"name": "x", "parameters": {"type": "object"}}}],
        "tool_choice": "auto",
        "messages": [{"role": "tool", "tool_call_id": "1", "content": "ok"}] * 5,
    }
    assert proxy._enforce_max_tool_call_rounds(body, "t", proxy._model_profile("deepseek-chat")) is True
    assert "tools" not in body
