from __future__ import annotations

import asyncio
from datetime import datetime

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
