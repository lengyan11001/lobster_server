from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.app.api.auth import get_current_user
from backend.app.api import scheduled_tasks
from backend.app.db import get_db
from backend.app.models import H5ChatEvent, H5ChatMessage, ScheduledTask, ScheduledTaskRun


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


def test_recurring_enqueue_refreshes_one_pending_run_in_place(db_session, test_user):
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

    assert second.id == first_id
    assert db_session.query(ScheduledTaskRun).filter(ScheduledTaskRun.task_id == task.id).count() == 1
    assert db_session.query(H5ChatMessage).filter(H5ChatMessage.id == second.h5_message_id).count() == 1
    assert second.content == "最新任务内容"
    assert second.created_at == second_at
    assert second.progress["coalesced_count"] == 1
    assert task.run_count == 2


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


def test_recurring_backlog_keeps_latest_and_skips_expired_without_touching_once_or_processing(
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

    assert skipped == 1
    assert db_session.get(ScheduledTaskRun, "recurring-old").status == "cancelled"
    assert db_session.get(ScheduledTaskRun, "recurring-latest").status == "pending"
    assert db_session.get(ScheduledTaskRun, "recurring-processing").status == "processing"
    assert db_session.get(ScheduledTaskRun, "once-old").status == "pending"


def test_single_expired_recurring_run_is_skipped(db_session, test_user):
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
    assert stale.progress["skip_reason"] == "expired_recurring_run"


def test_daily_scheduled_run_expires_after_thirty_minutes(db_session, test_user, monkeypatch):
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
    assert stale.progress["skip_reason"] == "expired_recurring_run"


def test_background_cleanup_coalesces_offline_recurring_runs(db_session, test_user):
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


def test_pending_claim_skips_expired_workflow_node_before_claim(db_session, test_user, monkeypatch):
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
    assert [item["id"] for item in result["items"]] == [available.id]
    cancelled = db_session.get(ScheduledTaskRun, expired.id)
    assert cancelled.status == "cancelled"
    assert cancelled.progress["reason"] == "workflow_node_deadline_expired"
    assert db_session.get(H5ChatMessage, message.id).status == "cancelled"
    assert any(
        event.payload.get("reason") == "workflow_node_deadline_expired"
        for event in db_session.query(H5ChatEvent).filter(H5ChatEvent.message_id == message.id).all()
    )


def test_expired_processing_workflow_node_releases_next_douyin_run(db_session, test_user, monkeypatch):
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
    assert [item["id"] for item in result["items"]] == [next_node.id]
    assert db_session.get(ScheduledTaskRun, processing.id).status == "cancelled"
    assert db_session.get(H5ChatMessage, message.id).status == "cancelled"
    assert db_session.get(ScheduledTaskRun, next_node.id).status == "processing"


def test_expired_workflow_node_heartbeat_cancels_instead_of_extending_run(db_session, test_user):
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

    with pytest.raises(HTTPException) as exc_info:
        scheduled_tasks.submit_scheduled_task_event(
            run.id,
            scheduled_tasks.ScheduledTaskEventIn(type="heartbeat", payload={"heartbeat": True}),
            _request(),
            test_user,
            db_session,
        )

    db_session.expire_all()
    assert exc_info.value.status_code == 409
    cancelled = db_session.get(ScheduledTaskRun, run.id)
    assert cancelled.status == "cancelled"
    assert cancelled.progress["reason"] == "workflow_node_deadline_expired"
    assert db_session.get(H5ChatMessage, message.id).status == "cancelled"


def test_expired_server_side_workflow_node_is_cancelled_before_execution(db_session, test_user):
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

    scheduled_tasks._execute_server_side_run(db_session, run, now=now)

    db_session.expire_all()
    cancelled = db_session.get(ScheduledTaskRun, run.id)
    assert cancelled.status == "cancelled"
    assert cancelled.progress["reason"] == "workflow_node_deadline_expired"


def test_pending_claim_scans_past_blocked_serial_runs(db_session, test_user, monkeypatch):
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

    assert [item["id"] for item in result["items"]] == ["workflow-pending"]
    assert db_session.get(ScheduledTaskRun, "douyin-pending").status == "pending"
    assert db_session.get(ScheduledTaskRun, "workflow-pending").status == "processing"


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

    assert [item["id"] for item in result["items"]] == ["video-pending"]
    assert db_session.get(ScheduledTaskRun, "wechat-pending").status == "pending"
