from __future__ import annotations

import uuid
from datetime import datetime

from backend.app.models import (
    H5ChatDevicePresence,
    H5ChatEvent,
    H5ChatMessage,
    H5WorkflowActivation,
    InstallationSlotOwner,
    ScheduledTask,
    ScheduledTaskRun,
)
from backend.app.services.installation_slot_ownership import (
    assert_installation_slot_owner,
    claim_installation_slot,
)


def test_stopping_workflow_activation_cancels_processing_takeover_run(
    db_session,
    db_session_factory,
    test_user,
    patch_fuiou_settings,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api.auth import get_current_user
    from backend.app.api.h5_workflows import router as h5_workflows_router
    from backend.app.db import get_db

    now = datetime.utcnow()
    installation_id = "workflow-stop-device"
    message_id = uuid.uuid4().hex
    run_id = uuid.uuid4().hex
    task = ScheduledTask(
        user_id=test_user.id,
        title="wechat takeover",
        task_kind="client_workflow",
        content="run",
        payload={"action": "native_wechat_poll"},
        schedule_type="interval",
        interval_seconds=1800,
        target_installation_ids=[installation_id],
        status="active",
        next_run_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    db_session.flush()
    activation = H5WorkflowActivation(
        user_id=test_user.id,
        installation_id=installation_id,
        template_id=1,
        template_owner_user_id=test_user.id,
        status="active",
        scheduled_task_ids=[task.id],
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add_all(
        [
            activation,
            H5ChatMessage(
                id=message_id,
                user_id=test_user.id,
                installation_id=installation_id,
                claimed_by_installation_id=installation_id,
                mode="scheduled_task",
                content="wechat takeover",
                status="processing",
                created_at=now,
                updated_at=now,
                claimed_at=now,
            ),
            ScheduledTaskRun(
                id=run_id,
                task_id=task.id,
                user_id=test_user.id,
                installation_id=installation_id,
                claimed_by_installation_id=installation_id,
                title="wechat takeover",
                task_kind="client_workflow",
                content="run",
                payload={"action": "native_wechat_poll"},
                status="processing",
                h5_message_id=message_id,
                created_at=now,
                updated_at=now,
                claimed_at=now,
                started_at=now,
            ),
        ]
    )
    db_session.commit()
    activation_id = activation.id

    app = FastAPI()
    app.include_router(h5_workflows_router, prefix="")

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

    response = client.post(f"/api/h5-workflows/activations/{activation_id}/stop")

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(H5WorkflowActivation, activation_id).status == "stopped"
    assert db_session.get(ScheduledTask, task.id).status == "paused"
    assert db_session.get(ScheduledTaskRun, run_id).status == "cancelled"
    assert db_session.get(H5ChatMessage, message_id).status == "cancelled"
    event = db_session.query(H5ChatEvent).filter_by(message_id=message_id, event_type="cancelled").one()
    assert event.payload["reason"] == "workflow_stopped"


def test_same_raw_slot_across_accounts_does_not_cancel_previous_account_work(
    db_session,
    test_user,
    other_user,
):
    now = datetime.utcnow()
    installation_id = "same-machine-slot-001"
    message_id = uuid.uuid4().hex
    direct_message_id = uuid.uuid4().hex
    run_id = uuid.uuid4().hex

    task = ScheduledTask(
        user_id=test_user.id,
        title="old account workflow",
        task_kind="client_workflow",
        content="run",
        payload={"action": "native_wechat_poll"},
        schedule_type="interval",
        interval_seconds=1800,
        target_installation_ids=[installation_id],
        status="active",
        next_run_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    db_session.flush()
    db_session.add_all(
        [
            H5WorkflowActivation(
                user_id=test_user.id,
                installation_id=installation_id,
                template_id=1,
                template_owner_user_id=test_user.id,
                status="active",
                scheduled_task_ids=[task.id],
                started_at=now,
                created_at=now,
                updated_at=now,
            ),
            H5ChatDevicePresence(
                user_id=test_user.id,
                installation_id=installation_id,
                display_name="old login",
                last_seen_at=now,
                created_at=now,
            ),
            H5ChatMessage(
                id=message_id,
                user_id=test_user.id,
                installation_id=installation_id,
                claimed_by_installation_id=installation_id,
                mode="scheduled_task",
                content="wechat takeover",
                status="processing",
                created_at=now,
                updated_at=now,
                claimed_at=now,
            ),
            H5ChatMessage(
                id=direct_message_id,
                user_id=test_user.id,
                installation_id=installation_id,
                mode="client_command",
                content="command",
                status="pending",
                created_at=now,
                updated_at=now,
            ),
            ScheduledTaskRun(
                id=run_id,
                task_id=task.id,
                user_id=test_user.id,
                installation_id=installation_id,
                claimed_by_installation_id=installation_id,
                title="wechat takeover",
                task_kind="client_workflow",
                content="run",
                payload={"action": "native_wechat_poll"},
                status="processing",
                h5_message_id=message_id,
                created_at=now,
                updated_at=now,
                claimed_at=now,
                started_at=now,
            ),
        ]
    )
    db_session.commit()

    first = claim_installation_slot(
        db_session,
        user_id=test_user.id,
        installation_id=installation_id,
        brand_mark="bihuo",
    )
    assert first["transferred"] is False

    second = claim_installation_slot(
        db_session,
        user_id=other_user.id,
        installation_id=installation_id,
        brand_mark="jinghai",
    )

    assert second["transferred"] is False
    assert second["previous_user_ids"] == []
    assert db_session.query(InstallationSlotOwner).filter_by(user_id=test_user.id).count() == 1
    other_owner = db_session.query(InstallationSlotOwner).filter_by(user_id=other_user.id).one()
    assert other_owner.brand_mark == "jinghai"
    assert other_owner.lease_version == 1
    assert db_session.query(H5WorkflowActivation).one().status == "active"
    assert db_session.query(ScheduledTask).filter_by(id=task.id).one().status == "active"
    assert db_session.query(ScheduledTaskRun).filter_by(id=run_id).one().status == "processing"
    assert db_session.query(H5ChatMessage).filter_by(id=message_id).one().status == "processing"
    assert db_session.query(H5ChatMessage).filter_by(id=direct_message_id).one().status == "pending"
    assert db_session.query(H5ChatDevicePresence).filter_by(user_id=test_user.id).count() == 1
    assert db_session.query(H5ChatEvent).filter_by(event_type="cancelled").count() == 0

    assert_installation_slot_owner(
        db_session,
        user_id=test_user.id,
        installation_id=installation_id,
    )
    assert_installation_slot_owner(
        db_session,
        user_id=other_user.id,
        installation_id=installation_id,
    )


def test_same_account_relogin_does_not_cancel_its_pending_work(db_session, test_user):
    now = datetime.utcnow()
    installation_id = "same-account-slot-001"
    claim_installation_slot(
        db_session,
        user_id=test_user.id,
        installation_id=installation_id,
        brand_mark="bihuo",
    )
    message = H5ChatMessage(
        id=uuid.uuid4().hex,
        user_id=test_user.id,
        installation_id=installation_id,
        mode="client_command",
        content="keep running",
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db_session.add(message)
    db_session.commit()

    result = claim_installation_slot(
        db_session,
        user_id=test_user.id,
        installation_id=installation_id,
        brand_mark="bihuo",
    )

    assert result["transferred"] is False
    db_session.refresh(message)
    assert message.status == "pending"
    owner = db_session.query(InstallationSlotOwner).filter_by(user_id=test_user.id).one()
    assert owner.lease_version == 1


def test_same_account_new_session_does_not_cancel_its_pending_work(db_session, test_user):
    now = datetime.utcnow()
    installation_id = "same-user-new-source-001"
    claim_installation_slot(
        db_session,
        user_id=test_user.id,
        installation_id=installation_id,
        brand_mark="bihuo",
        auth_session_id="login-session-a",
    )
    message = H5ChatMessage(
        id=uuid.uuid4().hex,
        user_id=test_user.id,
        installation_id=installation_id,
        mode="client_command",
        content="old source command",
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db_session.add(message)
    db_session.commit()

    result = claim_installation_slot(
        db_session,
        user_id=test_user.id,
        installation_id=installation_id,
        brand_mark="bihuo",
        auth_session_id="login-session-b",
    )

    assert result["transferred"] is False
    assert result["previous_user_ids"] == []
    db_session.refresh(message)
    assert message.status == "pending"
    owner = db_session.query(InstallationSlotOwner).filter_by(user_id=test_user.id).one()
    assert owner.auth_session_id == "login-session-b"
    assert owner.lease_version == 1
    assert_installation_slot_owner(
        db_session,
        user_id=test_user.id,
        installation_id=installation_id,
        auth_session_id="login-session-a",
    )
    assert_installation_slot_owner(
        db_session,
        user_id=test_user.id,
        installation_id=installation_id,
        auth_session_id="login-session-b",
    )


def test_same_raw_slot_heartbeats_are_scoped_per_account(
    db_session,
    db_session_factory,
    test_user,
    other_user,
    patch_fuiou_settings,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api.auth import access_token_claims, auth_session_id_from_token, create_access_token
    from backend.app.api.h5_chat import router as h5_chat_router
    from backend.app.db import get_db

    installation_id = "heartbeat-slot-001"
    app = FastAPI()
    app.include_router(h5_chat_router)

    def get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_db_override
    client = TestClient(app)
    old_token = create_access_token(access_token_claims(test_user))
    new_token = create_access_token(access_token_claims(other_user))
    old_headers = {
        "Authorization": f"Bearer {old_token}",
        "X-Installation-Id": installation_id,
        "X-Lobster-Brand": "bihuo",
    }
    new_headers = {
        "Authorization": f"Bearer {new_token}",
        "X-Installation-Id": installation_id,
        "X-Lobster-Brand": "bihuo",
    }

    assert client.post("/api/h5-chat/device-heartbeat", headers=old_headers, json={}).status_code == 200
    claim_installation_slot(
        db_session,
        user_id=other_user.id,
        installation_id=installation_id,
        brand_mark="bihuo",
        auth_session_id=auth_session_id_from_token(new_token),
    )

    stale = client.post("/api/h5-chat/device-heartbeat", headers=old_headers, json={})
    assert stale.status_code == 200
    assert client.post("/api/h5-chat/device-heartbeat", headers=new_headers, json={}).status_code == 200
    assert db_session.query(InstallationSlotOwner).filter_by(user_id=test_user.id).count() == 1
    assert db_session.query(InstallationSlotOwner).filter_by(user_id=other_user.id).count() == 1


def test_same_raw_slot_h5_pending_messages_are_scoped_by_user(
    db_session,
    db_session_factory,
    test_user,
    other_user,
    patch_fuiou_settings,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api.auth import access_token_claims, create_access_token
    from backend.app.api.h5_chat import router as h5_chat_router
    from backend.app.db import get_db

    installation_id = "shared-route-slot-001"
    now = datetime.utcnow()
    db_session.add_all(
        [
            H5ChatMessage(
                id="h5-user-a",
                user_id=test_user.id,
                installation_id=installation_id,
                mode="client_command",
                content="user a command",
                status="pending",
                created_at=now,
                updated_at=now,
            ),
            H5ChatMessage(
                id="h5-user-b",
                user_id=other_user.id,
                installation_id=installation_id,
                mode="client_command",
                content="user b command",
                status="pending",
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    db_session.commit()

    app = FastAPI()
    app.include_router(h5_chat_router)

    def get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_db_override
    client = TestClient(app)
    headers_a = {
        "Authorization": f"Bearer {create_access_token(access_token_claims(test_user))}",
        "X-Installation-Id": installation_id,
        "X-Lobster-Brand": "bihuo",
    }
    headers_b = {
        "Authorization": f"Bearer {create_access_token(access_token_claims(other_user))}",
        "X-Installation-Id": installation_id,
        "X-Lobster-Brand": "bihuo",
    }

    pending_a = client.get("/api/h5-chat/pending", headers=headers_a)
    pending_b = client.get("/api/h5-chat/pending", headers=headers_b)

    assert pending_a.status_code == 200
    assert [item["id"] for item in pending_a.json()["items"]] == ["h5-user-a"]
    assert pending_b.status_code == 200
    assert [item["id"] for item in pending_b.json()["items"]] == ["h5-user-b"]


def test_same_raw_slot_scheduled_runs_are_scoped_by_user(
    db_session,
    db_session_factory,
    test_user,
    other_user,
    patch_fuiou_settings,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api.auth import access_token_claims, create_access_token
    from backend.app.api.scheduled_tasks import router as scheduled_router
    from backend.app.db import get_db

    installation_id = "shared-route-slot-002"
    now = datetime.utcnow()
    db_session.add_all(
        [
            ScheduledTaskRun(
                id="run-user-a",
                user_id=test_user.id,
                installation_id=installation_id,
                title="user a run",
                task_kind="client_workflow",
                content="user a",
                payload={},
                status="pending",
                created_at=now,
                updated_at=now,
            ),
            ScheduledTaskRun(
                id="run-user-b",
                user_id=other_user.id,
                installation_id=installation_id,
                title="user b run",
                task_kind="client_workflow",
                content="user b",
                payload={},
                status="pending",
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    db_session.commit()

    app = FastAPI()
    app.include_router(scheduled_router)

    def get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_db_override
    client = TestClient(app)
    headers_a = {
        "Authorization": f"Bearer {create_access_token(access_token_claims(test_user))}",
        "X-Installation-Id": installation_id,
        "X-Lobster-Brand": "bihuo",
    }
    headers_b = {
        "Authorization": f"Bearer {create_access_token(access_token_claims(other_user))}",
        "X-Installation-Id": installation_id,
        "X-Lobster-Brand": "bihuo",
    }

    pending_a = client.get("/api/scheduled-tasks/pending", headers=headers_a)
    pending_b = client.get("/api/scheduled-tasks/pending", headers=headers_b)

    assert pending_a.status_code == 200
    assert [item["id"] for item in pending_a.json()["items"]] == ["run-user-a"]
    assert pending_b.status_code == 200
    assert [item["id"] for item in pending_b.json()["items"]] == ["run-user-b"]
