from pathlib import Path

from backend.app.api.h5_workflows import (
    WorkflowTemplateIn,
    _template_payload,
    create_workflow_template,
    update_workflow_template,
)
from backend.app.models import H5WorkflowTemplate


ROOT = Path(__file__).resolve().parents[2]


def _sales_body(label: str) -> WorkflowTemplateIn:
    return WorkflowTemplateIn(
        name="销售24小时员工",
        nodes=[
            {
                "id": "sales_1",
                "time": "06:00",
                "department_id": "sales",
                "ability_label": label,
                "plan": {"task_kind": "client_workflow", "payload": {"action": "local_bestseller_daily_video"}},
            }
        ],
        meta={"system_template_key": "system_sales", "source": "system_mirror"},
    )


def test_system_sales_save_creates_one_personal_mirror_and_then_updates_it(db_session, test_user):
    first = create_workflow_template(_sales_body("第一次保存"), current_user=test_user, db=db_session)
    second = create_workflow_template(_sales_body("第二次保存"), current_user=test_user, db=db_session)

    rows = db_session.query(H5WorkflowTemplate).filter(
        H5WorkflowTemplate.owner_user_id == test_user.id,
        H5WorkflowTemplate.status == "active",
    ).all()
    assert first["created"] is True
    assert second["created"] is False
    assert first["template"]["id"] == second["template"]["id"]
    assert len(rows) == 1
    assert rows[0].nodes[0]["ability_label"] == "第二次保存"
    assert second["template"]["meta"]["system_template_key"] == "system_sales"


def test_edit_without_meta_preserves_system_mirror_identity(db_session, test_user):
    created = create_workflow_template(_sales_body("初始内容"), current_user=test_user, db=db_session)

    updated = update_workflow_template(
        created["template"]["id"],
        WorkflowTemplateIn(name="我的销售", nodes=_sales_body("原地编辑").nodes),
        current_user=test_user,
        db=db_session,
    )

    assert updated["template"]["id"] == created["template"]["id"]
    assert updated["template"]["meta"]["system_template_key"] == "system_sales"
    assert db_session.query(H5WorkflowTemplate).count() == 1


def test_explicit_copy_metadata_still_creates_new_templates(db_session, test_user):
    body = WorkflowTemplateIn(
        name="销售副本",
        nodes=_sales_body("复制内容").nodes,
        meta={"copied_from": "system_sales", "copied_source": "system"},
    )

    first = create_workflow_template(body, current_user=test_user, db=db_session)
    second = create_workflow_template(body, current_user=test_user, db=db_session)

    assert first["template"]["id"] != second["template"]["id"]
    assert db_session.query(H5WorkflowTemplate).count() == 2


def test_plain_save_creates_blank_editor_template(db_session, test_user):
    body = WorkflowTemplateIn(name="普通保存", nodes=_sales_body("普通保存").nodes)

    created = create_workflow_template(body, current_user=test_user, db=db_session)

    assert created["created"] is True
    assert created["template"]["name"] == "普通保存"
    assert db_session.query(H5WorkflowTemplate).count() == 1


def test_custom_template_cannot_take_an_existing_system_sales_identity(db_session, test_user):
    sales = create_workflow_template(_sales_body("完整销售"), current_user=test_user, db=db_session)
    custom = create_workflow_template(
        WorkflowTemplateIn(name="新员工", nodes=_sales_body("单节点").nodes),
        current_user=test_user,
        db=db_session,
    )

    updated = update_workflow_template(
        custom["template"]["id"],
        WorkflowTemplateIn(
            name="新员工已更新",
            nodes=_sales_body("更新后的单节点").nodes,
            meta={"system_template_key": "system_sales", "source": "system_mirror"},
        ),
        current_user=test_user,
        db=db_session,
    )

    db_session.expire_all()
    sales_row = db_session.get(H5WorkflowTemplate, sales["template"]["id"])
    custom_row = db_session.get(H5WorkflowTemplate, custom["template"]["id"])
    assert sales_row.meta["system_template_key"] == "system_sales"
    assert sales_row.nodes[0]["ability_label"] == "完整销售"
    assert updated["template"]["name"] == "新员工已更新"
    assert custom_row.nodes[0]["ability_label"] == "更新后的单节点"
    assert not (custom_row.meta or {}).get("system_template_key")


def test_saved_workflow_drops_placeholder_nodes(db_session, test_user):
    normal_node = _sales_body("正常任务").nodes[0]
    placeholder_node = {
        "id": "sales_placeholder",
        "time": "15:00",
        "ability_label": "视频号评论区接管（敬请期待）",
        "comingSoon": True,
        "plan": {
            "task_kind": "workflow_placeholder",
            "payload": {"action": "workflow_coming_soon", "skip_execution": True},
        },
    }

    created = create_workflow_template(
        WorkflowTemplateIn(name="销售", nodes=[normal_node, placeholder_node]),
        current_user=test_user,
        db=db_session,
    )

    assert [node["ability_label"] for node in created["template"]["nodes"]] == ["正常任务"]
    row = db_session.query(H5WorkflowTemplate).one()
    assert [node["ability_label"] for node in row.nodes] == ["正常任务"]


def test_existing_workflow_payload_hides_legacy_placeholder_nodes():
    normal_node = _sales_body("正常任务").nodes[0]
    legacy_placeholder = {
        "id": "legacy_placeholder",
        "time": "15:15",
        "ability_label": "视频号私信接管（敬请期待）",
        "plan": {"task_kind": "client_workflow", "payload": {"action": "legacy_action"}},
    }
    row = H5WorkflowTemplate(owner_user_id=1, name="旧销售", nodes=[normal_node, legacy_placeholder])

    payload = _template_payload(row)

    assert [node["ability_label"] for node in payload["nodes"]] == ["正常任务"]


def test_h5_editor_opens_blank_draft_and_keeps_template_copy_support():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")

    assert 'meta: { copied_from: String(tpl.id || ""), copied_source: tpl.source || "" }' in script
    assert 'system_template_key: "system_sales"' in script
    assert 'if (key === "workflowNew")' in script
    assert "resetWorkflowDraft();" in script
    assert 'switchTab("workflow");' in script
    assert "workflowPlanDayModal" in html
    assert "workflowTemplateIsSales(tpl)" in script
    assert "plan_day: Number(planDay)" in script
    assert "function customWorkflowTemplateRows()" in script
    assert "const mirrors = new Map();" in script
    assert "return !workflowSystemTemplateKey(tpl) && !mergedIds.has(id);" in script
    assert "return personalSystemWorkflowTemplate(sid)" in script
    assert "if (state.workflowTemplateSaving) return;" in script
    assert 'meta.system_template_key || state.workflowViewingTemplateKey' not in script
    assert 'key === "system_sales" ? "/h5-static/designer-employee-sales.jpg" : ""' in script
    assert "20260730-workflow-menu-v2" in html


def test_custom_workflow_with_local_bestseller_requests_plan_day():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    activation = script.split("async function activateWorkflowTemplate", 1)[1].split("async function stopWorkflowActive", 1)[0]

    assert "function workflowTemplateRequiresPlanDay(tpl)" in script
    assert "workflowTemplateRequiresPlanDay(tpl)" in activation
    assert "workflowTemplateIsSales(tpl)" not in activation


def test_workflow_template_drawer_keeps_four_system_slots_and_restores_personal_sales():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    system_templates = script.split("function systemWorkflowTemplates()", 1)[1].split("function closeWorkflowOverlays", 1)[0]
    drawer = script.split("function renderWorkflowTemplates()", 1)[1].split("function userWorkflowTemplateRows", 1)[0]
    restore = script.split("function restoreSystemWorkflowTemplate", 1)[1].split("function resetWorkflowDraft", 1)[0]

    for template_id in ("system_sales", "system_customer_service", "system_overseas", "system_hr"):
        assert f'id: "{template_id}"' in script
    assert ".filter((tpl) => workflowTemplateNodeCount(tpl) > 0)" not in system_templates
    assert "const rows = systemWorkflowTemplates();" in drawer
    assert "workflowTemplateRows()" not in drawer
    assert 'data-workflow-restore-system="${escapeHtml(tpl.id)}"' in drawer
    assert 'state.workflowEditingTemplateId = personalMirror ? String(personalMirror.id || "") : "";' in restore
    assert 'system_template_key: id' in restore
    assert 'source: "system_mirror"' in restore
    assert "state.workflowNodesDraft = cloneWorkflowNodes(tpl.nodes);" in restore
    assert "系统推荐模板" in html


def test_home_employee_cards_open_the_editor_without_the_detail_dialog():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    strip_handler = script.split('$("customEmployeeStrip")?.addEventListener("click"', 1)[1].split('$("customEmployeeBackdrop")', 1)[0]
    floor_handler = script.split('$("employeeFloor").addEventListener("click"', 1)[1].split('document.querySelectorAll("[data-device-filter]")', 1)[0]
    editor = script.split("function openWorkflowTemplateEditor", 1)[1].split("async function loadWorkflowTemplates", 1)[0]

    assert "openWorkflowTemplateEditor" in strip_handler
    assert "openCustomEmployeeDetail" not in strip_handler
    assert "openWorkflowTemplateEditor" in floor_handler
    assert "openCustomEmployeeDetail" not in floor_handler
    assert "prepareSalesWorkflowDraft();" in editor
    assert "applyWorkflowTemplate(tpl);" in editor
    assert 'switchTab("workflow");' in editor


def test_active_workflow_status_is_scoped_to_its_current_device_tasks():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    start = script.index("function workflowRecordMatchesDisplayed(row)")
    end = script.index("function workflowRecordMatchesCurrent(row)", start)
    matcher = script[start:end]

    assert "workflowDisplayedContextIsActive()" in matcher
    assert "const activeIds = workflowActiveTaskIds();" in matcher
    assert "return !!rowTaskId && activeIds.has(rowTaskId);" in matcher


def test_workflow_action_menu_stays_above_children_and_closes_before_modal():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    styles = (ROOT / "h5_static" / "h5-app.css").read_text(encoding="utf-8")
    designer_styles = (ROOT / "h5_static" / "h5-designer-v2.css").read_text(encoding="utf-8")
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")

    modal_start = script.index('function openWorkflowActionModal(parentNodeId, actionId = "")')
    modal_body = script[modal_start : modal_start + 240]
    assert "closeTaskActionMenus();" in modal_body
    assert ".workflow-node-card.task-menu-open" in styles
    assert "setTaskActionMenuLayer(menu, true);" in script
    assert "evt.preventDefault();" in script
    assert ".workflow-timeline-entry.task-menu-open > .workflow-node-card" in styles
    assert ".workflow-timeline-entry.task-menu-open > .workflow-child-list" in styles
    assert ".designer-workflow-group.task-menu-open" in styles
    assert ".workflow-node-card.task-menu-open .task-action-list" in styles
    assert 'const childList = menu.closest(".workflow-child-list");' in script
    assert 'childList?.classList.add("task-menu-open");' in script
    assert "if (exceptMenu && item.contains(exceptMenu)) return;" in script
    assert ".workflow-timeline-entry:has(.task-action-menu[open])" in designer_styles
    assert ".workflow-child-list .workflow-node-card:has(.task-action-menu[open])" in designer_styles
    assert ".workflow-node-card:has(.task-action-menu[open]) .task-action-list" in designer_styles
    assert "20260803-workflow-child-menu-v3" in html


def test_native_wechat_takeover_uses_parent_group_switch_and_keeps_moments_children():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    styles = (ROOT / "h5_static" / "h5-designer-v2.css").read_text(encoding="utf-8")

    detector_start = script.index("function workflowNodeIsNativeWechatTakeover(parentNode)")
    detector_end = script.index("function workflowActionTypeOptions", detector_start)
    detector = script[detector_start:detector_end]
    options_start = detector_end
    options_end = script.index("function renderWorkflowActionTypeOptions", options_start)
    options = script[options_start:options_end]

    assert 'action === "native_wechat_poll"' in detector
    assert 'text.includes("微信私信接管")' in detector
    assert 'params.followup_action' not in detector
    assert 'values.push("native_wechat_moments_engage")' in options
    assert 'values.push("native_wechat_group_invite", "native_wechat_moments_engage")' not in options
    assert 'label: "微信自动拉群"' not in script
    assert 'if (key === "native_wechat_poll") return false;' in script
    assert 'taskFieldHtml("是否拉群"' in script
    assert 'workflowParamNativeWechatGroupInviteEnabled' in script
    assert 'group_invite_enabled: workflowParamChecked("workflowParamNativeWechatGroupInviteEnabled")' in script
    assert 'baseParams.group_invite_enabled = true' in script
    assert 'baseParams.trigger = baseParams.trigger || "qualified_intent"' in script
    assert 'native_wechat_group_invite' not in script
    assert '"native_wechat_moments_engage",' in script
    assert "#workflowActionPlatformField[hidden]" in styles
    assert "20260808-workflow-action-fields-v1" in html
    assert "20260808-workflow-native-children-v1" in html
    assert "20260814-wechat-group-switch-v1" in html


def test_workflow_title_and_controls_use_an_operation_menu_in_normal_flow():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    styles = (ROOT / "h5_static" / "h5-designer-v2.css").read_text(encoding="utf-8")
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")

    shell_start = styles.index("#workflowView .workflow-shell {")
    shell_styles = styles[shell_start : shell_start + 180]
    control_start = styles.index("#workflowView .workflow-control-card {")
    control_styles = styles[control_start : control_start + 180]
    assert 'workflow: ["我的AI员工", "24小时任务编排"]' in script
    assert "padding-top: 0;" in shell_styles
    assert "position: relative;" in control_styles
    assert "position: fixed;" not in control_styles
    assert "syncWorkflowControlCardBounds" not in script
    assert 'id="workflowOperationMenu"' in html
    assert 'id="workflowOperationList"' in html
    assert "#workflowView .workflow-operation-list {" in styles
    assert "20260803-workflow-dialog-keyboard-v2" in html


def test_workflow_day_dialog_and_template_drawer_stay_above_page_content():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    styles = (ROOT / "h5_static" / "h5-designer-v2.css").read_text(encoding="utf-8")
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")

    assert "function syncWorkflowPlanDayViewport()" in script
    assert "window.visualViewport?.addEventListener(\"resize\", syncWorkflowPlanDayViewport);" in script
    assert 'input?.focus({ preventScroll: true });' in script
    assert 'input?.scrollIntoView({ block: "center", inline: "nearest", behavior: "auto" });' in script
    assert "#workflowPlanDayModal {" in styles
    assert "z-index: 320;" in styles
    assert "height: var(--workflow-plan-viewport-height, 100dvh);" in styles
    assert "#workflowView .workflow-template-drawer {" in styles
    assert "z-index: 300;" in styles
    assert "20260803-workflow-dialog-keyboard-v2" in html
