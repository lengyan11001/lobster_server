"""Online 工作流节点「演示」必须和"启动工作流"下发完全一致的任务。

背景（09-15 用户反馈：演示-数字人口播视频 失败"请选择数字人"）：
老实现里客户端直接把节点里残留的 plan 原样下发，数字人节点那份还是 1.0 的
`hifly.video.create_by_tts` + 空参数；而真正启用工作流时会走服务端组装，换成
`shanjian_digital_human_video` + 2.0 参数（形象/声音候选、script_source 等）。
所以演示必然秒失败，用户看到的能力和启用时不是一回事。
"""

import copy
import ast
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.api import h5_workflows as workflow_api
from backend.app.api.h5_workflows import (
    WorkflowDemoPlanBody,
    _activate_nodes_for_device,
    _prepare_activation_nodes,
    _workflow_node_task_spec,
    workflow_demo_plan,
)


ROOT = Path(__file__).resolve().parents[2]
ONLINE_JS = ROOT.parent / "lobster_online" / "static" / "js" / "views" / "h5-employees.js"

TEMPLATE_NAME = "销售24小时员工"
SALES_META = {"system_template_key": "system_sales", "source": "system_mirror"}


def _digital_human_node() -> dict:
    """Online 销售员工模板里"创作数字人口播视频"节点的真实形状（1.0 残留 plan）。"""
    return {
        "id": "sales_dh_1",
        "time": "06:30",
        "end_time": "07:00",
        "department_id": "sales",
        "department_name": "销售部",
        "ability_key": "hifly.video.create_by_tts",
        "ability_label": "创作数字人口播视频",
        "note": "创作一条数字人口播视频（用于发朋友圈）",
        "plan": {
            "title": "创作数字人口播视频",
            "task_kind": "capability",
            "content": "H5 工作流：创作数字人口播视频",
            "payload": {
                "capability_id": "hifly.video.create_by_tts",
                "payload": {"script": "创作一条数字人口播视频（用于发朋友圈）"},
                "params": {},
            },
        },
    }


def _prepared_2_0_node() -> dict:
    """服务端组装后的 2.0 数字人节点（对应 shanjian_digital_human_video 分支）。"""
    node = copy.deepcopy(_digital_human_node())
    node["ability_key"] = "shanjian_digital_human_video"
    node["plan"]["task_kind"] = "client_workflow"
    node["plan"]["payload"] = {
        "action": "shanjian_digital_human_video",
        "params": {
            "script_source": "ip_daily_industry_hot_oral",
            "virtualman_candidates": [{"profile_id": 11, "virtualman_id": "vm-11"}],
            "virtualman_selection_mode": "daily_round_robin",
            "voice": "voice-7",
            "speaker_id": "voice-7",
            "voice_candidates": [{"id": "voice-7"}],
            "keyword_ids": [3],
            "keywords": ["留学中介"],
        },
    }
    return node


def _douyin_node() -> dict:
    return {
        "id": "douyin_private_1",
        "time": "14:45",
        "ability_key": "douyin_leads",
        "ability_label": "抖音私信接管",
        "plan": {
            "title": "抖音私信接管",
            "task_kind": "douyin_leads",
            "payload": {"action": "stranger_message", "params": {}},
        },
    }


def test_demo_plan_is_registered_on_the_workflow_router():
    paths = {getattr(route, "path", "") for route in workflow_api.router.routes}

    assert "/api/h5-workflows/demo-plan" in paths


def test_demo_and_activation_send_the_same_task(db_session, test_user, monkeypatch):
    """演示计划必须逐字段等于启动时真正下发的那条任务。"""
    monkeypatch.setattr(workflow_api, "online_user_for_mobile_user", lambda db, user: user)
    monkeypatch.setattr(
        workflow_api,
        "_prepare_sales_workflow_nodes",
        lambda **kwargs: [copy.deepcopy(_prepared_2_0_node())],
    )

    _activation, _stopped, tasks = _activate_nodes_for_device(
        db=db_session,
        current_user=test_user,
        owner=test_user,
        installation_id="slot-abc",
        template_id=4711,
        template_owner_user_id=test_user.id,
        template_name=TEMPLATE_NAME,
        nodes=[_digital_human_node()],
        timezone_offset_minutes=480,
        snapshot_extra=dict(SALES_META),
    )
    assert len(tasks) == 1, "启动必须下发 1 条数字人任务"
    started = tasks[0]

    demo = workflow_demo_plan(
        WorkflowDemoPlanBody(
            name=TEMPLATE_NAME,
            nodes=[_digital_human_node()],
            meta=dict(SALES_META),
            installation_id="slot-abc",
            template_id=4711,
        ),
        x_installation_id="slot-abc",
        current_user=test_user,
        db=db_session,
    )

    assert demo["ok"] is True
    assert len(demo["plans"]) == 1
    plan = demo["plans"][0]
    assert plan["task_kind"] == started.task_kind == "client_workflow"
    # schedule_config 属于排程本身（启动是每天定点、演示是立即执行一次），不参与对比
    demo_payload = {key: value for key, value in plan["payload"].items() if key != "schedule_config"}
    started_payload = {key: value for key, value in dict(started.payload).items() if key != "schedule_config"}
    assert demo_payload == started_payload
    # 演示必须是 2.0 能力，而不是节点里残留的 1.0 形态（老实现就是照抄后者，
    # 执行端拿不到形象/声音，于是秒失败"请选择数字人"）。
    params = plan["payload"]["params"]
    assert plan["payload"]["action"] == "shanjian_digital_human_video"
    assert "capability_id" not in plan["payload"]
    assert params["script_source"] == "ip_daily_industry_hot_oral"
    assert plan["payload"] != _digital_human_node()["plan"]["payload"]
    # 关键词/人设/素材等模板资源已按当前槽位实时覆盖（和落库时同一步）
    assert params["virtualman_candidates"] == started_payload["params"]["virtualman_candidates"]
    assert params["keyword_ids"] == started_payload["params"]["keyword_ids"]
    # 执行端要靠 h5_context 里的槽位解析该设备的个人模板资源
    assert plan["payload"]["h5_context"]["installation_id"] == "slot-abc"
    assert plan["payload"]["h5_context"]["workflow_template_id"] == 4711
    assert plan["server_side"] is False


def test_demo_reports_the_same_missing_resources_as_activation(db_session, test_user, monkeypatch):
    """资料不全时，演示给出的原因必须和启动完全一样（这里走真实组装逻辑）。"""
    monkeypatch.setattr(workflow_api, "online_user_for_mobile_user", lambda db, user: user)

    with pytest.raises(HTTPException) as started_exc:
        _activate_nodes_for_device(
            db=db_session,
            current_user=test_user,
            owner=test_user,
            installation_id="slot-abc",
            template_id=4711,
            template_owner_user_id=test_user.id,
            template_name=TEMPLATE_NAME,
            nodes=[_digital_human_node()],
            timezone_offset_minutes=480,
            snapshot_extra=dict(SALES_META),
        )

    with pytest.raises(HTTPException) as demo_exc:
        workflow_demo_plan(
            WorkflowDemoPlanBody(
                name=TEMPLATE_NAME,
                nodes=[_digital_human_node()],
                meta=dict(SALES_META),
                installation_id="slot-abc",
                template_id=4711,
            ),
            x_installation_id="slot-abc",
            current_user=test_user,
            db=db_session,
        )

    assert demo_exc.value.status_code == 400
    assert demo_exc.value.detail == started_exc.value.detail
    assert "缺少" in demo_exc.value.detail


def test_douyin_demo_task_stays_one_shot(db_session, test_user, monkeypatch):
    """演示不能让抖音获客变成常驻监控：一次性标记必须和启动一致。"""
    monkeypatch.setattr(workflow_api, "online_user_for_mobile_user", lambda db, user: user)

    demo = workflow_demo_plan(
        WorkflowDemoPlanBody(
            name="抖音私信员工",
            nodes=[_douyin_node()],
            meta={},
            installation_id="slot-abc",
        ),
        x_installation_id="slot-abc",
        current_user=test_user,
        db=db_session,
    )

    payload = demo["plans"][0]["payload"]
    assert payload["h5_one_shot"] is True
    assert payload["douyin_execution_mode"] == "one_shot"
    assert payload["h5_task_source"] == "workflow"
    assert payload["h5_context"]["installation_id"] == "slot-abc"


def test_demo_rejects_coming_soon_nodes(db_session, test_user, monkeypatch):
    monkeypatch.setattr(workflow_api, "online_user_for_mobile_user", lambda db, user: user)

    with pytest.raises(HTTPException) as exc_info:
        workflow_demo_plan(
            WorkflowDemoPlanBody(
                name="销售24小时员工",
                nodes=[{"id": "ph_1", "time": "09:00", "plan": {"payload": {"skip_execution": True}}}],
                meta=dict(SALES_META),
                installation_id="slot-abc",
            ),
            x_installation_id="slot-abc",
            current_user=test_user,
            db=db_session,
        )

    assert exc_info.value.status_code == 400


def test_demo_requires_nodes(db_session, test_user, monkeypatch):
    monkeypatch.setattr(workflow_api, "online_user_for_mobile_user", lambda db, user: user)

    with pytest.raises(HTTPException) as exc_info:
        workflow_demo_plan(
            WorkflowDemoPlanBody(name=TEMPLATE_NAME, nodes=[], meta={}, installation_id="slot-abc"),
            x_installation_id="slot-abc",
            current_user=test_user,
            db=db_session,
        )

    assert exc_info.value.status_code == 400


def test_shared_helpers_stay_wired_for_both_paths():
    """启动路径和演示路径都必须调用同一对组装函数（防止以后被改回两套）。"""
    source = (ROOT / "backend" / "app" / "api" / "h5_workflows.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    called: dict[str, int] = {}
    defined: set[str] = set()
    for item in ast.walk(tree):
        if isinstance(item, ast.Call) and isinstance(item.func, ast.Name):
            called[item.func.id] = called.get(item.func.id, 0) + 1
        if isinstance(item, ast.FunctionDef):
            defined.add(item.name)

    # 定义 1 处 + 启动/演示各调用 1 处
    assert called.get("_prepare_activation_nodes") == 2
    assert called.get("_workflow_node_task_spec") == 2
    # 组装逻辑只剩启动/演示共用的那份在调用
    assert called.get("_prepare_sales_workflow_nodes") == 1
    assert "_prepare_activation_nodes" in defined
    assert "_workflow_node_task_spec" in defined


def test_online_demo_button_calls_the_server_plan_endpoint():
    if not ONLINE_JS.exists():
        pytest.skip("Online 前端仓库不在同一台机器上")
    source = ONLINE_JS.read_text(encoding="utf-8")
    start = source.index("function demoNode(index)")
    end = source.index("function runSubmission(", start)
    body = source[start:end]

    assert "/api/h5-workflows/demo-plan" in body
    assert "template_id:templateId" in body
    assert "plan.server_side ? [] : [iid]" in body
