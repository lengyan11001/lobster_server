"""抖音获客任务类型归一（2026-09-20 用户 309 等 14 个账号的事故）。

事故：H5 编辑器把抖音节点的 plan 存成 `task_kind=client_workflow` + `payload.action=douyin_leads`，
而客户端只认顶层 `task_kind=douyin_leads`（子动作放 `payload.action`）→ 客户端报
「暂不支持的客户端工作流：douyin_leads」。首条坏任务 2026-09-16，全库 851 条。

覆盖：
1. 归一逻辑：client_workflow+douyin_leads → douyin_leads + 子动作（params.sales_action 优先、否则按标签推断）；
2. 正常数据（真正的 client_workflow 动作、正常的 douyin_leads）不被改动；
3. 下发/展示用的 _serialize_run / _serialize_task 会归一（老客户端也能拿到正确类型）；
4. 工作流节点组装 _workflow_node_task_spec 会归一（新旧模板都能正确启动作业）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import types
from datetime import datetime
from pathlib import Path

import pytest

from backend.app.api import admin, h5_workflows, scheduled_tasks
from backend.app.models import H5WorkflowTemplate, ScheduledTask, ScheduledTaskRun


def _bad_payload(action: str = "douyin_leads", sales_action: str = "") -> dict:
    params: dict = {"max_users": 20}
    if sales_action:
        params["sales_action"] = sales_action
    return {
        "action": action,
        "params": params,
        "h5_context": {
            "ability_key": "douyin_leads",
            "ability_label": "抖音自动养号",
            "workflow_template_id": 317,
        },
    }


# —— 1. 归一逻辑 ——

def test_mislabeled_douyin_task_is_detected():
    assert scheduled_tasks.is_mislabeled_douyin_task("client_workflow", _bad_payload()) is True
    assert scheduled_tasks.is_mislabeled_douyin_task("douyin_leads", _bad_payload()) is False
    assert scheduled_tasks.is_mislabeled_douyin_task(
        "client_workflow", {"action": "publish_content"}
    ) is False


def test_normalize_uses_params_sales_action():
    kind, payload, changed = scheduled_tasks.normalize_douyin_task_kind(
        "client_workflow", _bad_payload(sales_action="self_comment_monitor")
    )
    assert changed is True
    assert kind == "douyin_leads"
    assert payload["action"] == "self_comment_monitor"
    assert payload["params"]["sales_action"] == "self_comment_monitor"
    # 与 _workflow_node_task_spec 一致：H5 触发都是一次性动作
    assert payload["h5_one_shot"] is True
    assert payload["douyin_execution_mode"] == "one_shot"


def test_normalize_infers_action_from_label():
    payload = _bad_payload()
    payload["h5_context"]["ability_label"] = "抖音我的评论区"
    kind, fixed, changed = scheduled_tasks.normalize_douyin_task_kind("client_workflow", payload)
    assert changed is True and kind == "douyin_leads"
    assert fixed["action"] == "self_comment_monitor"

    payload2 = _bad_payload()
    payload2["h5_context"]["ability_label"] = "抖音精准用户触达"
    _kind, fixed2, _changed = scheduled_tasks.normalize_douyin_task_kind("client_workflow", payload2)
    assert fixed2["action"] == "precise_touch"

    payload3 = _bad_payload()
    payload3["h5_context"]["ability_label"] = "抖音精准获客AI"
    _kind, fixed3, _changed = scheduled_tasks.normalize_douyin_task_kind("client_workflow", payload3)
    assert fixed3["action"] == "search_collect"


def test_normalize_keeps_healthy_rows_untouched():
    # 真正的客户端工作流动作
    kind, payload, changed = scheduled_tasks.normalize_douyin_task_kind(
        "client_workflow",
        {"action": "publish_content", "params": {"asset_id": "a1"}},
    )
    assert changed is False and kind == "client_workflow" and payload["action"] == "publish_content"
    # 本来就是 douyin_leads 的任务
    kind2, payload2, changed2 = scheduled_tasks.normalize_douyin_task_kind(
        "douyin_leads", {"action": "search_collect", "params": {}}
    )
    assert changed2 is False and kind2 == "douyin_leads"
    # chat_message 等其它类型
    _k, _p, changed3 = scheduled_tasks.normalize_douyin_task_kind("chat_message", {"action": "douyin_leads"})
    assert changed3 is False


# —— 2. 下发/展示归一 ——

def test_serialize_run_normalizes_legacy_row(db_session):
    row = ScheduledTaskRun(
        id="run-bad-1",
        task_id=18885,
        user_id=309,
        task_kind="client_workflow",
        title="演示-抖音精准获客AI",
        payload=_bad_payload(sales_action="account_nurture"),
        status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()

    data = scheduled_tasks._serialize_run(row)
    assert data["task_kind"] == "douyin_leads"          # 客户端拿到正确的顶层类型
    assert data["payload"]["action"] == "account_nurture"
    assert data["payload"]["h5_one_shot"] is True
    # 原行不被改写（只影响下发内容）
    db_session.refresh(row)
    assert row.task_kind == "client_workflow"


def test_serialize_task_normalizes_legacy_row(db_session):
    row = ScheduledTask(
        id=9001,
        user_id=309,
        title="抖音自动养号",
        task_kind="client_workflow",
        content="H5 工作流：抖音自动养号",
        payload=_bad_payload(sales_action="account_nurture"),
        schedule_type="daily",
        status="active",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()

    data = scheduled_tasks._serialize_task(row)
    assert data["task_kind"] == "douyin_leads"
    assert data["payload"]["action"] == "account_nurture"


# —— 3. 节点组装归一 ——

def test_workflow_node_task_spec_normalizes_douyin_node():
    node = {
        "id": "wf_test_1",
        "time": "20:00",
        "ability_key": "douyin_leads",
        "ability_label": "抖音精准获客AI",
        "plan": {
            "title": "抖音精准获客AI",
            "task_kind": "client_workflow",
            "content": "H5 工作流：抖音精准获客AI",
            "payload": _bad_payload(),
        },
    }
    spec = h5_workflows._workflow_node_task_spec(
        node, None, installation_id="u309-test", template_id=317, template_name="抖音获客员工阿飞学长"
    )
    assert spec["task_kind"] == "douyin_leads"
    assert spec["payload"]["action"] == "account_nurture"   # 标签「抖音自动养号」推断
    assert spec["payload"]["h5_one_shot"] is True
    assert spec["payload"]["h5_context"]["installation_id"] == "u309-test"


def test_clean_nodes_normalizes_on_save():
    nodes = [
        {
            "id": "wf_test_2",
            "time": "20:00",
            "ability_key": "douyin_leads",
            "ability_label": "抖音我的评论区",
            "plan": {
                "title": "抖音我的评论区",
                "task_kind": "client_workflow",
                "payload": _bad_payload(sales_action="self_comment_monitor"),
            },
        }
    ]
    cleaned = h5_workflows._clean_nodes(nodes)
    plan = cleaned[0]["plan"]
    assert plan["task_kind"] == "douyin_leads"
    assert plan["payload"]["action"] == "self_comment_monitor"
    # 保存后的 JSON 里也不该再有坏组合
    assert json.loads(json.dumps(plan, ensure_ascii=False))["task_kind"] == "douyin_leads"


# —— 4. 标签 → 子动作：三端必须同源（编辑器 / 服务端 / 客户端）——

# 管理后台 NODE_PRESETS 里的 6 个抖音节点标签（admin.html）。
_EDITOR_DOUYIN_LABELS = {
    "抖音自动养号": "account_nurture",
    "抖音获客·关键词抓取精准客户": "search_collect",
    "抖音精准获客AI": "search_collect",
    "抖音私信接管": "stranger_message",
    "抖音精准用户触达": "precise_touch",
    "抖音我的评论区": "self_comment_monitor",
}
_EXTRA_DOUYIN_LABELS = {
    "抖音回复评论": "reply_comments",
    "抖音关注并评论": "follow_comment",
    "抖音主动私信10人": "direct_message",
    "抖音评论并@精准客户": "mention_comment",
}


def test_douyin_action_from_text_matches_editor_labels():
    for label, expected in {**_EDITOR_DOUYIN_LABELS, **_EXTRA_DOUYIN_LABELS}.items():
        assert scheduled_tasks.douyin_action_from_text(label) == expected, label


def test_douyin_action_priority_keeps_explicit_value():
    # params.sales_action 明确写了的，不能被标签推断顶掉
    payload = _bad_payload()
    payload["params"]["sales_action"] = "precise_touch"
    payload["h5_context"]["ability_label"] = "抖音自动养号"
    assert scheduled_tasks.douyin_action_from_hints(payload) == "precise_touch"


def _admin_html() -> str:
    path = Path(__file__).resolve().parent.parent / "app" / "static" / "admin.html"
    return path.read_text(encoding="utf-8")


def test_admin_editor_writes_top_level_douyin_kind():
    """管理后台编辑器不能再把抖音节点写成 client_workflow + action=douyin_leads。"""
    html = _admin_html()
    assert "if (key === 'douyin_leads') {" in html
    assert "kind = 'douyin_leads';" in html
    assert "action = salesDouyinAction(label, params);" in html


def test_admin_editor_js_action_matches_server(tmp_path):
    """编辑器 JS 的标签→子动作规则必须和服务端/客户端一致（用 node 实跑对比）。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过 JS/服务端一致性校验")
    html = _admin_html()
    match = re.search(r"  function salesDouyinAction\(label, params\) \{.*?\n  \}\n", html, re.S)
    assert match, "admin.html 里找不到 salesDouyinAction()"
    labels = list({**_EDITOR_DOUYIN_LABELS, **_EXTRA_DOUYIN_LABELS})
    driver = tmp_path / "check.js"
    driver.write_text(
        match.group(0)
        + "\nconst labels = " + json.dumps(labels, ensure_ascii=False) + ";\n"
        + "console.log(JSON.stringify(labels.map(function (l) { return salesDouyinAction(l, {}); })));\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        [node, str(driver)], capture_output=True, text=True, encoding="utf-8", check=True
    )
    js_result = json.loads(out.stdout.strip())
    py_result = [scheduled_tasks.douyin_action_from_text(label) for label in labels]
    assert js_result == py_result


# —— 5. 三条保存路径都必须归一 ——

def _system_catalog_row(db, key: str, nodes: list) -> H5WorkflowTemplate:
    row = H5WorkflowTemplate(
        owner_user_id=h5_workflows._SYSTEM_WORKFLOW_OWNER_ID,
        installation_id="",
        name="系统模板测试",
        nodes=nodes,
        status="active",
        meta={
            "source": h5_workflows._SYSTEM_WORKFLOW_CATALOG_SOURCE,
            "system_template_key": key,
        },
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    return row


def _mirror_row(db, key: str) -> H5WorkflowTemplate:
    row = H5WorkflowTemplate(
        owner_user_id=309,
        installation_id="u309-mirror",
        name="镜像",
        nodes=[],
        status="active",
        meta={"source": "system_mirror", "system_template_key": key},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    return row


def _douyin_node(label: str = "抖音私信接管") -> dict:
    return {
        "id": "wf_save_1",
        "time": "09:00",
        "ability_key": "douyin_leads",
        "ability_label": label,
        "department_id": "sales",
        "plan": {
            "title": label,
            "task_kind": "client_workflow",
            "content": "H5 工作流：" + label,
            "payload": {"action": "douyin_leads", "params": {}},
        },
    }


def test_save_system_workflow_template_normalizes_nodes(db_session):
    """H5/管理后台共用的系统模板保存接口：入库前归一。"""
    key = "system_douyin_leads"
    row = _system_catalog_row(db_session, key, [])
    mirror = _mirror_row(db_session, key)
    body = h5_workflows.SystemWorkflowTemplateIn(nodes=[_douyin_node()], confirm=True)
    result = h5_workflows.save_system_workflow_template(
        template_key=key,
        body=body,
        current_user=types.SimpleNamespace(role="admin"),
        db=db_session,
    )
    assert result["ok"] is True
    db_session.refresh(row)
    db_session.refresh(mirror)
    for saved in (row, mirror):
        plan = saved.nodes[0]["plan"]
        assert plan["task_kind"] == "douyin_leads"
        assert plan["payload"]["action"] == "stranger_message"
        assert plan["payload"]["params"]["sales_action"] == "stranger_message"


def test_admin_create_system_workflow_normalizes_nodes(db_session):
    """管理后台「新建系统模板」接口：入库前归一。"""
    body = admin.SystemWorkflowCreateBody(name="测试系统模板", nodes=[_douyin_node()])
    result = admin.admin_create_system_workflow(
        body=body,
        ctx=admin.AdminContext(role="admin"),
        db=db_session,
    )
    saved = (
        db_session.query(H5WorkflowTemplate)
        .filter(H5WorkflowTemplate.meta["system_template_key"].as_string() == result["key"])
        .first()
    )
    assert saved is not None
    plan = saved.nodes[0]["plan"]
    assert plan["task_kind"] == "douyin_leads"
    assert plan["payload"]["action"] == "stranger_message"


def test_admin_publish_system_workflow_normalizes_nodes(db_session):
    """管理后台「确认生效并同步」接口：正文和镜像都要归一。"""
    key = "system_douyin_leads"
    catalog = _system_catalog_row(db_session, key, [])
    mirror = _mirror_row(db_session, key)
    body = admin.SystemWorkflowBody(nodes=[_douyin_node("抖音我的评论区")], confirm=True)
    result = admin.admin_publish_system_workflow(
        key=key,
        body=body,
        ctx=admin.AdminContext(role="admin"),
        db=db_session,
    )
    assert result["ok"] is True
    db_session.refresh(catalog)
    db_session.refresh(mirror)
    for saved in (catalog, mirror):
        plan = saved.nodes[0]["plan"]
        assert plan["task_kind"] == "douyin_leads"
        assert plan["payload"]["action"] == "self_comment_monitor"


def test_normalize_workflow_nodes_for_save_covers_nested_nodes():
    nodes = [
        {"id": "n1", "plan": {"task_kind": "client_workflow", "payload": {"action": "douyin_leads"}}},
        {
            "id": "n2",
            "actions": [{"id": "a1", "plan": {"task_kind": "client_workflow", "payload": {"action": "douyin_leads"}}}],
            "children": [{"id": "c1", "plan": {"task_kind": "client_workflow", "payload": {"action": "publish_content"}}}],
        },
    ]
    cleaned, fixed = scheduled_tasks.normalize_workflow_nodes_for_save(nodes)
    assert fixed == 2
    assert cleaned[0]["plan"]["task_kind"] == "douyin_leads"
    assert cleaned[1]["actions"][0]["plan"]["task_kind"] == "douyin_leads"
    # 真正的客户端工作流动作不动
    assert cleaned[1]["children"][0]["plan"]["task_kind"] == "client_workflow"
