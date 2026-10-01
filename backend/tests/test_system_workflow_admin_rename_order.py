# -*- coding: utf-8 -*-
"""系统模板：后台改名字必须生效；顺序由后台决定（H5 与后台一致），不是数据库创建顺序。"""
from __future__ import annotations

from datetime import datetime

from backend.app.api import admin, h5_workflows
from backend.app.models import H5WorkflowTemplate

KEY = "system_douyin_leads"


def _node(label: str = "抖音私信接管") -> dict:
    return {
        "id": "n1",
        "time": "09:00",
        "ability_key": "douyin_leads",
        "ability_label": label,
        "plan": {"title": label, "task_kind": "douyin_leads", "content": "", "payload": {"action": "stranger_message"}},
    }


def _catalog(db, key: str, name: str, *, order=None) -> H5WorkflowTemplate:
    meta = {"source": h5_workflows._SYSTEM_WORKFLOW_CATALOG_SOURCE, "system_template_key": key}
    if order is not None:
        meta["system_order"] = order
    row = H5WorkflowTemplate(
        owner_user_id=h5_workflows._SYSTEM_WORKFLOW_OWNER_ID,
        installation_id="",
        name=name,
        nodes=[_node()],
        status="active",
        meta=meta,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _mirror(db, key: str, name: str, user_id: int = 309) -> H5WorkflowTemplate:
    row = H5WorkflowTemplate(
        owner_user_id=user_id,
        installation_id="u309-mirror",
        name=name,
        nodes=[_node()],
        status="active",
        meta={"source": "system_mirror", "system_template_key": key},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_admin_rename_takes_effect(db_session):
    """后台「确认生效」带上 name 之后，库里的名字和后台列表都要变（以前压根没提交 name）。"""
    catalog = _catalog(db_session, KEY, "旧名字")
    body = admin.SystemWorkflowBody(name="销售全流程员工（新）", nodes=catalog.nodes, confirm=True)
    result = admin.admin_publish_system_workflow(
        key=KEY, body=body, ctx=admin.AdminContext(role="admin"), db=db_session,
    )
    assert result["name"] == "销售全流程员工（新）"
    db_session.refresh(catalog)
    assert catalog.name == "销售全流程员工（新）"
    items = admin._system_workflow_catalog_items(db_session)
    assert [it["name"] for it in items if it["key"] == KEY] == ["销售全流程员工（新）"]
    assert admin._system_workflow_key_labels(db_session)[KEY] == "销售全流程员工（新）"


def test_admin_rename_syncs_mirror_but_keeps_user_renamed_one(db_session):
    catalog = _catalog(db_session, KEY, "旧名字")
    mirror = _mirror(db_session, KEY, "旧名字")
    mine = _mirror(db_session, KEY, "我自己改的名字", user_id=310)
    body = admin.SystemWorkflowBody(name="新名字", nodes=catalog.nodes, confirm=True)
    admin.admin_publish_system_workflow(key=KEY, body=body, ctx=admin.AdminContext(role="admin"), db=db_session)
    db_session.refresh(mirror)
    db_session.refresh(mine)
    assert mirror.name == "新名字"
    assert mine.name == "我自己改的名字"


def test_admin_order_controls_admin_and_h5_order(db_session):
    """后台 ↑↓ 调整顺序后，后台列表和 H5 用的排序 helper 必须一致。"""
    for key, name in (("system_custom_a", "A"), ("system_custom_b", "B"), ("system_custom_c", "C")):
        _catalog(db_session, key, name)
    assert [it["key"] for it in admin._system_workflow_catalog_items(db_session)] == [
        "system_custom_a", "system_custom_b", "system_custom_c"]

    result = admin.admin_set_system_workflow_order(
        key="system_custom_c",
        body=admin.SystemWorkflowOrderBody(order=1),
        ctx=admin.AdminContext(role="admin"),
        db=db_session,
    )
    assert result["keys"] == ["system_custom_c", "system_custom_a", "system_custom_b"]
    assert [it["key"] for it in admin._system_workflow_catalog_items(db_session)] == [
        "system_custom_c", "system_custom_a", "system_custom_b"]

    rows = (
        db_session.query(H5WorkflowTemplate)
        .filter(H5WorkflowTemplate.owner_user_id == h5_workflows._SYSTEM_WORKFLOW_OWNER_ID)
        .all()
    )
    ordered = [row.meta["system_template_key"] for row in sorted(rows, key=h5_workflows.system_catalog_display_key)]
    assert ordered == ["system_custom_c", "system_custom_a", "system_custom_b"]

    admin.admin_set_system_workflow_order(
        key="system_custom_c",
        body=admin.SystemWorkflowOrderBody(order=3),
        ctx=admin.AdminContext(role="admin"),
        db=db_session,
    )
    assert [it["key"] for it in admin._system_workflow_catalog_items(db_session)] == [
        "system_custom_a", "system_custom_b", "system_custom_c"]


def test_admin_order_does_not_touch_nodes_or_publish_state(db_session):
    catalog = _catalog(db_session, "system_custom_x", "X", order=10)
    mark = [n["id"] for n in catalog.nodes]
    admin.admin_set_system_workflow_order(
        key="system_custom_x",
        body=admin.SystemWorkflowOrderBody(order=1),
        ctx=admin.AdminContext(role="admin"),
        db=db_session,
    )
    db_session.refresh(catalog)
    assert [n["id"] for n in catalog.nodes] == mark
    assert catalog.meta.get("system_published") is None
    assert catalog.meta.get("system_order") == 10



def test_nodes_are_sorted_by_time_on_save():
    """新增节点必须按「节点设置的开始时间」插到该在的位置，不是永远排最后。"""
    from backend.app.api.scheduled_tasks import normalize_workflow_nodes_for_save

    nodes = [
        {"time": "09:00", "ability_label": "A"},
        {"time": "19:00", "ability_label": "B"},
        {"time": "08:00", "ability_label": "C"},   # 后来新加的
        {"time": "12:15", "ability_label": "D"},   # 后来新加的
    ]
    cleaned, _fixed = normalize_workflow_nodes_for_save(nodes)
    assert [n["time"] for n in cleaned] == ["08:00", "09:00", "12:15", "19:00"]

    # 下级动作也按时间排
    parent = [{"time": "09:00", "ability_label": "P", "actions": [
        {"time": "10:00", "ability_label": "c1"},
        {"time": "09:30", "ability_label": "c2"},
    ]}]
    cleaned2, _ = normalize_workflow_nodes_for_save(parent)
    assert [a["time"] for a in cleaned2[0]["actions"]] == ["09:30", "10:00"]


def test_admin_catalog_items_return_nodes_sorted_by_time(db_session):
    row = _catalog(db_session, "system_custom_sorted", "排序测试")
    row.nodes = [
        {"id": "n1", "time": "09:00", "ability_label": "A"},
        {"id": "n2", "time": "20:00", "ability_label": "B"},
        {"id": "n3", "time": "08:00", "ability_label": "C"},
    ]
    db_session.commit()
    items = admin._system_workflow_catalog_items(db_session)
    got = next(it for it in items if it["key"] == "system_custom_sorted")
    assert [n["time"] for n in got["nodes"]] == ["08:00", "09:00", "20:00"]


def test_rename_syncs_mirror_with_legacy_h5_default_name(db_session):
    """H5 里 system_sales 的硬编码默认名是「销售24小时员工」，后台改名要一起刷新这种镜像。"""
    sales_key = "system_sales"   # H5 里这个 key 的硬编码默认名就是「销售24小时员工」
    catalog = _catalog(db_session, sales_key, "旧名字")
    legacy = _mirror(db_session, sales_key, "销售24小时员工", user_id=35)
    customized = _mirror(db_session, sales_key, "我自己改的", user_id=36)
    body = admin.SystemWorkflowBody(name="新名字", nodes=catalog.nodes, confirm=True)
    admin.admin_publish_system_workflow(key=sales_key, body=body, ctx=admin.AdminContext(role="admin"), db=db_session)
    db_session.refresh(legacy)
    db_session.refresh(customized)
    assert legacy.name == "新名字"
    assert customized.name == "我自己改的"
