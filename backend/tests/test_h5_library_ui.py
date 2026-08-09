from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
H5 = ROOT / "h5_static"


def test_library_uses_twenty_item_pages_and_lazy_document_details():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert "assetLibraryPageSize: 20" in script
    assert "contentRecordPageSize: 20" in script
    assert 'compact: "true"' in script
    assert "/api/content-records/detail?" in script


def test_library_reuses_recent_pages_and_falls_back_when_direct_media_fails():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert "assetLibraryPageCache" in script
    assert "contentRecordPageCache" in script
    assert "data-library-media-fallback" in script


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
    assert "20260803-library-v4" in html


def test_ios_bottom_navigation_does_not_scale_on_tap():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    styles = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")

    start = styles.index(".designer-bottom-nav button {")
    end = styles.index(".designer-bottom-nav button.active", start)
    bottom_nav = styles[start:end]

    assert "touch-action: manipulation;" in bottom_nav
    assert "-webkit-tap-highlight-color: transparent;" in bottom_nav
    assert ".designer-bottom-nav button:not(:disabled):active" in bottom_nav
    assert "transform: none;" in bottom_nav
    assert "20260808-ios-bottom-nav-v1" in html


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


def test_active_workflow_timeline_filters_saved_placeholder_nodes():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    start = script.index("function renderWorkflowTimeline()")
    end = script.index("function renderWorkflowTemplates()", start)
    timeline = script[start:end]

    assert ".filter((node) => !workflowNodeIsPlaceholder(node))" in timeline
    assert ".filter((action) => !workflowNodeIsPlaceholder(action))" in timeline
    assert 'markerText.includes("敬请期待")' in script


def test_h5_write_actions_use_one_blocking_loading_dialog():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    styles = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")

    assert html.count('id="globalActionLoading"') == 1
    assert 'id="globalActionLoadingTitle"' in html
    assert 'id="globalActionLoadingDetail"' in html
    assert "function beginBlockingAction" in script
    assert "async function blockingFetch" in script
    assert 'const shouldBlock = method !== "GET" && requestOptions.blocking !== false;' in script
    assert 'blocking: nextStatus === "active" ? "正在启用任务" : "正在停用任务"' in script
    assert 'blocking: "正在下发任务"' in script
    assert ".global-action-loading" in styles
    assert "width: min(320px, calc(100vw - 32px));" in styles
    assert "z-index: 1000;" in styles
    assert "20260808-action-loading-v1" in html


def test_h5_background_reads_do_not_trigger_blocking_loading():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert 'api("/api/shanjian-digital-human/profile/task", {' in script
    assert 'json: { profile_id: Number(row.source_record_id) },\n            blocking: false,' in script
    assert 'json: { page_size: 60, sid, scene: "realMan", sort_by: "desc" },\n            blocking: false,' in script
    assert 'api("/api/hifly/avatar/library", { method: "POST", json: { page: 1, size: 100, include_mine: true }, blocking: false })' in script
    assert 'api("/api/hifly/voice/library", { method: "POST", json: {}, blocking: false })' in script
    assert 'const data = await api("/api/mastra-chat/messages", {\n          method: "POST",\n          blocking: false,' in script
