"""DeepSeek 官方直连的报文整形回归（2026-09-20）。

背景：编排会话（带工具）每一步先打 DeepSeek 官方直连，我们这侧发的报文形状一旦不合法，
整跳 422/400，候选链就掉到更慢的通道（实测单步 2~3 分钟）。下面这些形状都是
api.deepseek.com 实测会拒的，全部要在发出去之前修好。
"""
from __future__ import annotations

from backend.app.api import sutui_chat_proxy as proxy

URL = "https://api.deepseek.com/v1/chat/completions"


def _sanitize(body: dict, *, stream: bool = False) -> dict:
    proxy._normalize_deepseek_messages(URL, body, stream=stream)
    return body


def test_other_providers_untouched():
    body = {"messages": [{"role": "developer", "content": "x"}]}
    original = {"messages": [{"role": "developer", "content": "x"}]}
    proxy._normalize_deepseek_messages("https://www.yyapi.cloud/v1/chat/completions", body)
    assert body == original


def test_developer_role_becomes_system():
    body = _sanitize({"messages": [{"role": "developer", "content": "你是助手"}, {"role": "user", "content": "hi"}]})
    assert body["messages"][0]["role"] == "system"


def test_stream_options_dropped_for_non_stream():
    body = _sanitize({"messages": [{"role": "user", "content": "hi"}], "stream_options": {"include_usage": True}})
    assert "stream_options" not in body
    # 流式请求保留
    streamed = _sanitize(
        {"messages": [{"role": "user", "content": "hi"}], "stream_options": {"include_usage": True}},
        stream=True,
    )
    assert streamed["stream_options"] == {"include_usage": True}


def test_tool_content_missing_or_object_is_fixed():
    body = _sanitize(
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "t", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1"},
            ]
        }
    )
    tool_message = body["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["content"] == ""

    body2 = _sanitize(
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "t", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": {"count": 1}},
            ]
        }
    )
    assert body2["messages"][-1]["content"] == '{"count": 1}'


def test_orphan_tool_message_is_dropped():
    body = _sanitize(
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "tool", "tool_call_id": "gone", "content": "旧结果"},
                {"role": "user", "content": "继续"},
            ]
        }
    )
    roles = [message["role"] for message in body["messages"]]
    assert roles == ["user", "user"]


def test_assistant_tool_calls_without_results_get_placeholder():
    body = _sanitize(
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_9", "type": "function", "function": {"name": "t", "arguments": "{}"}}
                    ],
                },
                {"role": "user", "content": "继续"},
            ]
        }
    )
    roles = [message["role"] for message in body["messages"]]
    assert roles == ["user", "assistant", "tool", "user"]
    placeholder = body["messages"][2]
    assert placeholder["tool_call_id"] == "call_9"
    assert placeholder["content"]


def test_tool_pairing_order_is_preserved():
    body = _sanitize(
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "a", "type": "function", "function": {"name": "t1", "arguments": "{}"}},
                        {"id": "b", "type": "function", "function": {"name": "t2", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "b", "content": "B"},
                {"role": "tool", "tool_call_id": "a", "content": "A"},
                {"role": "assistant", "content": "done"},
            ]
        }
    )
    roles = [message["role"] for message in body["messages"]]
    assert roles == ["user", "assistant", "tool", "tool", "assistant"]
