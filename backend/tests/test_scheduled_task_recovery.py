from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from backend.app.api import scheduled_tasks
from backend.app.api import wechat_channels_transcript
from backend.app.models import CreativeGenerationJob, ScheduledTaskRun


def _run(*, run_id: str, user_id: int, task_kind: str, status: str = "processing") -> ScheduledTaskRun:
    now = datetime.utcnow()
    return ScheduledTaskRun(
        id=run_id,
        user_id=user_id,
        title=run_id,
        task_kind=task_kind,
        content="",
        payload={},
        status=status,
        progress={"stage": "before_restart"},
        started_at=now,
        created_at=now,
        updated_at=now,
    )


def test_recovery_only_claims_server_side_processing_runs(db_session, test_user, monkeypatch):
    server_run = _run(run_id="server-run", user_id=test_user.id, task_kind="ip_content_daily")
    client_run = _run(run_id="client-run", user_id=test_user.id, task_kind="client_workflow")
    db_session.add_all([server_run, client_run])
    db_session.commit()
    calls: list[tuple[str, bool]] = []

    def fake_execute(db, row, now=None, *, resume=False):
        calls.append((row.id, resume))
        row.status = "completed"
        db.commit()

    monkeypatch.setattr(scheduled_tasks, "_execute_server_side_run", fake_execute)

    recovered = scheduled_tasks._recover_interrupted_server_side_runs(db_session)

    assert recovered == 1
    assert calls == [("server-run", True)]
    db_session.refresh(client_run)
    assert client_run.status == "processing"


def test_ip_content_default_timeout_allows_full_multi_batch_run(monkeypatch):
    monkeypatch.delenv("LOBSTER_IP_CONTENT_SCHEDULE_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("LOBSTER_SERVER_SIDE_SCHEDULE_TIMEOUT_SEC", raising=False)

    assert scheduled_tasks._server_side_timeout_seconds("ip_content_daily") == 1800.0


def test_client_processing_recovery_uses_recent_progress_activity():
    now = datetime.utcnow()
    row = _run(run_id="active-client", user_id=1, task_kind="client_workflow")
    row.claimed_at = now - timedelta(minutes=20)
    row.updated_at = now - timedelta(minutes=2)

    assert scheduled_tasks._client_processing_run_is_stale(row, now) is False


def test_native_wechat_takeover_is_not_requeued_during_its_thirty_minute_session():
    now = datetime.utcnow()
    row = _run(run_id="wechat-takeover", user_id=1, task_kind="client_workflow")
    row.payload = {"action": "native_wechat_poll"}
    row.claimed_at = now - timedelta(minutes=30)
    row.updated_at = row.claimed_at

    assert scheduled_tasks._client_processing_run_is_stale(row, now) is False

    row.claimed_at = now - timedelta(minutes=46)
    row.updated_at = row.claimed_at
    assert scheduled_tasks._client_processing_run_is_stale(row, now) is True


def test_client_restart_fails_previous_process_runs_and_keeps_current_run(db_session, test_user):
    now = datetime.utcnow()
    previous = _run(run_id="previous-process", user_id=test_user.id, task_kind="client_workflow")
    previous.installation_id = "online-1"
    previous.claimed_by_installation_id = "online-1"
    previous.progress = {"stage": "running", "client_process_id": "old-process"}
    current = _run(run_id="current-process", user_id=test_user.id, task_kind="client_workflow")
    current.installation_id = "online-1"
    current.claimed_by_installation_id = "online-1"
    current.progress = {"stage": "running", "client_process_id": "new-process"}
    other_device = _run(run_id="other-device", user_id=test_user.id, task_kind="client_workflow")
    other_device.installation_id = "online-2"
    other_device.claimed_by_installation_id = "online-2"
    db_session.add_all([previous, current, other_device])
    db_session.commit()

    failed = scheduled_tasks._fail_previous_client_runs(
        db_session,
        user_id=test_user.id,
        installation_id="online-1",
        client_process_id="new-process",
        now=now,
    )
    db_session.commit()

    assert failed == 1
    assert previous.status == "failed"
    assert previous.error == "客户端已重启，上一轮任务已中断"
    assert previous.progress["stage"] == "client_restarted"
    assert current.status == "processing"
    assert other_device.status == "processing"


def test_scheduled_task_heartbeat_preserves_initial_claim_time(db_session, test_user):
    from starlette.requests import Request

    claimed_at = datetime.utcnow() - timedelta(minutes=5)
    row = _run(run_id="heartbeat-run", user_id=test_user.id, task_kind="client_workflow")
    row.claimed_by_installation_id = "device-a"
    row.claimed_at = claimed_at
    row.updated_at = claimed_at
    db_session.add(row)
    db_session.commit()

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/scheduled-tasks/runs/heartbeat-run/event",
            "headers": [(b"x-installation-id", b"device-a")],
        }
    )
    scheduled_tasks.submit_scheduled_task_event(
        "heartbeat-run",
        scheduled_tasks.ScheduledTaskEventIn(type="heartbeat", payload={"heartbeat": True}),
        request,
        test_user,
        db_session,
    )

    db_session.refresh(row)
    assert row.claimed_at == claimed_at
    assert row.updated_at > claimed_at


def test_wechat_transcript_reuses_terminal_job_for_same_scheduled_run(db_session, test_user):
    row = CreativeGenerationJob(
        job_id="wct_resume_test",
        user_id=test_user.id,
        feature_type="wechat_channels_transcript",
        provider="tikhub+stt",
        status="completed",
        stage="completed",
        progress=100,
        title="resume test",
        request_payload={"username": "finder-user", "videos": []},
        result_payload={"count": 1, "completed_count": 1, "failed_count": 0},
        meta={"scheduled_run_id": "scheduled-run", "items": []},
    )
    db_session.add(row)
    db_session.commit()

    result = asyncio.run(
        wechat_channels_transcript.run_wechat_channels_transcript_payload_to_completion(
            db=db_session,
            current_user=test_user,
            payload={},
            run_id="scheduled-run",
        )
    )

    assert result["job_id"] == "wct_resume_test"
    assert result["status"] == "completed"
