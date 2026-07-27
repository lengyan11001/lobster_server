from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.api.h5_workflows import WorkflowTemplateIn, create_workflow_template, update_workflow_template
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


def test_plain_save_cannot_create_another_template(db_session, test_user):
    body = WorkflowTemplateIn(name="普通保存", nodes=_sales_body("普通保存").nodes)

    with pytest.raises(HTTPException) as exc_info:
        create_workflow_template(body, current_user=test_user, db=db_session)

    assert exc_info.value.status_code == 400
    assert "只能通过复制创建" in str(exc_info.value.detail)
    assert db_session.query(H5WorkflowTemplate).count() == 0


def test_h5_editor_only_creates_from_system_mirror_or_explicit_copy():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")

    assert 'meta: { copied_from: String(tpl.id || ""), copied_source: tpl.source || "" }' in script
    assert 'system_template_key: "system_sales"' in script
    assert "新模板请从模板列表选择一个模板后点击复制" in script
    assert ".then(openCustomEmployeeList)" in script
    assert "function customWorkflowTemplateRows()" in script
    assert "const mirrors = new Map();" in script
    assert "return !workflowSystemTemplateKey(tpl) && !mergedIds.has(id);" in script
    assert "return personalSystemWorkflowTemplate(sid)" in script
    assert "if (state.workflowTemplateSaving) return;" in script
    assert 'key === "system_sales" ? "/h5-static/designer-employee-sales.jpg" : ""' in script
    assert "20260727-workflow-template-save-v3" in html
