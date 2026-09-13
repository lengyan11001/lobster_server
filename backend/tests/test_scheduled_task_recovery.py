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
    assert previous.error == "客户端进程已更换（未收到正常退出标记），上一轮任务已中断"
    assert previous.progress["stage"] == "client_gone_unknown"
    assert previous.progress["error_code"] == "client_gone_unknown"
    assert current.status == "processing"
    assert other_device.status == "processing"


def _previous_process_run(db_session, test_user, run_id: str, *, exit_reason: str | None = None):
    now = datetime.utcnow()
    row = _run(run_id=run_id, user_id=test_user.id, task_kind="client_workflow")
    row.installation_id = "online-1"
    row.claimed_by_installation_id = "online-1"
    progress = {"stage": "running", "client_process_id": "old-process"}
    if exit_reason is not None:
        progress["client_exit_reason"] = exit_reason
    row.progress = progress
    db_session.add(row)
    db_session.commit()
    return row, now


def test_reported_clean_restart_is_classified_as_clean(db_session, test_user):
    """客户端重启后补报“自己停的”，不能再一律记成进程已更换。"""
    row, now = _previous_process_run(db_session, test_user, "reported-clean")

    failed = scheduled_tasks._fail_previous_client_runs(
        db_session,
        user_id=test_user.id,
        installation_id="online-1",
        client_process_id="new-process",
        now=now,
        reported_exit_reason="update_restart",
    )
    db_session.commit()
    db_session.refresh(row)

    assert failed == 1
    assert row.status == "failed"
    assert row.error == "客户端已正常重启，上一轮任务已中断"
    assert row.progress["stage"] == "client_restart_clean"
    assert row.progress["error_code"] == "client_restart_clean"
    assert row.progress["client_exit_reason"] == "update_restart"
    assert row.progress["previous_client_process_id"] == "old-process"


def test_reported_crash_is_classified_as_crashed(db_session, test_user):
    """上一轮没有退出标记 => 真崩溃。"""
    row, now = _previous_process_run(db_session, test_user, "reported-crash")

    failed = scheduled_tasks._fail_previous_client_runs(
        db_session,
        user_id=test_user.id,
        installation_id="online-1",
        client_process_id="new-process",
        now=now,
        reported_exit_reason="crash",
    )
    db_session.commit()
    db_session.refresh(row)

    assert failed == 1
    assert row.error == "客户端异常退出，上一轮任务已中断"
    assert row.progress["error_code"] == "client_crashed"
    assert row.progress["client_exit_reason"] == "crash"


def test_run_own_exit_reason_wins_over_reported_reason(db_session, test_user):
    """run 自己记过原因时，以它为准，不被本次补报覆盖。"""
    row, now = _previous_process_run(db_session, test_user, "own-reason", exit_reason="user_closed")

    scheduled_tasks._fail_previous_client_runs(
        db_session,
        user_id=test_user.id,
        installation_id="online-1",
        client_process_id="new-process",
        now=now,
        reported_exit_reason="crash",
    )
    db_session.commit()
    db_session.refresh(row)

    assert row.progress["error_code"] == "client_restart_clean"
    assert row.progress["client_exit_reason"] == "user_closed"


def test_watchdog_and_launcher_reasons_are_treated_as_self_restart(db_session, test_user):
    for reason in ("watchdog_kill", "user_closed", "startup_cleanup", "launcher_exit", "clean"):
        row, now = _previous_process_run(db_session, test_user, f"reason-{reason}")
        scheduled_tasks._fail_previous_client_runs(
            db_session,
            user_id=test_user.id,
            installation_id="online-1",
            client_process_id="new-process",
            now=now,
            reported_exit_reason=reason,
        )
        db_session.commit()
        db_session.refresh(row)
        assert row.progress["error_code"] == "client_restart_clean", reason


def _target_rows():
    rows = [{"target": "甲", "action": "direct_message", "state": "succeeded", "reason": ""}]
    rows += [
        {
            "target": f"离线户{i}",
            "action": "direct_message",
            "action_label": "私信",
            "state": "failed",
            "error_code": "",
            "reason": "抖音账号不在线",
            "at": "2026-09-13 12:00:02",
        }
        for i in range(12)
    ]
    rows += [
        {"target": "乙", "action": "direct_message", "state": "started", "reason": ""},
        {"target": "关注评论", "action": "follow_comment", "state": "not_started", "reason": "前置动作未释放浏览器"},
        {"target": "私信", "action": "direct_message", "state": "not_started", "reason": "前置动作未释放浏览器"},
    ]
    return rows


def test_targets_digest_counts_states_and_ranks_reasons(db_session, test_user):
    row = _run(run_id="digest-run", user_id=test_user.id, task_kind="client_workflow")
    row.status = "completed"
    row.result_payload = {"targets_detail": _target_rows()}
    db_session.add(row)
    db_session.commit()

    digest = scheduled_tasks._run_targets_digest(row)

    assert digest["source"] == "targets"
    assert digest["summary"] == {
        "selected": 16,
        "total": 16,
        "started": 14,
        "succeeded": 1,
        "failed": 12,
        "not_started": 2,
    }
    assert digest["top_reason"] == "抖音账号不在线 ×12"
    assert digest["reasons"][0]["count"] == 12
    # 完整响应保留 5 个样例；compact 响应裁剪到 2 个（见 compact 用例）。
    assert digest["reasons"][0]["sample"] == ["离线户0", "离线户1", "离线户2", "离线户3", "离线户4"]
    assert digest["retry_not_started"] == 2
    assert digest["not_started_targets"] == ["关注评论", "私信"]


def test_targets_digest_falls_back_to_run_error(db_session, test_user):
    row = _run(run_id="digest-fallback", user_id=test_user.id, task_kind="client_workflow")
    row.status = "failed"
    row.error = "客户端异常退出，上一轮任务已中断"
    row.progress = {"error_code": "client_crashed"}
    db_session.add(row)
    db_session.commit()

    digest = scheduled_tasks._run_targets_digest(row)

    assert digest["source"] == "run"
    assert digest["summary"]["failed"] == 1
    assert digest["top_reason"] == "客户端异常退出，上一轮任务已中断 ×1"
    assert digest["reasons"][0]["code"] == "client_crashed"


def test_targets_digest_reads_nested_mcp_result_and_serializes(db_session, test_user):
    row = _run(run_id="digest-nested", user_id=test_user.id, task_kind="client_workflow")
    row.status = "completed"
    row.result_payload = {"mcp_result": {"targets_detail": _target_rows()}}
    db_session.add(row)
    db_session.commit()

    serialized = scheduled_tasks._serialize_run(row)
    assert serialized["targets_digest"]["summary"]["failed"] == 12
    compact = scheduled_tasks._serialize_run_compact(row)
    assert compact["targets_digest"]["summary"]["failed"] == 12


def test_targets_digest_compact_trims_samples_and_targets(db_session, test_user):
    row = _run(run_id="digest-compact", user_id=test_user.id, task_kind="client_workflow")
    row.status = "completed"
    detail = _target_rows()
    detail += [
        {"target": f"未启动{i}", "action": "follow_comment", "state": "not_started", "reason": f"原因{i}"}
        for i in range(30)
    ]
    row.result_payload = {"targets_detail": detail}
    db_session.add(row)
    db_session.commit()

    digest = scheduled_tasks._run_targets_digest(row, compact=True)

    assert len(digest["not_started_targets"]) == 20
    assert digest["retry_not_started"] == 32
    assert all(len(bucket["sample"]) <= 2 for bucket in digest["reasons"])


def test_previous_exit_reason_header_is_normalised():
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/scheduled-tasks/pending",
            "headers": [(b"x-previous-client-exit-reason", b"  Update_Restart  ")],
        }
    )
    assert scheduled_tasks._header_previous_client_exit_reason(request) == "update_restart"

    empty = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/scheduled-tasks/pending",
            "headers": [],
        }
    )
    assert scheduled_tasks._header_previous_client_exit_reason(empty) == ""


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
