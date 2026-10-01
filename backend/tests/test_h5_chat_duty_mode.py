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
    assert "h5-app.js?v=20261001-chat-duty-mode-v2" in html
    assert "h5ChatDutyMode() === \"service\"" in js
    assert "h5-app.css?v=20261001-chat-duty-mode-v1" in html

    # 逻辑：默认工作；客服模式包一层指令，非客服问题不执行
    assert "function h5ChatDutyMode(" in js
    assert "function applyH5DutyModeToContent(" in js
    assert "function initH5ChatDutyMode(" in js
    assert "【客服模式】" in js
    assert "只处理客服问题" in js
    assert "要安排工作请把输入框左下角的下拉切回「工作」" in js

    # 请求：content 用包装后的文本，并带上 duty_mode
    assert "buildMessageContent(applyH5DutyModeToContent(content))" in js
    assert "duty_mode: h5ChatDutyMode()," in js

    # 初始化 + 记住选择
    assert "initH5ChatDutyMode();" in js
    assert "lobster_h5_chat_duty_mode" in js
