from __future__ import annotations

import asyncio
import uuid
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
        id=f"session-{user_id}-{permission_mode}-{uuid.uuid4().hex}",
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


def test_system_task_session_is_listed_but_cannot_be_renamed_or_deleted(
    db_session, db_session_factory, test_user
):
    from backend.app.models import H5ChatMessage
    from backend.app.services.h5_chat_sessions import system_task_session_id

    now = datetime.utcnow()
    db_session.add(
        H5ChatMessage(
            id="legacy-system-task",
            user_id=test_user.id,
            mode="scheduled_task",
            content="历史工作流任务",
            status="completed",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()
    client = _client(db_session_factory, test_user.id)

    sessions = client.get("/api/mastra-chat/sessions")
    expected_id = system_task_session_id(test_user.id)
    system_session = next(row for row in sessions.json()["sessions"] if row["id"] == expected_id)

    assert system_session["title"] == "系统任务"
    assert system_session["system_managed"] is True
    assert client.patch(
        f"/api/mastra-chat/sessions/{expected_id}", json={"title": "改名"}
    ).status_code == 409
    assert client.delete(f"/api/mastra-chat/sessions/{expected_id}").status_code == 409


def test_default_mastra_message_does_not_enter_system_task_session(
    db_session, db_session_factory, test_user
):
    from backend.app.models import H5ChatMessage
    from backend.app.services.h5_chat_sessions import system_task_session_id

    now = datetime.utcnow()
    db_session.add(
        H5ChatMessage(
            id="scheduled-before-chat",
            user_id=test_user.id,
            mode="scheduled_task",
            content="系统任务",
            status="completed",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    response = _client(db_session_factory, test_user.id).post(
        "/api/mastra-chat/messages", json={"content": "新的普通对话"}
    )

    assert response.status_code == 200
    assert response.json()["message"]["session_id"] != system_task_session_id(test_user.id)


def test_mastra_capability_discovery_does_not_require_online_installation(
    db_session_factory, test_user, monkeypatch
):
    from backend.app.api import mastra_chat

    checks = []
    monkeypatch.setattr(
        mastra_chat,
        "_read_capability_catalog_json",
        lambda: {
            "cloud.allowed": {"enabled": True, "description": "cloud"},
            "cloud.denied": {"enabled": True, "description": "locked"},
            "cloud.disabled": {"enabled": False, "description": "disabled"},
        },
    )

    def _can_use(_db, user_id, capability_id, installation_id=None, *, require_installation=True):
        checks.append((user_id, capability_id, installation_id, require_installation))
        return capability_id == "cloud.allowed"

    monkeypatch.setattr(mastra_chat, "user_can_use_capability", _can_use)
    response = _client(db_session_factory, test_user.id).get("/api/mastra-chat/capabilities")

    assert response.status_code == 200, response.text
    assert list(response.json()["capabilities"]) == ["cloud.allowed"]
    assert checks == [
        (test_user.id, "cloud.allowed", None, False),
        (test_user.id, "cloud.denied", None, False),
    ]


def test_server_capability_discovery_skips_only_device_slot(monkeypatch):
    from backend.app.api import skills

    monkeypatch.setattr(skills, "_capability_to_package_map", lambda: {"paid.cloud": "paid-package"})
    monkeypatch.setattr(skills, "installation_slots_enabled", lambda: True)
    monkeypatch.setattr(skills, "_user_unlocked_package_ids", lambda _db, _user_id: set())
    assert not skills.user_can_use_capability(
        None,
        42,
        "paid.cloud",
        require_installation=False,
    )

    monkeypatch.setattr(
        skills,
        "_user_unlocked_package_ids",
        lambda _db, _user_id: {"paid-package"},
    )
    assert not skills.user_can_use_capability(None, 42, "paid.cloud")
    assert skills.user_can_use_capability(
        None,
        42,
        "paid.cloud",
        require_installation=False,
    )


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


def test_attachment_payload_enforces_per_file_limits(db_session, db_session_factory, test_user):
    from backend.app.models import Asset

    too_large = Asset(
        asset_id="oversized-chat-document",
        user_id=test_user.id,
        filename="超大资料.pdf",
        media_type="document",
        file_size=31 * 1024 * 1024,
        source_url="https://cdn.example.test/oversized.pdf",
    )
    db_session.add(too_large)
    db_session.commit()

    response = _client(db_session_factory, test_user.id).post(
        "/api/mastra-chat/messages",
        json={"content": "分析资料", "attachments": [{"asset_id": too_large.asset_id}]},
    )

    assert response.status_code == 400
    assert "素材过大" in response.json()["detail"]


def test_personal_profile_write_reuses_ip_persona_and_requires_authorization(
    db_session, db_session_factory, test_user
):
    from backend.app.models import H5ChatMessage, IPContentScheduleTemplate

    confirm_session = _session(db_session, test_user.id, permission_mode="confirm")
    confirm_parent = H5ChatMessage(
        id="profile-confirm-parent",
        user_id=test_user.id,
        session_id=confirm_session.id,
        mode="mastra",
        content="把客户资料写到人设",
        status="processing",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    full_session = _session(db_session, test_user.id, permission_mode="full")
    full_parent = H5ChatMessage(
        id="profile-full-parent",
        user_id=test_user.id,
        session_id=full_session.id,
        mode="mastra",
        content="更新人设",
        status="processing",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add_all([confirm_parent, full_parent])
    db_session.commit()
    client = _client(db_session_factory, test_user.id)

    blocked = client.patch(
        "/api/mastra-chat/personal-profile",
        json={"parent_message_id": confirm_parent.id, "fields": {"product": "工业相机"}},
    )
    assert blocked.status_code == 409

    saved = client.patch(
        "/api/mastra-chat/personal-profile",
        json={
            "parent_message_id": full_parent.id,
            "fields": {"name": "张总", "product": "工业相机", "target_customer": "制造企业"},
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["profile"]["fields"]["product"] == "工业相机"
    with db_session_factory() as session:
        row = session.query(IPContentScheduleTemplate).filter(
            IPContentScheduleTemplate.user_id == test_user.id,
            IPContentScheduleTemplate.name == "个人默认配置",
        ).one()
        assert row.requirements["basic_profile"]["name"] == "张总"
        assert row.requirements["business_description"]["target_customer"] == "制造企业"


def test_runner_compacts_only_older_turns_in_the_same_session(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatMessage
    from backend.app.services import mastra_chat_runner

    chat_session = _session(db_session, test_user.id, permission_mode="full")
    other_session = _session(db_session, test_user.id, permission_mode="full", title="其他会话")
    base = datetime.utcnow() - timedelta(hours=2)
    rows = []
    for index in range(10):
        created = base + timedelta(minutes=index)
        rows.append(
            H5ChatMessage(
                id=f"summary-history-{index}",
                user_id=test_user.id,
                session_id=chat_session.id,
                mode="mastra",
                content=f"同一会话问题 {index}",
                reply_text=f"同一会话回答 {index}",
                status="completed",
                created_at=created,
                updated_at=created,
                finished_at=created,
            )
        )
    rows.append(
        H5ChatMessage(
            id="summary-other-session",
            user_id=test_user.id,
            session_id=other_session.id,
            mode="mastra",
            content="不能进入摘要的其他会话",
            reply_text="隔离内容",
            status="completed",
            created_at=base,
            updated_at=base,
            finished_at=base,
        )
    )
    current = H5ChatMessage(
        id="summary-current",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="继续当前会话",
        status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add_all([*rows, current])
    db_session.commit()
    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)

    jobs = mastra_chat_runner._claim_jobs_sync(1)

    assert len(jobs) == 1
    assert [item["message_id"] for item in jobs[0].summary_messages] == [
        f"summary-history-{index}" for index in range(5)
    ]
    assert all("其他会话" not in item["user"] for item in jobs[0].summary_messages)
    assert jobs[0].summary_through_message_id == "summary-history-4"


def test_runner_injects_only_recent_mastra_turns_without_old_h5_or_task_noise(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatMessage
    from backend.app.services import mastra_chat_runner

    chat_session = _session(db_session, test_user.id, permission_mode="full")
    other_session = _session(db_session, test_user.id, permission_mode="full", title="其他会话")
    base = datetime.utcnow() - timedelta(minutes=10)
    rows = [
        H5ChatMessage(
            id="context-first",
            user_id=test_user.id,
            session_id=chat_session.id,
            mode="mastra",
            content="先写一条口播稿",
            reply_text="这是已经生成的完整口播稿",
            status="completed",
            created_at=base,
            updated_at=base,
            finished_at=base,
        ),
        H5ChatMessage(
            id="context-scheduled-noise",
            user_id=test_user.id,
            session_id=chat_session.id,
            mode="scheduled_task",
            content="[定时任务] 抖音自动养号",
            reply_text="养号任务完成",
            status="completed",
            created_at=base + timedelta(minutes=1),
            updated_at=base + timedelta(minutes=1),
            finished_at=base + timedelta(minutes=1),
        ),
        H5ChatMessage(
            id="context-legacy-direct",
            user_id=test_user.id,
            session_id=chat_session.id,
            mode="direct",
            content="旧版聊天问题",
            reply_text="旧版聊天回答",
            status="completed",
            created_at=base + timedelta(minutes=2),
            updated_at=base + timedelta(minutes=2),
            finished_at=base + timedelta(minutes=2),
        ),
        H5ChatMessage(
            id="context-other-session",
            user_id=test_user.id,
            session_id=other_session.id,
            mode="mastra",
            content="其他会话问题",
            reply_text="其他会话回答",
            status="completed",
            created_at=base + timedelta(minutes=3),
            updated_at=base + timedelta(minutes=3),
            finished_at=base + timedelta(minutes=3),
        ),
    ]
    current = H5ChatMessage(
        id="context-current",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="用刚才的口播稿制作数字人视频",
        status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add_all([*rows, current])
    db_session.commit()
    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)

    jobs = mastra_chat_runner._claim_jobs_sync(1)

    assert len(jobs) == 1
    assert jobs[0].recent_history == [
        {"role": "user", "content": "先写一条口播稿"},
        {"role": "assistant", "content": "这是已经生成的完整口播稿"},
    ]


def test_runner_does_not_claim_a_second_job_past_per_user_limit(
    db_session, db_session_factory, test_user, other_user, monkeypatch
):
    from backend.app.models import H5ChatMessage
    from backend.app.services import mastra_chat_runner

    now = datetime.utcnow()
    blocked = H5ChatMessage(
        id="per-user-blocked",
        user_id=test_user.id,
        mode="mastra",
        content="同一用户的第二个任务",
        status="pending",
        created_at=now,
        updated_at=now,
    )
    available = H5ChatMessage(
        id="per-user-available",
        user_id=other_user.id,
        mode="mastra",
        content="另一个用户的任务",
        status="pending",
        created_at=now + timedelta(seconds=1),
        updated_at=now + timedelta(seconds=1),
    )
    db_session.add_all([blocked, available])
    db_session.commit()
    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)

    jobs = mastra_chat_runner._claim_jobs_sync(2, {test_user.id: 1}, 1)

    assert [job.message_id for job in jobs] == [available.id]


def test_pending_queue_messages_can_be_edited_deleted_and_are_user_scoped(
    db_session, db_session_factory, test_user, other_user
):
    from backend.app.models import H5ChatMessage

    chat_session = _session(db_session, test_user.id)
    row = H5ChatMessage(
        id="queue-editable",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="原始要求",
        status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()
    owner_client = _client(db_session_factory, test_user.id)
    other_client = _client(db_session_factory, other_user.id)

    assert other_client.patch(f"/api/mastra-chat/messages/{row.id}", json={"content": "越权"}).status_code == 404
    edited = owner_client.patch(f"/api/mastra-chat/messages/{row.id}", json={"content": "修改后的要求"})
    queue = owner_client.get(f"/api/mastra-chat/queue?session_id={chat_session.id}")

    assert edited.status_code == 200, edited.text
    assert edited.json()["message"]["content"] == "修改后的要求"
    assert queue.json()["pending"][0]["queue_position"] == 1
    assert owner_client.delete(f"/api/mastra-chat/messages/{row.id}").status_code == 200
    with db_session_factory() as session:
        assert session.query(H5ChatMessage).filter(H5ChatMessage.id == row.id).first() is None


def test_cancel_processing_message_cancels_pending_online_children(
    db_session, db_session_factory, test_user
):
    from backend.app.models import H5ChatMessage

    chat_session = _session(db_session, test_user.id)
    now = datetime.utcnow()
    parent = H5ChatMessage(
        id="cancel-processing-parent",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="生成视频",
        status="processing",
        created_at=now,
        updated_at=now,
    )
    child = H5ChatMessage(
        id="cancel-pending-child",
        user_id=test_user.id,
        session_id=chat_session.id,
        parent_message_id=parent.id,
        mode="direct",
        content="Online 生成视频",
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([parent, child])
    db_session.commit()

    response = _client(db_session_factory, test_user.id).post(
        f"/api/mastra-chat/messages/{parent.id}/cancel"
    )

    assert response.status_code == 200, response.text
    assert response.json()["message"]["status"] == "cancelled"
    with db_session_factory() as session:
        assert session.query(H5ChatMessage).filter(H5ChatMessage.id == child.id).one().status == "cancelled"


def test_steer_cancels_current_thinking_and_is_claimed_before_normal_queue(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatMessage
    from backend.app.services import mastra_chat_runner

    chat_session = _session(db_session, test_user.id)
    now = datetime.utcnow()
    current = H5ChatMessage(
        id="steer-current",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="做一个产品视频",
        attachments=[{"asset_id": "original-image", "name": "original.jpg"}],
        status="processing",
        created_at=now,
        updated_at=now,
    )
    normal = H5ChatMessage(
        id="steer-normal-queue",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="后续普通任务",
        status="pending",
        created_at=now + timedelta(seconds=1),
        updated_at=now,
    )
    db_session.add_all([current, normal])
    db_session.commit()

    response = _client(db_session_factory, test_user.id).post(
        "/api/mastra-chat/messages",
        json={
            "session_id": chat_session.id,
            "content": "改成美女讲解，时长 15 秒",
            "queue_mode": "steer",
            "target_message_id": current.id,
            "attachments": [],
        },
    )
    assert response.status_code == 200, response.text
    replacement_id = response.json()["message"]["id"]
    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)

    jobs = mastra_chat_runner._claim_jobs_sync(1)

    assert jobs[0].message_id == replacement_id
    assert "原任务：\n做一个产品视频" in jobs[0].content
    assert "补充要求：\n改成美女讲解，时长 15 秒" in jobs[0].content
    assert jobs[0].attachments[0]["asset_id"] == "original-image"
    with db_session_factory() as session:
        assert session.query(H5ChatMessage).filter(H5ChatMessage.id == current.id).one().status == "cancelled"
        assert session.query(H5ChatMessage).filter(H5ChatMessage.id == normal.id).one().status == "pending"


def test_steer_is_rejected_after_side_effect_starts(db_session, db_session_factory, test_user):
    from backend.app.api.h5_chat import _add_event
    from backend.app.models import H5ChatMessage

    chat_session = _session(db_session, test_user.id)
    current = H5ChatMessage(
        id="steer-tool-started",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="发布视频",
        status="processing",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(current)
    db_session.flush()
    _add_event(
        db_session,
        current,
        "tool_start",
        {"name": "修改个人人设", "tool_id": "update_personal_profile"},
    )
    db_session.commit()

    response = _client(db_session_factory, test_user.id).post(
        "/api/mastra-chat/messages",
        json={
            "session_id": chat_session.id,
            "content": "换个平台发布",
            "queue_mode": "steer",
            "target_message_id": current.id,
        },
    )

    assert response.status_code == 409
    assert "加入队列" in response.json()["detail"]


def test_steer_remains_available_after_read_only_tool_call(db_session, db_session_factory, test_user):
    from backend.app.api.h5_chat import _add_event
    from backend.app.models import H5ChatMessage

    chat_session = _session(db_session, test_user.id)
    current = H5ChatMessage(
        id="steer-read-only-tool",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="根据记忆分析产品",
        status="processing",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(current)
    db_session.flush()
    _add_event(
        db_session,
        current,
        "tool_start",
        {"name": "读取个人记忆", "tool_id": "read_personal_memory"},
    )
    db_session.commit()

    response = _client(db_session_factory, test_user.id).post(
        "/api/mastra-chat/messages",
        json={
            "session_id": chat_session.id,
            "content": "重点分析高客单客户",
            "queue_mode": "steer",
            "target_message_id": current.id,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["message"]["queue_mode"] == "steer"


def test_runner_cancellation_watcher_stops_active_request(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatMessage
    from backend.app.services import mastra_chat_runner

    row = H5ChatMessage(
        id="runner-cancel-watch",
        user_id=test_user.id,
        mode="mastra",
        content="长时间思考",
        status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()
    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)
    job = mastra_chat_runner._claim_jobs_sync(1)[0]
    started = asyncio.Event()
    request_cancelled = asyncio.Event()

    async def fake_request(_job):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            request_cancelled.set()
            raise

    monkeypatch.setattr(mastra_chat_runner, "_run_job_request", fake_request)

    async def scenario():
        task = asyncio.create_task(mastra_chat_runner._run_job(job))
        await asyncio.wait_for(started.wait(), timeout=1)
        with db_session_factory() as session:
            saved = session.query(H5ChatMessage).filter(H5ChatMessage.id == row.id).one()
            saved.status = "cancelled"
            session.commit()
        await asyncio.wait_for(task, timeout=2)
        assert request_cancelled.is_set()

    asyncio.run(scenario())


def test_media_task_success_completes_with_saved_asset(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatApproval, H5ChatEvent, H5ChatMessage
    from backend.app.services import mastra_chat_runner

    chat_session = _session(db_session, test_user.id, permission_mode="confirm")
    now = datetime.utcnow()
    parent = H5ChatMessage(
        id="mastra-media-success",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="生成一张 16:9 图片",
        status="processing",
        created_at=now,
        updated_at=now,
    )
    approval = H5ChatApproval(
        id="mastra-media-success-approval",
        user_id=test_user.id,
        session_id=chat_session.id,
        message_id=parent.id,
        task="生成一张 16:9 图片",
        execution_target="server",
        status="executing",
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([parent, approval])
    db_session.commit()
    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)

    media_task = {
        "capability_id": "image.generate",
        "task_id": "image-task-1",
        "canonical_input": {
            "capability_id": "image.generate",
            "payload": {"image_size": "16:9", "resolution": "4K"},
        },
        "status": "completed",
        "terminal": True,
        "success": True,
        "saved_assets": [
            {"asset_id": "asset-1", "url": "https://cdn.test/result.jpg", "media_type": "image"}
        ],
    }
    mastra_chat_runner._complete_sync(parent.id, "处理完成。", [], None, [media_task], [])

    with db_session_factory() as session:
        saved = session.query(H5ChatMessage).filter(H5ChatMessage.id == parent.id).one()
        saved_approval = session.query(H5ChatApproval).filter(H5ChatApproval.id == approval.id).one()
        final_event = (
            session.query(H5ChatEvent)
            .filter(H5ChatEvent.message_id == parent.id, H5ChatEvent.event_type == "final")
            .one()
        )
        assert saved.status == "completed"
        assert saved.reply_text == "图片已生成。"
        assert saved.error is None
        assert saved_approval.status == "completed"
        assert final_event.payload["saved_assets"][0]["url"] == "https://cdn.test/result.jpg"
        assert final_event.payload["media_tasks"][0]["task_id"] == "image-task-1"


def test_media_task_failure_fails_message_and_approval(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatApproval, H5ChatEvent, H5ChatMessage
    from backend.app.services import mastra_chat_runner

    chat_session = _session(db_session, test_user.id, permission_mode="confirm")
    now = datetime.utcnow()
    parent = H5ChatMessage(
        id="mastra-media-failure",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="生成图片",
        status="processing",
        created_at=now,
        updated_at=now,
    )
    approval = H5ChatApproval(
        id="mastra-media-failure-approval",
        user_id=test_user.id,
        session_id=chat_session.id,
        message_id=parent.id,
        task="生成图片",
        execution_target="server",
        status="executing",
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([parent, approval])
    db_session.commit()
    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)

    mastra_chat_runner._complete_sync(
        parent.id,
        "处理完成。",
        [],
        None,
        [
            {
                "capability_id": "image.generate",
                "task_id": "image-task-failed",
                "status": "failed",
                "terminal": True,
                "success": False,
                "error": "上游余额不足",
            }
        ],
        [],
    )

    with db_session_factory() as session:
        saved = session.query(H5ChatMessage).filter(H5ChatMessage.id == parent.id).one()
        saved_approval = session.query(H5ChatApproval).filter(H5ChatApproval.id == approval.id).one()
        error_event = (
            session.query(H5ChatEvent)
            .filter(H5ChatEvent.message_id == parent.id, H5ChatEvent.event_type == "error")
            .one()
        )
        assert saved.status == "failed"
        assert saved.error == "上游余额不足"
        assert saved_approval.status == "failed"
        assert error_event.payload["error"] == "上游余额不足"


def test_media_task_cannot_complete_without_output_url(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatMessage
    from backend.app.services import mastra_chat_runner

    parent = H5ChatMessage(
        id="mastra-media-no-output",
        user_id=test_user.id,
        mode="mastra",
        content="生成图片",
        status="processing",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(parent)
    db_session.commit()
    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)

    mastra_chat_runner._complete_sync(
        parent.id,
        "处理完成。",
        [],
        None,
        [
            {
                "capability_id": "image.generate",
                "task_id": "image-without-output",
                "status": "completed",
                "terminal": True,
                "success": True,
                "saved_assets": [],
            }
        ],
        [],
    )

    with db_session_factory() as session:
        saved = session.query(H5ChatMessage).filter(H5ChatMessage.id == parent.id).one()
        assert saved.status == "failed"
        assert saved.error == "图片任务已结束，但没有返回可用的成品地址"


def test_media_task_progress_is_persisted_for_restart_resume(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatApproval, H5ChatMessage
    from backend.app.services import mastra_chat_runner

    chat_session = _session(db_session, test_user.id, permission_mode="confirm")
    old = datetime.utcnow() - timedelta(hours=1)
    parent = H5ChatMessage(
        id="mastra-media-resume",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="生成图片",
        status="processing",
        claimed_by_installation_id="mastra-server",
        claimed_at=old,
        created_at=old,
        updated_at=old,
    )
    approval = H5ChatApproval(
        id="mastra-media-resume-approval",
        user_id=test_user.id,
        session_id=chat_session.id,
        message_id=parent.id,
        task="生成图片",
        execution_target="server",
        status="executing",
        created_at=old,
        updated_at=old,
        decided_at=old,
    )
    db_session.add_all([parent, approval])
    db_session.commit()
    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)

    task = {
        "capability_id": "image.generate",
        "task_id": "resume-task-1",
        "canonical_input": {
            "capability_id": "image.generate",
            "payload": {"image_size": "16:9", "resolution": "4K"},
        },
        "status": "processing",
        "terminal": False,
        "success": None,
        "saved_assets": [],
        "poll_count": 2,
    }
    mastra_chat_runner._append_event_sync(
        parent.id,
        "progress",
        {"text": "图片仍在生成", "media_task": task},
    )
    with db_session_factory() as session:
        saved_parent = session.query(H5ChatMessage).filter(H5ChatMessage.id == parent.id).one()
        saved_parent.updated_at = old
        session.commit()

    monkeypatch.setattr(mastra_chat_runner, "_stale_after_seconds", lambda: 300)
    assert mastra_chat_runner._recover_stale_sync() == 1
    jobs = mastra_chat_runner._claim_jobs_sync(1)

    assert jobs[0].approval_granted is True
    assert jobs[0].existing_media_tasks[0]["task_id"] == "resume-task-1"
    assert jobs[0].existing_media_tasks[0]["canonical_input"]["payload"]["image_size"] == "16:9"


def test_media_task_transport_failure_requeues_same_task_id(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatApproval, H5ChatEvent, H5ChatMessage
    from backend.app.services import mastra_chat_runner

    chat_session = _session(db_session, test_user.id, permission_mode="confirm")
    now = datetime.utcnow()
    parent = H5ChatMessage(
        id="mastra-media-transport-retry",
        user_id=test_user.id,
        session_id=chat_session.id,
        mode="mastra",
        content="生成图片",
        status="processing",
        claimed_by_installation_id="mastra-server",
        claimed_at=now,
        created_at=now,
        updated_at=now,
    )
    approval = H5ChatApproval(
        id="mastra-media-transport-approval",
        user_id=test_user.id,
        session_id=chat_session.id,
        message_id=parent.id,
        task="生成图片",
        execution_target="server",
        status="executing",
        created_at=now,
        updated_at=now,
        decided_at=now,
    )
    db_session.add_all([parent, approval])
    db_session.commit()
    monkeypatch.setattr(mastra_chat_runner, "SessionLocal", db_session_factory)

    result = mastra_chat_runner._requeue_media_sync(
        parent.id,
        [
            {
                "capability_id": "image.generate",
                "task_id": "same-task-after-restart",
                "status": "processing",
                "terminal": False,
                "success": None,
            }
        ],
        "Mastra connection reset",
    )

    assert result == "requeued_media"
    with db_session_factory() as session:
        saved = session.query(H5ChatMessage).filter(H5ChatMessage.id == parent.id).one()
        saved_approval = session.query(H5ChatApproval).filter(H5ChatApproval.id == approval.id).one()
        queued = (
            session.query(H5ChatEvent)
            .filter(H5ChatEvent.message_id == parent.id, H5ChatEvent.event_type == "queued")
            .one()
        )
        assert saved.mode == "mastra"
        assert saved.status == "pending"
        assert saved.claimed_by_installation_id is None
        assert saved_approval.status == "approved"
        assert queued.payload["media_tasks"][0]["task_id"] == "same-task-after-restart"


def test_mastra_media_generation_is_guarded_and_polled_to_terminal():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "mastra_server" / "src" / "mastra" / "index.ts"
    ).read_text(encoding="utf-8")

    assert "const existing = tasks.get(capabilityId)" in source
    assert "capability_id: 'task.get_result'" in source
    assert "if (!task.terminal) await pollExisting(task, context)" in source
    assert "mediaExecution.resumeExisting" in source
    assert "media_tasks: mediaExecution.snapshots()" in source
