from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_ip_daily_form_uses_compact_mobile_controls():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    styles = (ROOT / "h5_static" / "h5-designer-v2.css").read_text(encoding="utf-8")
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")

    assert script.count('class="ip-daily-task-options"') == 3
    assert script.count("ip-daily-sync-option") == 3
    assert 'taskFieldHtml("模板", ipTemplateSelectControl("abilityIpTemplate"), true)' in script
    assert 'taskFieldHtml("模板", ipTemplateSelectControl("workflowParamIpTemplate"), true)' in script
    assert '.ip-daily-task-options {' in styles
    assert 'grid-template-columns: repeat(2, minmax(0, 1fr));' in styles
    assert 'width: 18px !important;' in styles
    assert "20260730-ip-daily-form-v1" in html
