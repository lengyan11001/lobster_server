"""数字人口播下发前「没套模板」的提醒（2026-10-16）。

背景：同一个手动创建路径上，选没选剪辑模板的差别只是 use_template 的 true/false，
默认「不套模板」以前会静默出一版数字人原片，事后完全看不出来。
这里锁死四件事：提醒存在并挂在两个下发入口 / 默认值没被改 / 取消会切到套用模板 /
已经选了模板就不再打扰。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
H5_APP = ROOT / "h5_static" / "h5-app.js"


def _src() -> str:
    return H5_APP.read_text(encoding="utf-8")


def test_confirm_guard_exists_and_is_wired_into_both_submit_paths():
    src = _src()
    assert "function confirmDigitalHumanTemplateBeforeSubmit(scope)" in src
    # AI营销「安排工作」弹窗（数字人口播视频）
    assert 'if (String(item.key || "") === "hifly.video.create_by_tts"' in src
    assert '&& !confirmDigitalHumanTemplateBeforeSubmit("work")) {' in src
    # 任务面板定时下发（同一个技能）
    assert 'if (state.taskAbility === "hifly.video.create_by_tts"' in src
    assert '&& !confirmDigitalHumanTemplateBeforeSubmit("task")) {' in src


def test_default_option_is_still_no_template():
    """不改默认：手动面板「剪辑方式」第一个选项仍是「不套模板」。"""
    src = _src()
    assert (
        'taskSelectHtml("taskHiflyTemplateMode", optionHtml("none", "不套模板") + optionHtml("template", "套用模板"))'
        in src
    )


def test_confirm_text_explains_confirm_and_cancel():
    src = _src()
    assert "这次「剪辑方式」是【不套模板】" in src
    assert "确认：照这样下发。" in src
    assert "取消：帮你切到「套用剪辑模板」，先选好模板再下发。" in src


def test_cancel_switches_to_template_mode_and_points_at_the_template_field():
    src = _src()
    assert 'setHiflyTemplateMode(modeId, "template");' in src
    assert "field.classList.remove(\"hidden\");" in src
    assert "scrollIntoView({ block: \"center\" })" in src
    assert "还没有可用剪辑模板，请先到「个人默认配置」添加并设为当前" in src


def test_guard_is_skipped_when_a_template_is_already_chosen():
    src = _src()
    assert 'if (hiflyTemplateModeValue(modeId) === "template" && workflowParamValue(templateId)) return true;' in src
