from __future__ import annotations


def _catalogue() -> str:
    lines = [f"- comfly.cap{i}：示例能力描述，用于把目录撑长" for i in range(60)]
    return "【可用能力目录】\n" + "\n".join(lines)


def _current_turn_message() -> str:
    """复刻 mastra 侧拼出来的本轮消息：正文 + 能力目录 + 素材清单 + 图片理解结果。"""
    return (
        "【系统角色设定】\n身份：必火AI员工执行系统\n\n"
        "【本轮用户请求】\n理解一下这个图片\n\n"
        "【工作模式·预取简要目录（系统注入）】\n"
        + _catalogue()
        + "\n\n本轮附加素材：\n"
        "1. 微信图片_20260928125852.png [image] "
        "https://lobster-online-assets.example.com/assets/d04a21a52603.png\n\n"
        "请把这些素材视为用户本轮的真实输入。\n\n"
        "[图片理解结果]\n一罐蓝色易拉罐凉茶，罐身「贵州小叶苦丁凉茶」，净含量 310mL。\n"
        "[图片理解结果结束]"
    )


def test_current_turn_keeps_attachment_manifest_and_image_understanding():
    from backend.app.api.sutui_chat_proxy import _SLIM_MSG_MAX_CHARS, _SLIM_MSG_TAIL_CHARS, _slim_messages

    message = _current_turn_message()
    assert len(message) > _SLIM_MSG_MAX_CHARS

    out = _slim_messages(
        [
            {"role": "system", "content": "S" * 50},
            {"role": "user", "content": "之前的问题"},
            {"role": "assistant", "content": "之前的回答"},
            {"role": "user", "content": message},
        ]
    )
    last = out[-1]["content"]
    assert "本轮附加素材：" in last
    assert "d04a21a52603.png" in last
    assert "[图片理解结果]" in last
    assert "贵州小叶苦丁凉茶" in last
    assert "理解一下这个图片" in last
    assert len(last) <= _SLIM_MSG_MAX_CHARS + _SLIM_MSG_TAIL_CHARS + 40


def test_history_message_is_still_truncated_head_only():
    from backend.app.api.sutui_chat_proxy import _SLIM_MSG_MAX_CHARS, _truncate_msg

    out = _truncate_msg({"role": "assistant", "content": "历史消息。" * 400})
    assert out["content"].endswith("…(truncated)")
    assert len(out["content"]) == _SLIM_MSG_MAX_CHARS + len("…(truncated)")


def test_long_current_turn_without_markers_keeps_its_tail():
    from backend.app.api.sutui_chat_proxy import _truncate_msg

    text = "开头" + "填充" * 500 + "结尾的补充要求"
    out = _truncate_msg({"role": "user", "content": text}, keep_tail=3000)
    assert "结尾的补充要求" in out["content"]
    assert "…(中间内容已省略)…" in out["content"]


def test_base64_images_are_still_stripped():
    from backend.app.api.sutui_chat_proxy import _truncate_msg

    payload = "data:image/png;base64," + "A" * 400
    out = _truncate_msg({"role": "user", "content": "看一下这张图 " + payload})
    assert "data:image" not in out["content"]
    assert "[image]" in out["content"]


def test_slim_messages_keeps_turn_roles_and_history_limit():
    from backend.app.api.sutui_chat_proxy import _SLIM_MSG_MAX_TURNS, _slim_messages

    msgs = [{"role": "system", "content": "S"}]
    for i in range(20):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})
    msgs.append({"role": "user", "content": _current_turn_message()})

    out = _slim_messages(msgs)
    assert out[0]["role"] == "system"
    assert out[-1]["role"] == "user"
    assert len(out) == 1 + _SLIM_MSG_MAX_TURNS
    assert "d04a21a52603.png" in out[-1]["content"]

