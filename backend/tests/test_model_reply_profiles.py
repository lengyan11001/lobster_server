"""假工具调用按结构识别，不依赖逐格式正则；模型差异只体现在策略开关上。"""
from __future__ import annotations

import importlib
import json

LT = "<"
GT = ">"


def _module():
    from backend.app.services import model_reply_profiles as module

    importlib.reload(module)
    return module


def _tag(name: str, body: str = "", *, attrs: str = "") -> str:
    return f"{LT}{name}{attrs}{GT}{body}{LT}/{name}{GT}"


def _fake_call_samples() -> list[tuple[str, str]]:
    query = "\u8bfb\u53d6\u94fe\u63a5 \u7f51\u9875\u5185\u5bb9"
    dsml = f"{LT}||DSML|| calls{GT}\n{LT}||DSML|| invoke name=x{GT}\n{LT}||DSML|| parameter name=query{GT}{query}"
    return [
        ("tag_form", _tag("listSystemCapabilities", _tag("query", query))),
        (
            "claude_form",
            _tag("tool_calls", _tag("invoke", f"{LT}parameter name=query{GT}{query}{LT}/parameter{GT}", attrs=' name="x"')),
        ),
        ("dsml_form", dsml),
        ("fenced_json", '```json\n{"tool_calls": [{"name": "dispatchOnlineTask"}]}\n```'),
        ("bare_json", json.dumps({"name": "dispatchOnlineTask", "arguments": {"capability_id": "douyin"}})),
        ("invented_tag", _tag("some_future_tool", _tag("query", query))),
    ]


def test_every_textual_tool_call_shape_is_stripped():
    module = _module()
    profile = module.profile_for("deepseek-flash")
    tools = ("listSystemCapabilities", "dispatchOnlineTask")

    for name, sample in _fake_call_samples():
        text = "\u8c03\n" + sample + "\n\u63a5\u5165 deepseek"
        cleaned, changed = module.strip_fake_tool_text(text, profile, tools)
        assert changed is True, name
        assert "listSystemCapabilities" not in cleaned, name
        assert "dispatchOnlineTask" not in cleaned, name
        assert "DSML" not in cleaned, name
        assert "\u63a5\u5165 deepseek" in cleaned, name


def test_plain_prose_is_never_touched():
    module = _module()
    profile = module.profile_for("deepseek-flash")
    samples = [
        "\u4ef7\u683c" + LT + "100 \u5143\uff0c\u5f88\u5212\u7b97",
        LT + "div" + GT + "\u666e\u901a HTML" + LT + "/div" + GT,
        "\u8fd9\u662f\u4e00\u6bb5\u6b63\u5e38\u6587\u5b57\uff0c\u6ca1\u6709\u5de5\u5177\u8c03\u7528",
        '{"\u540d\u5b57": "\u5f20\u4e09"}',
    ]
    for sample in samples:
        cleaned, changed = module.strip_fake_tool_text(sample, profile, ("listSystemCapabilities",))
        assert changed is False, sample
        assert cleaned == sample


def test_whole_message_becomes_fallback_when_only_fake_call():
    module = _module()
    profile = module.profile_for("deepseek-chat")
    message = {"role": "assistant", "content": _tag("dispatchOnlineTask", _tag("query", "x"))}

    assert module.sanitize_message_content(message, profile, ("dispatchOnlineTask",)) is True
    assert message["content"] == profile.fake_tool_fallback_text


def test_reasoning_content_is_a_per_model_switch():
    module = _module()
    deepseek = module.profile_for("deepseek-v4-flash")
    default = module.profile_for("openai/gpt-5.6-sol")

    msg_a = {"role": "assistant", "content": "ok", "reasoning_content": "thinking"}
    assert module.sanitize_message_content(msg_a, deepseek) is True
    assert "reasoning_content" not in msg_a

    msg_b = {"role": "assistant", "content": "ok", "reasoning_content": "thinking"}
    assert module.sanitize_message_content(msg_b, default) is False
    assert msg_b["reasoning_content"] == "thinking"


def test_stream_guard_drops_block_after_normal_text():
    module = _module()
    guard = module.guard_for("deepseek-flash", ("listSystemCapabilities",))

    emitted = guard.feed("\u8c03\n")
    emitted += guard.feed(LT + "listSystemCapabilities" + GT)
    emitted += guard.feed(_tag("query", "\u7f51\u9875\u5185\u5bb9"))
    emitted += guard.feed(LT + "/listSystemCapabilities" + GT)
    emitted += guard.feed("\n\u63a5\u5165 deepseek")
    emitted += guard.flush()

    assert "listSystemCapabilities" not in emitted
    assert "\u8c03\n" in emitted
    assert "\u63a5\u5165 deepseek" in emitted


def test_stream_guard_drops_split_json_tool_call():
    module = _module()
    guard = module.guard_for("deepseek-flash", ("dispatchOnlineTask",))

    emitted = guard.feed("\u597d\u7684\uff0c\u6211\u5148\u770b\u4e00\u4e0b ")
    emitted += guard.feed('{"name": "dispatchOnline')
    emitted += guard.feed('Task", "arguments": {"capability_id": "douyin"}}')
    emitted += guard.feed(" \u9a6c\u4e0a\u5904\u7406")
    emitted += guard.flush()

    assert "dispatchOnlineTask" not in emitted
    assert "\u597d\u7684\uff0c\u6211\u5148\u770b\u4e00\u4e0b" in emitted
    assert "\u9a6c\u4e0a\u5904\u7406" in emitted


def test_stream_guard_releases_unrelated_braces_and_angles():
    module = _module()
    guard = module.guard_for("deepseek-flash", ("listSystemCapabilities",))

    assert guard.feed("JSON \u793a\u4f8b {\u597d\u7684} ") == "JSON \u793a\u4f8b {\u597d\u7684} "
    # 标签块会先扣住观察一下，确认不是调用后原样放出来（不丢内容、只是稍晚）
    html = LT + "b" + GT + "\u52a0\u7c97" + LT + "/b" + GT
    assert guard.feed(html) + guard.flush() == html


def test_env_can_turn_stream_guard_off_for_a_model(monkeypatch):
    monkeypatch.setenv(
        "SUTUI_CHAT_MODEL_PROFILES_JSON",
        '[{"id":"quiet","match":["quiet-*"],"guard_stream_fake_tools":false}]',
    )
    module = _module()

    quiet = module.profile_for("quiet-1")
    assert quiet.id == "quiet"
    assert module.guard_for("quiet-1").enabled is False
    assert module.profile_for("deepseek-chat").id == "deepseek"


def test_env_can_pin_a_model_to_a_profile(monkeypatch):
    monkeypatch.setenv("SUTUI_CHAT_MODEL_PROFILE_MAP_JSON", '{"vendor-x":"deepseek"}')
    module = _module()

    assert module.profile_for("vendor-x").id == "deepseek"
    assert module.profile_for("vendor-y").id == "default"


def test_env_can_add_extra_tool_names(monkeypatch):
    monkeypatch.setenv(
        "SUTUI_CHAT_MODEL_PROFILES_JSON",
        '[{"id":"vendor","match":["vendor-*"],"extra_tool_names":["vendor_magic_tool"]}]',
    )
    module = _module()
    profile = module.profile_for("vendor-1")
    text = _tag("vendor_magic_tool", _tag("query", "x"))

    cleaned, changed = module.strip_fake_tool_text(text, profile)
    assert changed is True
    assert "vendor_magic_tool" not in cleaned


def test_proxy_wiring_uses_declared_tool_names():
    from backend.app.api import sutui_chat_proxy as proxy

    body = {"tools": [{"type": "function", "function": {"name": "listSystemCapabilities"}}]}
    assert proxy._request_tool_names(body) == ("listSystemCapabilities",)
    assert proxy._request_tool_names({}) == ()

    payload = {"choices": [{"message": {"role": "assistant", "content": _tag("b", "\u6b63\u5e38\u6587\u672c")}}]}
    assert proxy._strip_fake_tool_text_from_response(payload, proxy._model_profile("deepseek-flash"), ("listSystemCapabilities",)) is False

    fake = {"choices": [{"message": {"role": "assistant", "content": _tag("listSystemCapabilities", _tag("query", "x"))}}]}
    assert proxy._strip_fake_tool_text_from_response(fake, proxy._model_profile("gpt-5.6-sol"), ("listSystemCapabilities",)) is True
    assert "listSystemCapabilities" not in fake["choices"][0]["message"]["content"]


def test_proxy_stream_event_guard_uses_tool_names():
    from backend.app.api import sutui_chat_proxy as proxy

    guard = proxy._model_profiles.guard_for("deepseek-chat", ("listSystemCapabilities",))
    first = proxy._guard_sse_event(
        b'data: {"choices":[{"delta":{"content":"' + LT.encode() + b'listSystem"}}]}',
        guard,
    )
    second = proxy._guard_sse_event(
        b'data: {"choices":[{"delta":{"content":"Capabilities' + GT.encode() + b'"}}]}',
        guard,
    )

    assert b"listSystem" not in first
    assert b"listSystem" not in second


# 线上真实 case（2026-09-10）：DeepSeek 把 DSML 信封写成全角竖线 ｜（U+FF5C），
# 旧逻辑只认 "<||DSML"，于是闭合标签一路漏进正文，用户看到的就是 "</invoke>"。
PIPE = "\uff5c"  # ｜


def _prod_dsml_text() -> str:
    return (
        f"{LT}{PIPE}{PIPE}DSML{PIPE}{PIPE} calls{GT}" + "\n"
        f"{LT}/{PIPE}{PIPE}DSML{PIPE}{PIPE} invoke{GT}" + "\n"
        f"\u9996\u5e27 \u6765\u6e90\u56fe{LT}/{PIPE}{PIPE}DSML{PIPE}{PIPE} parameter{GT}" + "\n"
        f"{LT}/{PIPE}{PIPE}DSML{PIPE}{PIPE} invoke{GT}" + "\n"
        f"{LT}/{PIPE}{PIPE}DSML{PIPE}{PIPE} calls{GT}"
    )


def test_fullwidth_dsml_envelope_is_fully_stripped():
    module = _module()
    profile = module.profile_for("deepseek-flash")

    cleaned, changed = module.strip_fake_tool_text(_prod_dsml_text(), profile, ())
    assert changed is True
    assert cleaned == ""
    assert "DSML" not in cleaned
    assert PIPE not in cleaned


def test_fullwidth_dsml_envelope_keeps_answer_around_it():
    module = _module()
    profile = module.profile_for("deepseek-flash")
    text = "\u597d\u7684\uff0c\u6211\u6765\u5904\u7406\n" + _prod_dsml_text() + "\n\u5df2\u5b8c\u6210"

    cleaned, changed = module.strip_fake_tool_text(text, profile, ())
    assert changed is True
    assert "DSML" not in cleaned
    assert cleaned.startswith("\u597d\u7684\uff0c\u6211\u6765\u5904\u7406")
    assert cleaned.endswith("\u5df2\u5b8c\u6210")


def test_literal_escape_pipe_form_is_stripped():
    module = _module()
    profile = module.profile_for("deepseek-flash")
    text = "\u63a5\u5165\n" + r"</\uff5c\uff5cDSML\uff5c\uff5c invoke>" + "\n\u5b8c\u4e8b"

    cleaned, changed = module.strip_fake_tool_text(text, profile, ())
    assert changed is True
    assert "DSML" not in cleaned
    assert "uff5c" not in cleaned
    assert "\u63a5\u5165" in cleaned and "\u5b8c\u4e8b" in cleaned


def test_stream_guard_swallows_split_fullwidth_dsml_envelope():
    module = _module()
    guard = module.guard_for("deepseek-flash", ("listSystemCapabilities",))

    # 线上就是这么一片一片吐出来的
    emitted = guard.feed(LT)
    emitted += guard.feed(f"{PIPE}{PIPE}DSML{PIPE}{PIPE} calls{GT}")
    emitted += guard.feed(f"{GT}\n{LT}/{PIPE}{PIPE}DSML{PIPE}{PIPE} invoke{GT}")
    emitted += guard.feed(
        f"\u9996\u5e27 \u6765\u6e90\u56fe{LT}/{PIPE}{PIPE}DSML{PIPE}{PIPE} parameter{GT}"
        f"\n{LT}/{PIPE}{PIPE}DSML{PIPE}{PIPE} invoke{GT}"
    )
    emitted += guard.feed(f"\n{LT}/{PIPE}{PIPE}DSML{PIPE}{PIPE} calls")
    emitted += guard.feed(GT)
    emitted += guard.flush()

    assert emitted == ""


def test_stream_guard_keeps_real_text_around_fullwidth_envelope():
    module = _module()
    guard = module.guard_for("deepseek-flash", ())
    emitted = guard.feed("\u6b63\u5728\u5904\u7406 ") + guard.feed(_prod_dsml_text()) + guard.feed(" \u5df2\u5b8c\u6210")
    emitted += guard.flush()

    assert "DSML" not in emitted
    assert PIPE not in emitted
    assert emitted.startswith("\u6b63\u5728\u5904\u7406")
    assert emitted.endswith("\u5df2\u5b8c\u6210")


def test_fullwidth_pipe_in_plain_answer_survives():
    module = _module()
    profile = module.profile_for("deepseek-flash")
    sample = f"\u4ef7\u683c{PIPE}\u9ad8\u6e05\uff0c\u5efa\u8bae\u9009\u5b83"

    cleaned, changed = module.strip_fake_tool_text(sample, profile, ())
    assert changed is False
    assert cleaned == sample
