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
