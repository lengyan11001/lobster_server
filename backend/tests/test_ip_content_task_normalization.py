# -*- coding: utf-8 -*-
"""朋友圈图文/口播任务归一：task 行和 run 行必须一致（2026-10-20）。

线上事故：task=client_workflow + action=ip_content_moments，run 被归一成
ip_content_daily；服务端按 task 等设备领，设备按 run 认为是服务端任务不领，
run 永久 pending。这里锁住三件事：
  1. 归一函数本身；
  2. 建 run 时把归一写回 task；
  3. 历史 stranded pending 的 run 能被服务端兜底执行（不依赖设备）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend.app.api import scheduled_tasks
from backend.app.models import ScheduledTask, ScheduledTaskRun


def _moments_task(user_id: int) -> ScheduledTask:
    now = datetime.utcnow() - timedelta(minutes=1)
    row = ScheduledTask(
        user_id=user_id,
        title="朋友圈图文",
        task_kind="client_workflow",
        content="H5 工作流：朋友圈图文",
        payload={
            "action": "ip_content_moments",
            "params": {},
            "h5_context": {
                "workflow_node_id": "wf_pxmp1f0e",
                "workflow_node_time": "15:00",
                "workflow_node_end_time": "15:20",
            },
            "schedule_config": {"daily_times": ["15:00"], "timezone_offset_minutes": 480},
        },
        schedule_type="daily_times",
        target_installation_ids=["test-installation"],
        status="active",
        next_run_at=now,
        run_count=0,
        created_at=now,
        updated_at=now,
    )
    return row


def test_normalize_moments_action_to_server_task():
    kind, payload, changed = scheduled_tasks.normalize_ip_content_client_workflow(
        "client_workflow",
        {"action": "ip_content_moments", "h5_context": {"workflow_node_id": "n1"}},
    )
    assert changed is True
    assert kind == "ip_content_daily"
    assert "action" not in payload
    assert payload["tasks"] == ["moments_candidate"]
    assert payload["h5_context"]["workflow_node_id"] == "n1"


def test_normalize_is_idempotent_for_server_task():
    kind, payload, changed = scheduled_tasks.normalize_ip_content_client_workflow(
        "ip_content_daily", {"tasks": ["moments_candidate"]}
    )
    assert changed is False
    assert kind == "ip_content_daily"


def test_create_run_writes_normalization_back_to_task(db_session, test_user):
    task = _moments_task(test_user.id)
    db_session.add(task)
    db_session.commit()

    run = scheduled_tasks._create_run_for_target(db_session, task, "test-installation", datetime.utcnow())
    db_session.commit()

    assert run.task_kind == "ip_content_daily"
    assert "action" not in (run.payload or {})
    assert (run.payload or {})["tasks"] == ["moments_candidate"]

    db_session.refresh(task)
    assert task.task_kind == "ip_content_daily", "run 归一后必须回写任务，否则 task/run 类型打架"
    assert "action" not in (task.payload or {})
    assert (task.payload or {})["tasks"] == ["moments_candidate"]


def test_stranded_pending_server_side_run_is_not_left_to_client(db_session, test_user, monkeypatch):
    """历史 stranded run（run 是服务端类型、task 还是脏的）要被服务端接走执行。"""
    now = datetime.utcnow()
    task = _moments_task(test_user.id)
    db_session.add(task)
    db_session.flush()
    run = ScheduledTaskRun(
        id="stranded-run-1",
        task_id=task.id,
        user_id=test_user.id,
        title="朋友圈图文",
        task_kind="ip_content_daily",
        content="H5 工作流：朋友圈图文",
        payload={"tasks": ["moments_candidate"], "h5_context": {"workflow_node_id": "wf_pxmp1f0e"}},
        status="pending",
        progress={"queued_at": (now - timedelta(minutes=100)).isoformat()},
        installation_id="test-installation",
        created_at=now - timedelta(minutes=100),
        updated_at=now - timedelta(minutes=100),
    )
    db_session.add(run)
    db_session.commit()

    executed = []

    def fake_execute(db, row, at, resume=False):
        executed.append(row.id)
        row.status = "completed"
        row.finished_at = at
        row.updated_at = at
        db.commit()

    monkeypatch.setattr(scheduled_tasks, "_execute_server_side_run", fake_execute)

    recovered = scheduled_tasks._recover_stranded_pending_server_side_runs(db_session, now, min_age_seconds=60)

    assert recovered == 1
    assert executed == ["stranded-run-1"]
    db_session.refresh(task)
    assert task.task_kind == "ip_content_daily"


def test_enqueue_treats_normalized_moments_task_as_server_side(db_session, test_user, monkeypatch):
    """到点后不再等设备来领：归一后的朋友圈图文直接进服务端执行。"""
    executed = []

    def fake_execute(db, run, at, resume=False):
        executed.append(run.id)
        run.status = "completed"
        run.finished_at = at
        run.updated_at = at
        db.flush()

    monkeypatch.setattr(scheduled_tasks, "_execute_server_side_run", fake_execute)

    task = _moments_task(test_user.id)
    db_session.add(task)
    db_session.commit()

    runs = scheduled_tasks._enqueue_task(db_session, task, datetime.utcnow())
    db_session.commit()

    assert task.task_kind == "ip_content_daily"
    assert runs and executed == [runs[0].id], "归一后必须按服务端任务执行，而不是挂 pending 等设备"
