from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
H5 = ROOT / "h5_static"


def test_library_uses_twenty_item_pages_and_lazy_document_details():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert "assetLibraryPageSize: 20" in script
    assert "contentRecordPageSize: 20" in script
    assert 'compact: "true"' in script
    assert "/api/content-records/detail?" in script


def test_placeholder_employees_remain_visible_while_placeholder_nodes_are_filtered():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert ".filter((node) => !workflowNodeIsPlaceholder(node))" in script
    assert "sortWorkflowTemplatesForDisplay(workflowTemplateRows())" in script
    assert '{ id: "customer_service", name: "客服", status: "敬请期待"' in script
    assert '{ id: "overseas", name: "海外员工", status: "敬请期待"' in script
    assert '{ id: "hr", name: "HR", status: "敬请期待"' in script


def test_profile_displays_real_credit_balance():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert 'id="profileCreditBalance"' in html
    assert '$("profileCreditBalance").textContent = compactNumber(user.credits, 2)' in script
    assert "20260803-library-v3" in html


def test_workflow_missing_dialog_is_a_bounded_single_action_sheet():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    styles = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")

    assert "启动前还差一步" in html
    assert 'id="workflowMissingPrimary"' not in html
    assert "workflowMissingPrimary" not in script
    assert "#workflowMissingModal .workflow-missing-popover" in styles
    assert "box-sizing: border-box" in styles
    assert "overflow-x: hidden" in styles
