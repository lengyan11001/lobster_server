# -*- coding: utf-8 -*-
"""节点演示：不按节点时间窗，父节点完成后自动触发子节点（2026-10-21）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from backend.app.api import scheduled_tasks
from backend.app.models import ScheduledTask, ScheduledTaskRun


def _demo_payload(node_id: str, *, parent_node_id: str = "", template_id: int = 542) -> dict:
    ctx = {
        "template_source": "personal_current",
        "workflow_template_id": template_id,
        "workflow_node_id": node_id,
        "workflow_node_time": "08:00",
        "workflow_node_end_time": "08:30",
    }
    if parent_node_id:
        ctx["workflow_parent_node_id"] = parent_node_id
    return {"tasks": ["moments_candidate"], "h5_context": ctx, "schedule_config": {"timezone_offset_minutes": 480}}


def test_demo_detection():
    payload = _demo_payload("wf_parent")
    assert scheduled_tasks._is_demo_once_payload(payload, "once", "user") is True
    assert scheduled_tasks._is_demo_once_payload(payload, "daily_times", "user") is False
    assert scheduled_tasks._is_demo_once_payload(payload, "once", "workflow") is False


def test_demo_task_ignores_past_node_time(db_session, test_user, monkeypatch):
    kicks = []
    monkeypatch.setattr(scheduled_tasks, "_kick_demo_server_task", lambda tid: kicks.append(tid))
    past = (datetime.utcnow() - timedelta(hours=4)).replace(microsecond=0).isoformat() + "Z"
    body = scheduled_tasks.ScheduledTaskCreate(
        title="演示-朋友圈图文",
        task_kind="ip_content_daily",
        content="演示",
        payload=_demo_payload("wf_parent"),
        schedule_type="once",
        start_at=past,
        timezone_offset_minutes=480,
        installation_ids=["test-installation"],
    )
    task = scheduled_tasks._create_task_row(
        db_session, body, target_user_id=test_user.id, created_by_user_id=test_user.id, created_by_role="user"
    )
    db_session.commit()
    assert task.next_run_at is not None
    assert abs((task.next_run_at - datetime.utcnow()).total_seconds()) < 60, "演示必须按点击时间开始，而不是节点时间"
    assert kicks == [task.id], "服务端演示任务要立刻在后台开跑"


def test_demo_task_not_expired_by_node_window(db_session, test_user):
    task = ScheduledTask(
        user_id=test_user.id,
        title="演示-朋友圈图文",
        task_kind="ip_content_daily",
        content="演示",
        payload=_demo_payload("wf_parent"),
        schedule_type="once",
        status="active",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(task)
    db_session.commit()
    past = datetime.utcnow() - timedelta(hours=4)
    assert scheduled_tasks._scheduled_occurrence_expired(db_session, task, past, datetime.utcnow()) is False


def test_parent_completion_enqueues_demo_child(db_session, test_user):
    parent = ScheduledTask(
        user_id=test_user.id,
        title="演示-朋友圈图文",
        task_kind="ip_content_daily",
        content="演示",
        payload=_demo_payload("wf_parent"),
        schedule_type="once",
        status="completed",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    child = ScheduledTask(
        user_id=test_user.id,
        title="演示-发布到朋友圈",
        task_kind="client_workflow",
        content="演示发布",
        payload={"action": "publish_content", "params": {}, "h5_context": {**_demo_payload("wf_parent_action_1", parent_node_id="wf_parent")["h5_context"]}},
        schedule_type="once",
        status="active",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add_all([parent, child])
    db_session.commit()

    count = scheduled_tasks._enqueue_demo_children(db_session, parent, datetime.utcnow())
    db_session.commit()

    assert count == 1
    run = db_session.query(ScheduledTaskRun).filter(ScheduledTaskRun.task_id == child.id).first()
    assert run is not None and run.status == "pending", "父节点完成后子节点必须立刻进队列"

def test_demo_child_waits_for_parent(db_session, test_user, monkeypatch):
    """演示的子节点（发布）创建时不能立刻入队，必须等父节点完成。"""
    monkeypatch.setattr(scheduled_tasks, "_kick_demo_server_task", lambda tid: None)
    payload = _demo_payload("wf_parent_action_1", parent_node_id="wf_parent")
    payload["action"] = "publish_content"
    body = scheduled_tasks.ScheduledTaskCreate(
        title="演示-发布到朋友圈",
        task_kind="client_workflow",
        content="演示发布",
        payload=payload,
        schedule_type="once",
        timezone_offset_minutes=480,
        installation_ids=["test-installation"],
    )
    task = scheduled_tasks._create_task_row(
        db_session, body, target_user_id=test_user.id, created_by_user_id=test_user.id, created_by_role="user"
    )
    db_session.commit()
    assert task.next_run_at is None, "子节点不能按点击时间开始"
    assert db_session.query(ScheduledTaskRun).filter(ScheduledTaskRun.task_id == task.id).count() == 0
