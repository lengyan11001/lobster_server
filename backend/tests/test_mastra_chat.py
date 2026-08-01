from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(db_session_factory, user_id: int) -> TestClient:
    from backend.app.api.auth import get_current_user
    from backend.app.api.h5_chat import router as h5_chat_router
    from backend.app.api.mastra_chat import router
    from backend.app.db import get_db
    from backend.app.models import User

    app = FastAPI()
    app.include_router(h5_chat_router)
    app.include_router(router)

    def _get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    def _get_current_user_override():
        session = db_session_factory()
        try:
            return session.query(User).filter(User.id == user_id).first()
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _get_current_user_override
    return TestClient(app)


def _session(db, user_id: int, *, permission_mode: str = "confirm", title: str = "测试会话"):
    from backend.app.models import H5ChatSession

    row = H5ChatSession(
        id=f"session-{user_id}-{permission_mode}-{datetime.utcnow().timestamp()}",
        user_id=user_id,
        title=title,
        permission_mode=permission_mode,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def test_create_mastra_message_uses_server_orchestrator_mode(db_session_factory, test_user):
    from backend.app.models import H5ChatEvent, H5ChatMessage

    response = _client(db_session_factory, test_user.id).post(
        "/api/mastra-chat/messages",
        json={"content": "帮我生成一条产品文案", "installation_id": "desktop-a"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()["message"]
    assert payload["mode"] == "mastra"
    assert payload["status"] == "pending"
    assert payload["installation_id"] == "desktop-a"
    with db_session_factory() as session:
        row = session.query(H5ChatMessage).filter(H5ChatMessage.id == payload["id"]).one()
        event = session.query(H5ChatEvent).filter(H5ChatEvent.message_id == row.id).one()
        assert row.parent_message_id is None
        assert event.event_type == "queued"


def test_dispatch_online_task_is_linked_and_deduplicated(db_session, db_session_factory, test_user):
    from backend.app.models import H5ChatDevicePresence, H5ChatMessage

    chat_session = _session(db_session, test_user.id, permission_mode="full")
    parent = H5ChatMessage(
        id="parent-mastra-message",
        user_id=test_user.id,
        session_id=chat_session.id,
        installation_id="desktop-a",
        mode="mastra",
        content="发布到朋友圈",
        status="processing",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    presence = H5ChatDevicePresence(
        user_id=test_user.id,
        installation_id="desktop-a",
        display_name="测试电脑",
        last_seen_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
    )
    db_session.add_all([parent, presence])
    db_session.commit()

    client = _client(db_session_factory, test_user.id)
    body = {
        "task": "使用当前账号发布到朋友圈",
        "reason": "需要本机微信登录态",
        "parent_message_id": parent.id,
        "installation_id": "desktop-a",
    }
    first = client.post("/api/mastra-chat/online-dispatch", json=body)
    second = client.post("/api/mastra-chat/online-dispatch", json=body)

    assert first.status_code == 200, first.text
    assert first.json()["online"] is True
    assert second.status_code == 200, second.text
    assert second.json()["deduplicated"] is True
    assert second.json()["message"]["id"] == first.json()["message"]["id"]
    with db_session_factory() as session:
        children = session.query(H5ChatMessage).filter(H5ChatMessage.parent_message_id == parent.id).all()
        assert len(children) == 1
        assert children[0].mode == "direct"


def test_runner_falls_back_to_online_without_holding_failed_parent(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatMessage
    from backend.app.services import mastra_chat_runner

    parent = H5ChatMessage(
        id="fallback-mastra-message",
        user_id=test_user.id,
        mode="mastra",
        content="打开本机微信",
        status="processing",
        claimed_by_installation_id="mastra-server",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(parent)
    db_session.commit()
    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)

    result = mastra_chat_runner._fallback_or_fail_sync(parent.id, "connection refused")

    assert result == "fallback_online"
    with db_session_factory() as session:
        saved = session.query(H5ChatMessage).filter(H5ChatMessage.id == parent.id).one()
        assert saved.mode == "direct"
        assert saved.status == "pending"
        assert saved.claimed_by_installation_id is None


def test_parent_waits_for_orchestrator_reply_before_merging_online_result(
    db_session, db_session_factory, test_user
):
    from backend.app.api.h5_chat import _finish_mastra_parent_from_children
    from backend.app.models import H5ChatEvent, H5ChatMessage

    now = datetime.utcnow()
    parent = H5ChatMessage(
        id="race-parent-message",
        user_id=test_user.id,
        mode="mastra",
        content="发布内容",
        status="processing",
        created_at=now,
        updated_at=now,
    )
    child = H5ChatMessage(
        id="race-child-message",
        user_id=test_user.id,
        parent_message_id=parent.id,
        mode="direct",
        content="执行发布",
        status="completed",
        reply_text="发布成功",
        created_at=now,
        updated_at=now,
        finished_at=now,
    )
    db_session.add_all([parent, child])
    db_session.commit()

    _finish_mastra_parent_from_children(db_session, child)
    assert parent.status == "processing"
    assert not db_session.query(H5ChatEvent).filter(
        H5ChatEvent.message_id == parent.id,
        H5ChatEvent.event_type == "final",
    ).count()

    parent.reply_text = "已安排发布。"
    _finish_mastra_parent_from_children(db_session, child)
    db_session.commit()
    assert parent.status == "completed"
    assert "发布成功" in parent.reply_text


def test_sessions_keep_history_and_permissions_isolated(db_session_factory, test_user):
    client = _client(db_session_factory, test_user.id)
    first = client.post(
        "/api/mastra-chat/sessions",
        json={"title": "产品策划", "permission_mode": "confirm"},
    ).json()["session"]
    second = client.post(
        "/api/mastra-chat/sessions",
        json={"title": "发布执行", "permission_mode": "full"},
    ).json()["session"]

    first_message = client.post(
        "/api/mastra-chat/messages",
        json={"session_id": first["id"], "content": "只属于产品策划"},
    )
    second_message = client.post(
        "/api/mastra-chat/messages",
        json={"session_id": second["id"], "content": "只属于发布执行"},
    )
    assert first_message.status_code == 200, first_message.text
    assert second_message.status_code == 200, second_message.text

    first_history = client.get(f'/api/h5-chat/messages?session_id={first["id"]}').json()["messages"]
    second_history = client.get(f'/api/h5-chat/messages?session_id={second["id"]}').json()["messages"]
    assert [item["message"]["content"] for item in first_history] == ["只属于产品策划"]
    assert [item["message"]["content"] for item in second_history] == ["只属于发布执行"]

    sessions = client.get("/api/mastra-chat/sessions").json()["sessions"]
    by_id = {row["id"]: row for row in sessions}
    assert by_id[first["id"]]["permission_mode"] == "confirm"
    assert by_id[second["id"]]["permission_mode"] == "full"
    assert by_id[first["id"]]["message_count"] == 1
    assert by_id[second["id"]]["message_count"] == 1


def test_attachment_only_message_validates_owner_and_serializes(
    db_session, db_session_factory, test_user, other_user
):
    from backend.app.models import Asset

    owned = Asset(
        asset_id="owned-chat-image",
        user_id=test_user.id,
        filename="产品图.png",
        media_type="image",
        file_size=321,
        source_url="https://cdn.example.test/owned-chat-image.png",
    )
    foreign = Asset(
        asset_id="foreign-chat-image",
        user_id=other_user.id,
        filename="其他用户.png",
        media_type="image",
        source_url="https://cdn.example.test/foreign-chat-image.png",
    )
    db_session.add_all([owned, foreign])
    db_session.commit()

    client = _client(db_session_factory, test_user.id)
    chat_session = client.post("/api/mastra-chat/sessions", json={"title": "看图"}).json()["session"]
    response = client.post(
        "/api/mastra-chat/messages",
        json={
            "session_id": chat_session["id"],
            "content": "",
            "attachments": [{"asset_id": owned.asset_id, "media_type": "file"}],
        },
    )
    assert response.status_code == 200, response.text
    message = response.json()["message"]
    assert message["content"] == ""
    assert message["attachments"] == [
        {
            "asset_id": owned.asset_id,
            "url": owned.source_url,
            "name": owned.filename,
            "media_type": "image",
            "content_type": "",
            "size": 321,
        }
    ]

    rejected = client.post(
        "/api/mastra-chat/messages",
        json={
            "session_id": chat_session["id"],
            "content": "分析这个素材",
            "attachments": [{"asset_id": foreign.asset_id}],
        },
    )
    assert rejected.status_code == 400
    assert "不属于当前账号" in rejected.json()["detail"]


def test_confirm_mode_blocks_dispatch_until_approved_run_is_claimed(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatApproval, H5ChatMessage
    from backend.app.services import mastra_chat_runner

    chat_session = _session(db_session, test_user.id, permission_mode="confirm")
    parent = H5ChatMessage(
        id="confirm-dispatch-parent",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="发布到朋友圈",
        status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(parent)
    db_session.commit()
    client = _client(db_session_factory, test_user.id)
    dispatch_body = {
        "task": "发布当前视频到朋友圈",
        "reason": "需要本机微信",
        "parent_message_id": parent.id,
    }

    blocked = client.post("/api/mastra-chat/online-dispatch", json=dispatch_body)
    assert blocked.status_code == 409

    requested = client.post(
        "/api/mastra-chat/approval-request",
        json={**dispatch_body, "execution_target": "online"},
    )
    assert requested.status_code == 200, requested.text
    approval_id = requested.json()["approval"]["id"]
    approved = client.post(
        f"/api/mastra-chat/approvals/{approval_id}/decision",
        json={"decision": "approve"},
    )
    assert approved.status_code == 200, approved.text

    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)
    jobs = mastra_chat_runner._claim_jobs_sync(1)
    assert jobs and jobs[0].approval_granted is True
    assert jobs[0].approval_id == approval_id
    with db_session_factory() as session:
        approval = session.query(H5ChatApproval).filter(H5ChatApproval.id == approval_id).one()
        assert approval.status == "executing"

    dispatched = client.post(
        "/api/mastra-chat/online-dispatch",
        json={**dispatch_body, "approval_id": approval_id},
    )
    assert dispatched.status_code == 200, dispatched.text
    assert dispatched.json()["message"]["parent_message_id"] == parent.id


def test_full_authorization_bypasses_confirmation(db_session, db_session_factory, test_user):
    from backend.app.models import H5ChatApproval, H5ChatMessage

    chat_session = _session(db_session, test_user.id, permission_mode="full")
    parent = H5ChatMessage(
        id="full-auth-parent",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="执行云端任务",
        status="processing",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(parent)
    db_session.commit()
    client = _client(db_session_factory, test_user.id)

    response = client.post(
        "/api/mastra-chat/approval-request",
        json={
            "parent_message_id": parent.id,
            "task": "执行云端任务",
            "reason": "用户已完全授权",
            "execution_target": "server",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["approved"] is True
    with db_session_factory() as session:
        assert session.query(H5ChatApproval).filter(H5ChatApproval.message_id == parent.id).count() == 0


def test_approval_during_planning_requeues_once_without_duplicate(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatApproval, H5ChatMessage
    from backend.app.services import mastra_chat_runner

    chat_session = _session(db_session, test_user.id, permission_mode="confirm")
    parent = H5ChatMessage(
        id="approval-planning-race",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="生成并发布一张海报",
        status="processing",
        claimed_by_installation_id="mastra-server",
        claimed_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(parent)
    db_session.commit()
    client = _client(db_session_factory, test_user.id)
    request_body = {
        "parent_message_id": parent.id,
        "task": "生成并发布海报",
        "reason": "会生成素材并产生费用",
        "execution_target": "auto",
    }
    approval_id = client.post("/api/mastra-chat/approval-request", json=request_body).json()["approval"]["id"]
    decision = client.post(
        f"/api/mastra-chat/approvals/{approval_id}/decision",
        json={"decision": "approve"},
    )
    assert decision.status_code == 200, decision.text

    duplicate = client.post("/api/mastra-chat/approval-request", json=request_body)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["approval"]["id"] == approval_id
    assert duplicate.json()["approval"]["status"] == "approved"

    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)
    mastra_chat_runner._complete_sync(parent.id, "执行方案已准备", [], None)
    with db_session_factory() as session:
        saved = session.query(H5ChatMessage).filter(H5ChatMessage.id == parent.id).one()
        assert saved.status == "pending"
        assert saved.claimed_by_installation_id is None
        assert session.query(H5ChatApproval).filter(H5ChatApproval.message_id == parent.id).count() == 1


def test_rejected_approval_cannot_be_overwritten_by_late_runner(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatMessage
    from backend.app.services import mastra_chat_runner

    chat_session = _session(db_session, test_user.id, permission_mode="confirm")
    parent = H5ChatMessage(
        id="rejected-late-runner",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="发布内容",
        status="processing",
        claimed_by_installation_id="mastra-server",
        claimed_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(parent)
    db_session.commit()
    client = _client(db_session_factory, test_user.id)
    approval_id = client.post(
        "/api/mastra-chat/approval-request",
        json={
            "parent_message_id": parent.id,
            "task": "发布内容",
            "reason": "外部发布",
            "execution_target": "online",
        },
    ).json()["approval"]["id"]
    client.post(
        f"/api/mastra-chat/approvals/{approval_id}/decision",
        json={"decision": "reject"},
    )

    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)
    mastra_chat_runner._complete_sync(parent.id, "这条迟到的结果不能覆盖取消状态", [], None)
    with db_session_factory() as session:
        saved = session.query(H5ChatMessage).filter(H5ChatMessage.id == parent.id).one()
        assert saved.status == "completed"
        assert "取消执行" in saved.reply_text
        assert "迟到的结果" not in saved.reply_text


def test_restart_restores_executing_approval_for_retry(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatApproval, H5ChatMessage
    from backend.app.services import mastra_chat_runner

    chat_session = _session(db_session, test_user.id, permission_mode="confirm")
    old = datetime.utcnow() - timedelta(hours=1)
    parent = H5ChatMessage(
        id="stale-approved-run",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="继续执行",
        status="processing",
        claimed_by_installation_id="mastra-server",
        claimed_at=old,
        created_at=old,
        updated_at=old,
    )
    approval = H5ChatApproval(
        id="stale-executing-approval",
        user_id=test_user.id,
        session_id=chat_session.id,
        message_id=parent.id,
        task="继续执行",
        execution_target="auto",
        status="executing",
        created_at=old,
        updated_at=old,
        decided_at=old,
    )
    db_session.add_all([parent, approval])
    db_session.commit()
    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)
    monkeypatch.setattr(mastra_chat_runner, "_stale_after_seconds", lambda: 300)

    assert mastra_chat_runner._recover_stale_sync() == 1
    with db_session_factory() as session:
        saved_parent = session.query(H5ChatMessage).filter(H5ChatMessage.id == parent.id).one()
        saved_approval = session.query(H5ChatApproval).filter(H5ChatApproval.id == approval.id).one()
        assert saved_parent.status == "pending"
        assert saved_parent.claimed_by_installation_id is None
        assert saved_approval.status == "approved"


def test_online_pending_still_claims_legacy_client_command(db_session, test_user):
    from starlette.requests import Request

    from backend.app.api.h5_chat import h5_pending_messages
    from backend.app.models import H5ChatMessage

    installation_id = "legacy-command-device"
    row = H5ChatMessage(
        id="legacy-client-command",
        user_id=test_user.id,
        installation_id=installation_id,
        mode="client_command",
        content="执行旧版客户端指令",
        status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/h5-chat/pending",
            "query_string": b"",
            "headers": [(b"x-installation-id", installation_id.encode("ascii"))],
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )

    result = h5_pending_messages(
        request=request,
        limit=2,
        current_user_id=test_user.id,
        db=db_session,
    )
    assert [item["id"] for item in result["items"]] == [row.id]
    assert result["items"][0]["mode"] == "client_command"
