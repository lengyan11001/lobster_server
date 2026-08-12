from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.app.api.auth import get_current_user
from backend.app.api import scheduled_tasks
from backend.app.db import get_db
from backend.app.models import H5ChatMessage, ScheduledTask, ScheduledTaskRun


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
