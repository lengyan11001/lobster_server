"""H5「AI 调度助手」：工作 / 客服 下拉（客服模式只把客服问题交给 AI）。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
H5 = ROOT / "h5_static"


def test_h5_chat_duty_mode_select_and_wiring():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    js = (H5 / "h5-app.js").read_text(encoding="utf-8")
    css = (H5 / "h5-app.css").read_text(encoding="utf-8")

    # 下拉框：工作 / 客服
    assert 'id="h5ChatDutyMode"' in html
    assert '<option value="work" selected>工作</option>' in html
    assert '<option value="service">客服</option>' in html
    assert ".composer-duty-wrap select" in css
    assert "h5-app.js?v=20261001-clean-service-reply-v5" in html
    assert "h5ChatDutyMode() === \"service\"" in js
    assert "h5-app.css?v=20261001-chat-duty-mode-v1" in html

    # 逻辑：默认工作；客服模式包一层指令，非客服问题不执行
    assert "function h5ChatDutyMode(" in js
    assert "function applyH5DutyModeToContent(" in js
    assert "function initH5ChatDutyMode(" in js
    # 客服模式不再在前端做判断/隔离：只把 duty_mode 交给服务端（服务端注入客服百问百答）
    assert "【客服模式】" not in js
    assert "只处理客服问题" not in js
    assert "return String(content || \"\");" in js

    # 请求：content 用包装后的文本，并带上 duty_mode
    assert "buildMessageContent(applyH5DutyModeToContent(content))" in js
    # 工作模式请求体保持原样：只有客服模式才带 duty_mode
    assert '...(h5ChatDutyMode() === "service" ? { duty_mode: "service" } : {})' in js
    assert "h5DutyPlaceholderBackup" in js

    # 初始化 + 记住选择
    assert "initH5ChatDutyMode();" in js
    assert "lobster_h5_chat_duty_mode" in js

def test_h5_service_reply_is_cleaned_before_display():
    js = (H5 / "h5-app.js").read_text(encoding="utf-8")
    assert "function cleanH5ServiceReply(" in js
    assert 'if (h5ChatDutyMode() === "service") reply = cleanH5ServiceReply(reply);' in js
    assert 'if (h5ChatDutyMode() === "service" && msg.reply_text) msg.reply_text = cleanH5ServiceReply(msg.reply_text);' in js
