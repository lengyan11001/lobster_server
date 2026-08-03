from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
H5 = ROOT / "h5_static"


def test_library_uses_twenty_item_pages_and_lazy_document_details():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert "assetLibraryPageSize: 20" in script
    assert "contentRecordPageSize: 20" in script
    assert 'compact: "true"' in script
    assert "/api/content-records/detail?" in script


def test_unavailable_employee_placeholders_are_filtered_from_user_views():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert "workflowTemplateRows().filter((tpl) => !workflowTemplateIsComingSoon(tpl))" in script
    assert ".filter((node) => !workflowNodeIsPlaceholder(node))" in script
    assert '{ id: "customer_service", name: "客服", status: "敬请期待"' not in script
    assert '{ id: "overseas", name: "海外员工", status: "敬请期待"' not in script
    assert '{ id: "hr", name: "HR", status: "敬请期待"' not in script


def test_profile_displays_real_credit_balance():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert 'id="profileCreditBalance"' in html
    assert '$("profileCreditBalance").textContent = compactNumber(user.credits, 2)' in script
    assert "20260803-library-v1" in html
