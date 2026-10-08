from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.app.api.auth import get_current_user
from backend.app.api import scheduled_tasks
from backend.app.db import get_db
from backend.app.models import H5ChatDevicePresence, H5ChatEvent, H5ChatMessage, ScheduledTask, ScheduledTaskRun


def _task(user_id: int, *, title: str = "周期任务", schedule_type: str = "daily_times") -> ScheduledTask:
    now = datetime.utcnow()
    return ScheduledTask(
        user_id=user_id,
        title=title,
        task_kind="openclaw_message",
        content=title,
        payload={},
        schedule_type=schedule_type,
        interval_seconds=300 if schedule_type == "interval" else None,
        target_installation_ids=["test-installation"],
        status="active",
        next_run_at=now,
        run_count=0,
        created_at=now,
        updated_at=now,
    )


def _run(
    *,
    run_id: str,
    user_id: int,
    task_id: int | None,
    task_kind: str,
    status: str,
    created_at: datetime,
    installation_id: str = "test-installation",
) -> ScheduledTaskRun:
    return ScheduledTaskRun(
        id=run_id,
        task_id=task_id,
        user_id=user_id,
        title=run_id,
        task_kind=task_kind,
        content=run_id,
        payload={},
        status=status,
        progress={},
        installation_id=installation_id,
        claimed_by_installation_id=installation_id if status == "processing" else None,
        claimed_at=created_at if status == "processing" else None,
        started_at=created_at if status == "processing" else None,
        created_at=created_at,
        updated_at=created_at,
    )


def _request(installation_id: str = "test-installation") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/scheduled-tasks/pending",
            "headers": [(b"x-installation-id", installation_id.encode("ascii"))],
            "query_string": b"",
        }
    )


def _douyin_recurring_task(user_id: int, *, title: str = "今天执行抖音获客", task_id: int | None = None) -> ScheduledTask:
    now = datetime.utcnow() - timedelta(minutes=1)
    row = ScheduledTask(
        user_id=user_id,
        title=title,
        task_kind="douyin_leads",
        content="H5 工作流：抖音获客",
        payload={
            "action": "search_collect",
            "params": {
                "keyword": "阀门厂家",
                "mode": "script",
                "regions": ["全国"],
            },
            "schedule_config": {"timezone_offset_minutes": 480},
        },
        schedule_type="interval",
        interval_seconds=3600,
        target_installation_ids=["test-installation"],
        status="active",
        next_run_at=now,
        run_count=0,
        created_at=now,
        updated_at=now,
    )
    if task_id is not None:
        row.id = task_id
    return row


def _workflow_payload(*, action: str, start: str, end: str, timezone_offset_minutes: int = 480) -> dict:
    return {
        "action": action,
        "h5_task_source": "workflow",
        "h5_context": {
            "workflow_node_id": f"node-{start}-{end}",
            "workflow_node_time": start,
            "workflow_node_end_time": end,
        },
        "schedule_config": {
            "timezone_offset_minutes": timezone_offset_minutes,
            "daily_times": [start],
        },
    }


def test_recurring_enqueue_creates_one_run_per_due_occurrence(db_session, test_user):
    task = _task(test_user.id)
    db_session.add(task)
    db_session.commit()
    first_at = datetime.utcnow() - timedelta(minutes=10)

    first = scheduled_tasks._create_run_for_target(db_session, task, "test-installation", first_at)
    db_session.commit()
    first_id = first.id
    second_at = datetime.utcnow()
    task.content = "最新任务内容"

    second = scheduled_tasks._create_run_for_target(db_session, task, "test-installation", second_at)
    db_session.commit()

    assert second.id != first_id
    assert db_session.query(ScheduledTaskRun).filter(ScheduledTaskRun.task_id == task.id).count() == 2
    assert db_session.query(H5ChatMessage).filter(H5ChatMessage.id == second.h5_message_id).count() == 1
    assert second.content == "最新任务内容"
    assert second.created_at == second_at
    assert "coalesced_count" not in second.progress
    assert task.run_count == 2


def test_due_run_is_not_materialized_while_installation_is_busy(db_session, test_user):
    now = datetime.utcnow()
    task = _task(test_user.id, title="到点排队任务", schedule_type="interval")
    task.next_run_at = now - timedelta(seconds=1)
    db_session.add(task)
    db_session.flush()
    processing = _run(
        run_id="busy-before-due-run",
        user_id=test_user.id,
        task_id=None,
        task_kind="douyin_leads",
        status="processing",
        created_at=now,
    )
    db_session.add(processing)
    db_session.commit()

    assert scheduled_tasks._enqueue_due_tasks(db_session, test_user.id, "test-installation") == 0
    assert db_session.query(ScheduledTaskRun).filter(ScheduledTaskRun.task_id == task.id).count() == 0
    assert task.next_run_at is not None and task.next_run_at <= now


def test_overdue_recurring_task_materializes_only_current_occurrence(db_session, test_user):
    now = datetime.utcnow()
    task = _task(test_user.id, title="补齐周期任务", schedule_type="interval")
    task.interval_seconds = 60
    task.next_run_at = now - timedelta(minutes=3)
    db_session.add(task)
    db_session.commit()

    assert scheduled_tasks._enqueue_due_tasks(db_session, test_user.id) == 1
    runs = (
        db_session.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.task_id == task.id)
        .order_by(ScheduledTaskRun.created_at.asc())
        .all()
    )
    assert len(runs) == 1
    assert runs[0].status == "pending"
    assert task.next_run_at is not None and task.next_run_at > now


def test_pausing_scheduled_task_cancels_processing_run(
    db_session,
    db_session_factory,
    test_user,
    patch_fuiou_settings,
):
    now = datetime.utcnow()
    task = _task(test_user.id, title="native wechat takeover", schedule_type="interval")
    task.task_kind = "client_workflow"
    task.payload = {"action": "native_wechat_poll"}
    task.target_installation_ids = ["device-a"]
    db_session.add(task)
    db_session.flush()
    message = H5ChatMessage(
        id="pause-processing-message",
        user_id=test_user.id,
        installation_id="device-a",
        claimed_by_installation_id="device-a",
        mode="scheduled_task",
        content="native wechat takeover",
        status="processing",
        created_at=now,
        updated_at=now,
        claimed_at=now,
    )
    run = _run(
        run_id="pause-processing-run",
        user_id=test_user.id,
        task_id=task.id,
        task_kind="client_workflow",
        status="processing",
        created_at=now,
        installation_id="device-a",
    )
    run.h5_message_id = message.id
    db_session.add_all([message, run])
    db_session.commit()
    task_id = task.id

    app = FastAPI()
    app.include_router(scheduled_tasks.router, prefix="")

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    def _get_current_user_override():
        s = db_session_factory()
        try:
            from backend.app.models import User

            return s.query(User).filter(User.id == test_user.id).first()
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _get_current_user_override
    client = TestClient(app)

    response = client.patch(f"/api/scheduled-tasks/tasks/{task_id}", json={"status": "paused"})

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(ScheduledTask, task_id).status == "paused"
    assert db_session.get(ScheduledTaskRun, run.id).status == "cancelled"
    assert db_session.get(H5ChatMessage, message.id).status == "cancelled"


def test_expired_pending_runs_are_skipped_without_touching_processing(
    db_session,
    test_user,
):
    now = datetime.utcnow()
    recurring = _task(test_user.id)
    once = _task(test_user.id, title="一次性任务", schedule_type="once")
    db_session.add_all([recurring, once])
    db_session.commit()
    rows = [
        _run(
            run_id="recurring-old",
            user_id=test_user.id,
            task_id=recurring.id,
            task_kind="openclaw_message",
            status="pending",
            created_at=now - timedelta(days=2),
        ),
        _run(
            run_id="recurring-latest",
            user_id=test_user.id,
            task_id=recurring.id,
            task_kind="openclaw_message",
            status="pending",
            created_at=now - timedelta(minutes=5),
        ),
        _run(
            run_id="recurring-processing",
            user_id=test_user.id,
            task_id=recurring.id,
            task_kind="openclaw_message",
            status="processing",
            created_at=now - timedelta(minutes=2),
        ),
        _run(
            run_id="once-old",
            user_id=test_user.id,
            task_id=once.id,
            task_kind="openclaw_message",
            status="pending",
            created_at=now - timedelta(days=2),
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()

    skipped = scheduled_tasks._coalesce_recurring_pending_runs(
        db_session,
        user_id=test_user.id,
        installation_id="test-installation",
        now=now,
    )
    db_session.commit()

    assert skipped == 2
    assert db_session.get(ScheduledTaskRun, "recurring-old").status == "cancelled"
    assert db_session.get(ScheduledTaskRun, "recurring-old").error is None
    assert db_session.get(ScheduledTaskRun, "recurring-old").result_payload["skipped"] is True
    assert db_session.get(ScheduledTaskRun, "recurring-old").result_payload["skip_reason"] == "expired_before_execution"
    assert db_session.get(ScheduledTaskRun, "recurring-latest").status == "pending"
    assert db_session.get(ScheduledTaskRun, "recurring-processing").status == "processing"
    assert db_session.get(ScheduledTaskRun, "once-old").status == "cancelled"


def test_old_recurring_run_is_skipped_before_claim(db_session, test_user):
    now = datetime.utcnow()
    recurring = _task(test_user.id, schedule_type="interval")
    db_session.add(recurring)
    db_session.commit()
    stale = _run(
        run_id="expired-interval",
        user_id=test_user.id,
        task_id=recurring.id,
        task_kind="openclaw_message",
        status="pending",
        created_at=now - timedelta(hours=2),
    )
    db_session.add(stale)
    db_session.commit()

    skipped = scheduled_tasks._coalesce_recurring_pending_runs(
        db_session,
        user_id=test_user.id,
        installation_id="test-installation",
        now=now,
    )
    db_session.commit()

    assert skipped == 1
    assert stale.status == "cancelled"


def test_daily_scheduled_run_is_skipped_after_node_window(db_session, test_user, monkeypatch):
    now = datetime.utcnow()
    monkeypatch.delenv("LOBSTER_CLIENT_DAILY_PENDING_MAX_AGE_SECONDS", raising=False)
    recurring = _task(test_user.id, schedule_type="daily_times")
    db_session.add(recurring)
    db_session.commit()
    stale = _run(
        run_id="expired-daily-time",
        user_id=test_user.id,
        task_id=recurring.id,
        task_kind="client_workflow",
        status="pending",
        created_at=now - timedelta(minutes=31),
    )
    db_session.add(stale)
    db_session.commit()

    skipped = scheduled_tasks._coalesce_recurring_pending_runs(
        db_session,
        user_id=test_user.id,
        installation_id="test-installation",
        now=now,
    )
    db_session.commit()

    assert skipped == 1
    assert stale.status == "cancelled"


def test_background_cleanup_expires_offline_recurring_runs(db_session, test_user):
    now = datetime.utcnow()
    recurring = _task(test_user.id)
    db_session.add(recurring)
    db_session.commit()
    old = _run(
        run_id="offline-recurring-old",
        user_id=test_user.id,
        task_id=recurring.id,
        task_kind="openclaw_message",
        status="pending",
        created_at=now - timedelta(hours=2),
    )
    latest = _run(
        run_id="offline-recurring-latest",
        user_id=test_user.id,
        task_id=recurring.id,
        task_kind="openclaw_message",
        status="pending",
        created_at=now - timedelta(minutes=2),
    )
    db_session.add_all([old, latest])
    db_session.commit()

    skipped = scheduled_tasks._cleanup_recurring_pending_backlog(db_session, now)
    db_session.commit()

    assert skipped == 1
    assert old.status == "cancelled"
    assert latest.status == "pending"


def test_background_cleanup_fails_abandoned_client_run_and_mirror(db_session, test_user, monkeypatch):
    now = datetime.utcnow()
    monkeypatch.setenv("LOBSTER_CLIENT_RUN_HARD_TIMEOUT_SECONDS", "3600")
    stale = _run(
        run_id="abandoned-client-run",
        user_id=test_user.id,
        task_id=None,
        task_kind="client_workflow",
        status="processing",
        created_at=now - timedelta(hours=2),
    )
    stale.h5_message_id = "abandoned-client-message"
    message = H5ChatMessage(
        id=stale.h5_message_id,
        user_id=test_user.id,
        mode="scheduled_task",
        content="旧任务",
        status="processing",
        created_at=stale.created_at,
        updated_at=stale.updated_at,
    )
    fresh = _run(
        run_id="active-client-run",
        user_id=test_user.id,
        task_id=None,
        task_kind="client_workflow",
        status="processing",
        created_at=now - timedelta(minutes=10),
    )
    db_session.add_all([stale, fresh, message])
    db_session.commit()

    failed = scheduled_tasks._fail_abandoned_client_runs(db_session, now)
    db_session.commit()

    assert failed == 1
    assert stale.status == "failed"
    assert stale.progress["stage"] == "client_progress_timeout"
    assert message.status == "failed"
    assert fresh.status == "processing"


def test_background_cleanup_keeps_stale_run_while_owning_device_is_still_online(db_session, test_user, monkeypatch):
    now = datetime.utcnow()
    monkeypatch.setenv("LOBSTER_CLIENT_RUN_HARD_TIMEOUT_SECONDS", "3600")
    stale = _run(
        run_id="stale-but-online-client-run",
        user_id=test_user.id,
        task_id=None,
        task_kind="douyin_leads",
        status="processing",
        created_at=now - timedelta(hours=3),
    )
    stale.updated_at = now - timedelta(hours=3)
    presence = H5ChatDevicePresence(
        user_id=test_user.id,
        installation_id="test-installation",
        last_seen_at=now - timedelta(seconds=20),
        created_at=now - timedelta(hours=3),
    )
    db_session.add_all([stale, presence])
    db_session.commit()

    failed = scheduled_tasks._fail_abandoned_client_runs(db_session, now)
    db_session.commit()

    assert failed == 0
    assert stale.status == "processing"


def test_workflow_node_deadline_uses_run_day_timezone_and_overnight_window():
    payload = _workflow_payload(action="direct_message", start="22:15", end="22:30")

    assert scheduled_tasks._workflow_node_deadline_utc(
        payload,
        reference_at=datetime(2026, 8, 18, 14, 15),
    ) == datetime(2026, 8, 18, 14, 30)

    overnight = _workflow_payload(action="direct_message", start="23:45", end="00:15")
    assert scheduled_tasks._workflow_node_deadline_utc(
        overnight,
        reference_at=datetime(2026, 8, 18, 15, 45),
    ) == datetime(2026, 8, 18, 16, 15)
    assert scheduled_tasks._workflow_node_deadline_utc({}, reference_at=datetime.utcnow()) is None


def test_workflow_node_deadline_prefers_materialized_absolute_deadline():
    payload = _workflow_payload(action="direct_message", start="22:15", end="22:30")
    materialized = scheduled_tasks._materialize_workflow_node_window(
        payload,
        scheduled_at=datetime(2026, 8, 19, 14, 15),
    )

    assert scheduled_tasks._workflow_node_deadline_utc(
        materialized,
        reference_at=datetime(2026, 8, 2, 14, 15),
    ) == datetime(2026, 8, 19, 14, 30)
    assert materialized["h5_context"]["workflow_node_scheduled_at"] == "2026-08-19T14:15:00"
    assert materialized["h5_context"]["workflow_node_deadline_at"] == "2026-08-19T14:30:00"
    assert "workflow_node_deadline_at" not in payload["h5_context"]


def test_materialized_workflow_node_replaces_a_stale_absolute_deadline():
    payload = _workflow_payload(action="direct_message", start="22:15", end="22:30")
    payload["h5_context"]["workflow_node_scheduled_at"] = "2026-08-18T14:15:00"
    payload["h5_context"]["workflow_node_deadline_at"] = "2026-08-18T14:30:00"

    materialized = scheduled_tasks._materialize_workflow_node_window(
        payload,
        scheduled_at=datetime(2026, 8, 19, 14, 15),
    )

    assert materialized["h5_context"]["workflow_node_scheduled_at"] == "2026-08-19T14:15:00"
    assert materialized["h5_context"]["workflow_node_deadline_at"] == "2026-08-19T14:30:00"


def test_materialized_workflow_run_uses_scheduled_trigger_day(db_session, test_user):
    task = _task(test_user.id, title="workflow node")
    task.task_kind = "douyin_leads"
    task.created_by_role = "workflow"
    task.payload = _workflow_payload(action="direct_message", start="22:15", end="22:30")
    db_session.add(task)
    db_session.commit()

    run = scheduled_tasks._create_run_for_target(
        db_session,
        task,
        "test-installation",
        datetime(2026, 8, 20, 14, 45),
        scheduled_at=datetime(2026, 8, 19, 14, 15),
    )

    assert run.payload["h5_context"]["workflow_node_scheduled_at"] == "2026-08-19T14:15:00"
    assert run.payload["h5_context"]["workflow_node_deadline_at"] == "2026-08-19T14:30:00"
    assert "workflow_node_deadline_at" not in task.payload["h5_context"]


def test_pending_materializes_due_run_before_honoring_empty_cache(db_session, test_user, monkeypatch):
    calls: list[str] = []

    def fake_enqueue(*_args, **_kwargs):
        calls.append("enqueue")
        return 0

    def fake_empty_cache(*_args, **_kwargs):
        calls.append("cache")
        return True

    monkeypatch.setattr(scheduled_tasks, "_enqueue_due_tasks", fake_enqueue)
    monkeypatch.setattr(scheduled_tasks, "_pending_empty_recent", fake_empty_cache)

    result = scheduled_tasks.pending_scheduled_task_runs(
        _request(),
        limit=1,
        current_user_id=test_user.id,
        db=db_session,
    )

    assert result["throttled"] is True
    assert calls[:2] == ["enqueue", "cache"]


def test_pending_claim_keeps_expired_workflow_node_in_fifo_order(db_session, test_user, monkeypatch):
    now = datetime.utcnow().replace(second=0, microsecond=0)
    created_at = now - timedelta(hours=2)
    local_start = (created_at + timedelta(hours=8)).replace(second=0, microsecond=0)
    local_end = local_start + timedelta(minutes=1)
    expired = _run(
        run_id="expired-workflow-pending",
        user_id=test_user.id,
        task_id=None,
        task_kind="douyin_leads",
        status="pending",
        created_at=created_at,
    )
    expired.payload = _workflow_payload(
        action="direct_message",
        start=local_start.strftime("%H:%M"),
        end=local_end.strftime("%H:%M"),
    )
    expired.created_by_role = "workflow"
    expired.h5_message_id = "expired-workflow-pending-message"
    message = H5ChatMessage(
        id=expired.h5_message_id,
        user_id=test_user.id,
        mode="scheduled_task",
        content="expired workflow node",
        status="pending",
        created_at=created_at,
        updated_at=created_at,
    )
    available = _run(
        run_id="available-after-expired-pending",
        user_id=test_user.id,
        task_id=None,
        task_kind="client_workflow",
        status="pending",
        created_at=now - timedelta(minutes=1),
    )
    db_session.add_all([expired, message, available])
    db_session.commit()
    monkeypatch.setattr(scheduled_tasks, "_enqueue_due_tasks", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(scheduled_tasks, "_touch_installation_slot_lazy", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scheduled_tasks, "_pending_empty_recent", lambda *_args, **_kwargs: False)

    result = scheduled_tasks.pending_scheduled_task_runs(
        _request(),
        limit=1,
        current_user_id=test_user.id,
        db=db_session,
    )

    db_session.expire_all()
    assert [item["id"] for item in result["items"]] == [expired.id]
    claimed = db_session.get(ScheduledTaskRun, expired.id)
    assert claimed.status == "processing"
    assert db_session.get(H5ChatMessage, message.id).status == "processing"
    assert db_session.get(ScheduledTaskRun, available.id).status == "pending"


def test_processing_workflow_node_keeps_next_douyin_run_queued(db_session, test_user, monkeypatch):
    now = datetime.utcnow().replace(second=0, microsecond=0)
    created_at = now - timedelta(hours=2)
    local_start = (created_at + timedelta(hours=8)).replace(second=0, microsecond=0)
    local_end = local_start + timedelta(minutes=1)
    processing = _run(
        run_id="expired-workflow-processing",
        user_id=test_user.id,
        task_id=None,
        task_kind="douyin_leads",
        status="processing",
        created_at=created_at,
    )
    processing.payload = _workflow_payload(
        action="direct_message",
        start=local_start.strftime("%H:%M"),
        end=local_end.strftime("%H:%M"),
    )
    processing.created_by_role = "workflow"
    processing.h5_message_id = "expired-workflow-processing-message"
    message = H5ChatMessage(
        id=processing.h5_message_id,
        user_id=test_user.id,
        mode="scheduled_task",
        content="expired processing workflow node",
        status="processing",
        created_at=created_at,
        updated_at=created_at,
    )
    next_node = _run(
        run_id="next-douyin-node",
        user_id=test_user.id,
        task_id=None,
        task_kind="douyin_leads",
        status="pending",
        created_at=now - timedelta(minutes=1),
    )
    next_node.payload = {"action": "stranger_message"}
    db_session.add_all([processing, message, next_node])
    db_session.commit()
    monkeypatch.setattr(scheduled_tasks, "_enqueue_due_tasks", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(scheduled_tasks, "_touch_installation_slot_lazy", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scheduled_tasks, "_pending_empty_recent", lambda *_args, **_kwargs: False)

    result = scheduled_tasks.pending_scheduled_task_runs(
        _request(),
        limit=1,
        current_user_id=test_user.id,
        db=db_session,
    )

    db_session.expire_all()
    assert result["items"] == []
    assert db_session.get(ScheduledTaskRun, processing.id).status == "processing"
    assert db_session.get(H5ChatMessage, message.id).status == "processing"
    assert db_session.get(ScheduledTaskRun, next_node.id).status == "pending"


def test_workflow_node_heartbeat_extends_run_after_window(db_session, test_user):
    now = datetime.utcnow().replace(second=0, microsecond=0)
    created_at = now - timedelta(hours=2)
    local_start = (created_at + timedelta(hours=8)).replace(second=0, microsecond=0)
    local_end = local_start + timedelta(minutes=1)
    run = _run(
        run_id="expired-workflow-heartbeat",
        user_id=test_user.id,
        task_id=None,
        task_kind="client_workflow",
        status="processing",
        created_at=created_at,
    )
    run.payload = _workflow_payload(
        action="native_wechat_poll",
        start=local_start.strftime("%H:%M"),
        end=local_end.strftime("%H:%M"),
    )
    run.h5_message_id = "expired-workflow-heartbeat-message"
    message = H5ChatMessage(
        id=run.h5_message_id,
        user_id=test_user.id,
        mode="scheduled_task",
        content="expired workflow heartbeat",
        status="processing",
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add_all([run, message])
    db_session.commit()

    result = scheduled_tasks.submit_scheduled_task_event(
        run.id,
        scheduled_tasks.ScheduledTaskEventIn(type="heartbeat", payload={"heartbeat": True}),
        _request(),
        test_user,
        db_session,
    )

    db_session.expire_all()
    assert result["ok"] is True
    retained = db_session.get(ScheduledTaskRun, run.id)
    assert retained.status == "processing"
    assert db_session.get(H5ChatMessage, message.id).status == "processing"


def test_expired_server_side_workflow_node_is_not_cancelled_before_execution(db_session, test_user):
    now = datetime.utcnow().replace(second=0, microsecond=0)
    created_at = now - timedelta(hours=2)
    local_start = (created_at + timedelta(hours=8)).replace(second=0, microsecond=0)
    local_end = local_start + timedelta(minutes=1)
    run = _run(
        run_id="expired-server-workflow-node",
        user_id=test_user.id,
        task_id=None,
        task_kind="ip_content_daily",
        status="pending",
        created_at=created_at,
    )
    run.created_by_role = "workflow"
    run.payload = _workflow_payload(
        action="server_content",
        start=local_start.strftime("%H:%M"),
        end=local_end.strftime("%H:%M"),
    )
    db_session.add(run)
    db_session.commit()

    assert scheduled_tasks._expire_workflow_node_run(db_session, run, now=now) is False
    assert run.status == "pending"


def test_pending_claim_does_not_claim_other_runs_while_installation_is_processing(db_session, test_user, monkeypatch):
    now = datetime.utcnow()
    processing = _run(
        run_id="douyin-processing",
        user_id=test_user.id,
        task_id=None,
        task_kind="douyin_leads",
        status="processing",
        created_at=now,
    )
    blocked = _run(
        run_id="douyin-pending",
        user_id=test_user.id,
        task_id=None,
        task_kind="douyin_leads",
        status="pending",
        created_at=now - timedelta(minutes=2),
    )
    available = _run(
        run_id="workflow-pending",
        user_id=test_user.id,
        task_id=None,
        task_kind="client_workflow",
        status="pending",
        created_at=now - timedelta(minutes=1),
    )
    db_session.add_all([processing, blocked, available])
    db_session.commit()
    monkeypatch.setattr(scheduled_tasks, "_enqueue_due_tasks", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(scheduled_tasks, "_touch_installation_slot_lazy", lambda *_args, **_kwargs: None)

    result = scheduled_tasks.pending_scheduled_task_runs(
        _request(),
        limit=1,
        current_user_id=test_user.id,
        db=db_session,
    )

    assert result["items"] == []
    assert db_session.get(ScheduledTaskRun, "douyin-pending").status == "pending"
    assert db_session.get(ScheduledTaskRun, "workflow-pending").status == "pending"


def test_pending_poll_does_not_materialize_due_work_behind_processing_run(db_session, test_user, monkeypatch):
    now = datetime.utcnow()
    task = _task(test_user.id, title="忙碌期间到点", schedule_type="interval")
    task.next_run_at = now - timedelta(seconds=1)
    db_session.add(task)
    processing = _run(
        run_id="processing-before-poll-enqueue",
        user_id=test_user.id,
        task_id=None,
        task_kind="client_workflow",
        status="processing",
        created_at=now,
    )
    db_session.add(processing)
    db_session.commit()
    monkeypatch.setattr(scheduled_tasks, "_touch_installation_slot_lazy", lambda *_args, **_kwargs: None)

    result = scheduled_tasks.pending_scheduled_task_runs(
        _request(),
        limit=1,
        current_user_id=test_user.id,
        db=db_session,
    )

    assert result["items"] == []
    assert db_session.query(ScheduledTaskRun).filter(ScheduledTaskRun.task_id == task.id).count() == 0


def test_idle_poll_materializes_and_claims_one_due_task(db_session, test_user, monkeypatch):
    now = datetime.utcnow()
    first = _task(test_user.id, title="当前任务一", schedule_type="once")
    second = _task(test_user.id, title="当前任务二", schedule_type="once")
    first.next_run_at = now - timedelta(seconds=1)
    second.next_run_at = now - timedelta(seconds=1)
    db_session.add_all([first, second])
    db_session.commit()
    monkeypatch.setattr(scheduled_tasks, "_touch_installation_slot_lazy", lambda *_args, **_kwargs: None)

    result = scheduled_tasks.pending_scheduled_task_runs(
        _request(),
        limit=5,
        current_user_id=test_user.id,
        db=db_session,
    )

    assert len(result["items"]) == 1
    claimed = db_session.get(ScheduledTaskRun, result["items"][0]["id"])
    assert claimed is not None and claimed.status == "processing"
    assert db_session.query(ScheduledTaskRun).filter(ScheduledTaskRun.task_id == second.id).count() == 0


def test_expired_due_task_is_advanced_without_a_run(db_session, test_user):
    now = datetime.utcnow()
    task = _task(test_user.id, title="已过期任务", schedule_type="interval")
    task.next_run_at = now - timedelta(minutes=10)
    db_session.add(task)
    db_session.commit()

    assert scheduled_tasks._enqueue_due_tasks(db_session, test_user.id, "test-installation") == 0
    assert db_session.query(ScheduledTaskRun).filter(ScheduledTaskRun.task_id == task.id).count() == 0
    assert task.next_run_at is not None and task.next_run_at > now


def test_pending_claim_serializes_native_wechat_runs_per_installation(db_session, test_user, monkeypatch):
    now = datetime.utcnow()
    processing = _run(
        run_id="wechat-processing",
        user_id=test_user.id,
        task_id=None,
        task_kind="client_workflow",
        status="processing",
        created_at=now,
    )
    processing.payload = {"action": "native_wechat_poll"}
    pending_wechat = _run(
        run_id="wechat-pending",
        user_id=test_user.id,
        task_id=None,
        task_kind="client_workflow",
        status="pending",
        created_at=now - timedelta(minutes=2),
    )
    pending_wechat.payload = {"action": "native_wechat_poll"}
    available_video = _run(
        run_id="video-pending",
        user_id=test_user.id,
        task_id=None,
        task_kind="client_workflow",
        status="pending",
        created_at=now - timedelta(minutes=1),
    )
    available_video.payload = {"action": "shanjian_digital_human_video"}
    db_session.add_all([processing, pending_wechat, available_video])
    db_session.commit()
    monkeypatch.setattr(scheduled_tasks, "_enqueue_due_tasks", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(scheduled_tasks, "_touch_installation_slot_lazy", lambda *_args, **_kwargs: None)

    result = scheduled_tasks.pending_scheduled_task_runs(
        _request(),
        limit=2,
        current_user_id=test_user.id,
        db=db_session,
    )

    assert result["items"] == []
    assert db_session.get(ScheduledTaskRun, "wechat-pending").status == "pending"
    assert db_session.get(ScheduledTaskRun, "video-pending").status == "pending"


def test_pending_claim_returns_only_one_run_per_installation(db_session, test_user, monkeypatch):
    now = datetime.utcnow()
    first = _run(
        run_id="first-pending",
        user_id=test_user.id,
        task_id=None,
        task_kind="client_workflow",
        status="pending",
        created_at=now - timedelta(minutes=2),
    )
    second = _run(
        run_id="second-pending",
        user_id=test_user.id,
        task_id=None,
        task_kind="client_workflow",
        status="pending",
        created_at=now - timedelta(minutes=1),
    )
    db_session.add_all([first, second])
    db_session.commit()
    monkeypatch.setattr(scheduled_tasks, "_enqueue_due_tasks", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(scheduled_tasks, "_touch_installation_slot_lazy", lambda *_args, **_kwargs: None)

    result = scheduled_tasks.pending_scheduled_task_runs(
        _request(),
        limit=2,
        current_user_id=test_user.id,
        db=db_session,
    )

    assert [item["id"] for item in result["items"]] == ["first-pending"]
    assert db_session.get(ScheduledTaskRun, "first-pending").status == "processing"
    assert db_session.get(ScheduledTaskRun, "second-pending").status == "pending"


def test_create_task_queues_second_active_dispatch_for_installation(db_session, test_user, monkeypatch):
    monkeypatch.setattr(scheduled_tasks, "online_user_for_mobile_user", lambda _db, user: user)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/scheduled-tasks/tasks",
            "headers": [(b"x-installation-id", b"test-installation")],
            "query_string": b"",
        }
    )
    body = scheduled_tasks.ScheduledTaskCreate(
        title="first dispatch",
        task_kind="client_workflow",
        content="first",
        payload={"action": "publish_content"},
        schedule_type="once",
        installation_ids=["test-installation"],
    )
    first = scheduled_tasks.create_scheduled_task(body, request, test_user, db_session)
    assert first["runs"]

    second_body = scheduled_tasks.ScheduledTaskCreate(
        title="second dispatch",
        task_kind="client_workflow",
        content="second",
        payload={"action": "publish_content"},
        schedule_type="once",
        installation_ids=["test-installation"],
    )
    second = scheduled_tasks.create_scheduled_task(second_body, request, test_user, db_session)
    assert second["runs"] == []
    second_task = db_session.get(ScheduledTask, second["task"]["id"])
    assert second_task.next_run_at is not None


def test_create_recurring_douyin_task_reuses_same_active_definition(db_session, test_user):
    first = scheduled_tasks._create_task_row(
        db_session,
        scheduled_tasks.ScheduledTaskCreate(
            title="今天执行抖音获客",
            task_kind="douyin_leads",
            content="H5 工作流：抖音获客",
            payload={
                "action": "search_collect",
                "params": {
                    "keyword": "阀门厂家",
                    "mode": "script",
                    "regions": ["全国"],
                },
            },
            schedule_type="interval",
            interval_seconds=3600,
            start_at="2099-01-01T00:00",
            timezone_offset_minutes=0,
            installation_ids=["test-installation"],
        ),
        target_user_id=test_user.id,
        created_by_user_id=test_user.id,
        created_by_role="user",
    )
    second = scheduled_tasks._create_task_row(
        db_session,
        scheduled_tasks.ScheduledTaskCreate(
            title="今天执行抖音获客",
            task_kind="douyin_leads",
            content="H5 工作流：抖音获客",
            payload={
                "action": "search_collect",
                "params": {
                    "keyword": "船用阀门,氢能源阀门",
                    "mode": "script",
                    "regions": ["全国"],
                },
            },
            schedule_type="interval",
            interval_seconds=3600,
            start_at="2099-01-01T00:00",
            timezone_offset_minutes=0,
            installation_ids=["test-installation"],
        ),
        target_user_id=test_user.id,
        created_by_user_id=test_user.id,
        created_by_role="user",
    )

    rows = db_session.query(ScheduledTask).filter(ScheduledTask.user_id == test_user.id).all()
    assert second.id == first.id
    assert len(rows) == 1
    assert rows[0].payload["params"]["keyword"] == "船用阀门,氢能源阀门"


def test_due_recurring_douyin_duplicates_enqueue_once(db_session, test_user):
    db_session.add(_douyin_recurring_task(test_user.id, task_id=101))
    db_session.add(_douyin_recurring_task(test_user.id, task_id=102))
    db_session.commit()

    enqueued = scheduled_tasks._enqueue_due_tasks(db_session, user_id=test_user.id)
    runs = db_session.query(ScheduledTaskRun).filter(ScheduledTaskRun.user_id == test_user.id).all()

    assert enqueued == 1
    assert len(runs) == 1
    assert runs[0].task_id == 101


TAKEOVER_DEADLINE_TEXT = (
    "个微私信接管正常收工：节点时间已到，后续节点继续执行。\n\n"
    "接管实况\n- 已巡检：4 轮，耗时 6 分 12 秒"
)


def test_workflow_node_deadline_keeps_the_clients_takeover_report(db_session, test_user):
    run = _run(
        run_id="takeover-deadline-report",
        user_id=test_user.id,
        task_id=None,
        task_kind="client_workflow",
        status="processing",
        created_at=datetime.utcnow(),
    )
    run.payload = {"action": "native_wechat_poll", "params": {"account_id": "wechat-account-a"}}
    db_session.add(run)
    db_session.commit()

    result = scheduled_tasks.submit_scheduled_task_event(
        run.id,
        scheduled_tasks.ScheduledTaskEventIn(
            type="cancelled",
            payload={
                "reason": "workflow_node_deadline_expired",
                "deadline_at": "2026-09-13T09:30:00+00:00",
                "phase": "while_running",
                "text": TAKEOVER_DEADLINE_TEXT,
                "takeover": {
                    "completed_rounds": 4,
                    "replied": 3,
                    "skipped": 2,
                    "failed": 0,
                    "friend_requests_checked": 5,
                    "friend_requests_accepted": 2,
                    "friend_requests_failed": 1,
                    "group_invite_candidates": 1,
                },
                "local_stop": {"action": "native_wechat_poll", "stop_requested": True},
            },
        ),
        _request(),
        test_user,
        db_session,
    )

    db_session.expire_all()
    row = db_session.get(ScheduledTaskRun, run.id)
    assert result["cancelled"] is True
    assert row.status == "cancelled"
    assert row.result_text == TAKEOVER_DEADLINE_TEXT
    assert row.result_payload["skipped"] is True
    assert row.result_payload["skip_reason"] == "workflow_node_deadline_expired"
    assert row.result_payload["takeover"]["replied"] == 3
    assert row.result_payload["takeover"]["group_invite_candidates"] == 1
    assert row.result_payload["local_stop"]["stop_requested"] is True
    assert row.result_payload["phase"] == "while_running"


def test_workflow_node_deadline_fallback_reports_takeover_activity():
    text = scheduled_tasks._workflow_deadline_fallback_text(
        {
            "takeover": {
                "completed_rounds": 3,
                "replied": 2,
                "skipped": 1,
                "failed": 0,
                "friend_requests_checked": 4,
                "friend_requests_accepted": 2,
                "friend_requests_failed": 1,
                "group_invite_candidates": 1,
                "duration_label": "5 分 0 秒",
            }
        }
    )

    assert text.startswith("个微私信接管正常收工")
    assert "已巡检 3 轮，耗时 5 分 0 秒" in text
    assert "自动回复 2 个会话" in text
    assert "疑似加群线索 1 个会话" in text
    assert "本次任务已自动停止" not in text
    assert scheduled_tasks._workflow_deadline_fallback_text({}) == "节点时间已结束，本次任务已自动停止，后续节点继续执行。"


def test_demo_moments_task_uses_current_template_marker(db_session, test_user):
    """自编朋友圈演示的 payload 不带关键词/同行，但带了当前模板标记，不能被空参数拦住。"""
    task = scheduled_tasks._create_task_row(
        db_session,
        scheduled_tasks.ScheduledTaskCreate(
            title='演示-朋友圈图文',
            task_kind='ip_content_daily',
            content='H5 工作流：朋友圈图文',
            payload={
                'template_id': 0,
                'use_personal_default': True,
                'tasks': ['moments_candidate'],
                'sync_before': True,
                'requirements': {},
                'h5_context': {
                    'template_source': 'personal_current',
                    'workflow_node_id': 'moments_1',
                    'workflow_template_id': 88,
                    'ability_key': 'ip_content_moments',
                },
            },
            schedule_type='once',
            installation_ids=[],
        ),
        target_user_id=test_user.id,
        created_by_user_id=test_user.id,
        created_by_role='user',
    )
    assert task.payload['template_source'] == 'personal_current'
    assert task.payload['tasks'] == ['moments_candidate']


def test_manual_ip_daily_without_materials_still_rejected(db_session, test_user):
    with pytest.raises(HTTPException) as exc:
        scheduled_tasks._create_task_row(
            db_session,
            scheduled_tasks.ScheduledTaskCreate(
                title='手工日更',
                task_kind='ip_content_daily',
                content='手工',
                payload={'tasks': ['moments_candidate'], 'template_id': 0},
                schedule_type='once',
            ),
            target_user_id=test_user.id,
            created_by_user_id=test_user.id,
            created_by_role='user',
        )
    assert exc.value.status_code == 400
    assert '关键词' in exc.value.detail
