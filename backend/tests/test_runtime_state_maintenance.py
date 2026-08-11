from __future__ import annotations

from datetime import datetime, timedelta


def test_runtime_state_maintenance_repairs_durable_state(
    db_session,
    db_session_factory,
    test_user,
    monkeypatch,
):
    from backend.app.models import CreativeGenerationJob, H5ChatApproval, H5ChatMessage
    from backend.app.services import runtime_state_maintenance

    now = datetime.utcnow()
    old = now - timedelta(days=8)
    recent = now - timedelta(minutes=10)
    completed = H5ChatMessage(
        id="maintenance-completed-message",
        user_id=test_user.id,
        mode="mastra",
        content="已完成",
        status="completed",
        created_at=old,
        updated_at=old,
        finished_at=old,
    )
    approval = H5ChatApproval(
        id="maintenance-executing-approval",
        user_id=test_user.id,
        session_id="maintenance-session",
        message_id=completed.id,
        task="保存资料",
        status="executing",
        created_at=old,
        updated_at=old,
    )
    expired_chat = H5ChatMessage(
        id="maintenance-expired-chat",
        user_id=test_user.id,
        mode="direct",
        content="很久以前的请求",
        status="pending",
        created_at=old,
        updated_at=old,
    )
    active_chat = H5ChatMessage(
        id="maintenance-active-chat",
        user_id=test_user.id,
        mode="direct",
        content="刚提交的请求",
        status="pending",
        created_at=recent,
        updated_at=recent,
    )
    stale_without_provider = CreativeGenerationJob(
        job_id="maintenance-no-provider",
        user_id=test_user.id,
        feature_type="image_studio",
        status="running",
        stage="queued",
        created_at=old,
        updated_at=old,
    )
    stale_with_provider = CreativeGenerationJob(
        job_id="maintenance-old-provider",
        user_id=test_user.id,
        feature_type="seedance_tvc",
        provider_task_id="provider-old",
        status="running",
        stage="generating",
        created_at=old,
        updated_at=old,
    )
    active_job = CreativeGenerationJob(
        job_id="maintenance-active-provider",
        user_id=test_user.id,
        feature_type="seedance_tvc",
        provider_task_id="provider-active",
        status="running",
        stage="generating",
        created_at=recent,
        updated_at=recent,
    )
    db_session.add_all(
        [
            completed,
            approval,
            expired_chat,
            active_chat,
            stale_without_provider,
            stale_with_provider,
            active_job,
        ]
    )
    db_session.commit()
    monkeypatch.setattr(runtime_state_maintenance, "SessionLocal", db_session_factory)

    result = runtime_state_maintenance.cleanup_runtime_state_sync(now)

    assert result["approvals_repaired"] == 1
    assert result["chat_messages_expired"] == 1
    assert result["creative_jobs_expired"] == 2
    with db_session_factory() as db:
        assert db.get(H5ChatApproval, approval.id).status == "completed"
        assert db.get(H5ChatMessage, expired_chat.id).status == "cancelled"
        assert db.get(H5ChatMessage, active_chat.id).status == "pending"
        assert db.get(CreativeGenerationJob, stale_without_provider.id).status == "failed"
        assert db.get(CreativeGenerationJob, stale_with_provider.id).status == "failed"
        assert db.get(CreativeGenerationJob, active_job.id).status == "running"
