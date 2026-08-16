from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.auth import access_token_claims, create_access_token, get_current_user
from backend.app.api.h5_chat import router as h5_chat_router
from backend.app.api.scheduled_tasks import router as scheduled_tasks_router
from backend.app.api.settings_api import router as settings_router
from backend.app.db import get_db
from backend.app.models import InstallationSlotOwner, User, UserInstallation, UserMachineIdentity


def _client_for_user(db_session_factory, user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(settings_router, prefix="")

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    def _get_current_user_override():
        s = db_session_factory()
        try:
            return s.query(User).filter(User.id == user_id).first()
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _get_current_user_override
    return TestClient(app)


def test_bind_keeps_unique_slots_so_one_account_can_show_multiple_devices(
    db_session,
    db_session_factory,
    test_user,
):
    client = _client_for_user(db_session_factory, test_user.id)

    first = client.post(
        "/api/installation-id/bind",
        headers={"X-Installation-Id": "device-slot-alpha"},
        json={"installation_id": "device-slot-alpha", "device_id": "device-slot-alpha"},
    )
    second = client.post(
        "/api/installation-id/bind",
        headers={"X-Installation-Id": "device-slot-beta"},
        json={"installation_id": "device-slot-beta", "device_id": "device-slot-beta"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["installation_id"] == "device-slot-alpha"
    assert second.json()["installation_id"] == "device-slot-beta"
    assert first.json()["signed"] is False
    assert second.json()["signed"] is False
    rows = {
        row.installation_id
        for row in db_session.query(UserInstallation).filter(UserInstallation.user_id == test_user.id).all()
    }
    assert {"device-slot-alpha", "device-slot-beta"}.issubset(rows)
    assert db_session.query(InstallationSlotOwner).filter(InstallationSlotOwner.user_id == test_user.id).count() == 2


def test_bind_signs_only_when_slot_is_already_used_by_another_account(
    db_session,
    db_session_factory,
    test_user,
    other_user,
):
    first_client = _client_for_user(db_session_factory, test_user.id)
    second_client = _client_for_user(db_session_factory, other_user.id)
    raw_slot = "shared-device-slot"

    first = first_client.post(
        "/api/installation-id/bind",
        headers={"X-Installation-Id": raw_slot},
        json={"installation_id": raw_slot, "device_id": raw_slot},
    )
    second = second_client.post(
        "/api/installation-id/bind",
        headers={"X-Installation-Id": raw_slot},
        json={"installation_id": raw_slot, "device_id": raw_slot},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["installation_id"] == raw_slot
    payload = second.json()
    assert payload["installation_id"] != raw_slot
    assert payload["signed"] is True
    assert payload["signature_reason"] == "duplicate"
    assert payload["replaced_installation_id"] == raw_slot
    assert re.fullmatch(rf"u{other_user.id}-[a-f0-9]{{32}}", payload["installation_id"])
    assert db_session.query(UserInstallation).filter_by(user_id=test_user.id, installation_id=raw_slot).one()
    assert db_session.query(UserInstallation).filter_by(user_id=other_user.id, installation_id=payload["installation_id"]).one()


def test_bind_does_not_resign_an_existing_signed_slot_on_later_login(
    db_session_factory,
    test_user,
    other_user,
):
    first_client = _client_for_user(db_session_factory, test_user.id)
    second_client = _client_for_user(db_session_factory, other_user.id)
    raw_slot = "copied-old-slot"
    assert first_client.post(
        "/api/installation-id/bind",
        headers={"X-Installation-Id": raw_slot},
        json={"installation_id": raw_slot, "device_id": raw_slot},
    ).status_code == 200
    signed = second_client.post(
        "/api/installation-id/bind",
        headers={"X-Installation-Id": raw_slot},
        json={"installation_id": raw_slot, "device_id": raw_slot},
    ).json()["installation_id"]

    again = second_client.post(
        "/api/installation-id/bind",
        headers={"X-Installation-Id": signed},
        json={"installation_id": signed, "device_id": "new-local-seed-after-ota"},
    )

    assert again.status_code == 200
    payload = again.json()
    assert payload["installation_id"] == signed
    assert payload["signed"] is True
    assert payload["signature_reason"] == "already_signed"


def test_bind_separates_same_account_copied_slot_by_machine_identity(
    db_session,
    db_session_factory,
    test_user,
):
    client = _client_for_user(db_session_factory, test_user.id)
    raw_slot = "same-user-copied-slot"

    first = client.post(
        "/api/installation-id/bind",
        headers={"X-Installation-Id": raw_slot},
        json={
            "installation_id": raw_slot,
            "device_id": raw_slot,
            "machine_instance_id": "machine-instance-alpha",
        },
    )
    second = client.post(
        "/api/installation-id/bind",
        headers={"X-Installation-Id": raw_slot},
        json={
            "installation_id": raw_slot,
            "device_id": raw_slot,
            "machine_instance_id": "machine-instance-beta",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["installation_id"] == raw_slot
    assert first_payload["signed"] is False
    assert second_payload["installation_id"] != raw_slot
    assert second_payload["signed"] is True
    assert second_payload["signature_reason"] == "duplicate_machine"

    again = client.post(
        "/api/installation-id/bind",
        headers={"X-Installation-Id": second_payload["installation_id"]},
        json={
            "installation_id": second_payload["installation_id"],
            "device_id": raw_slot,
            "machine_instance_id": "machine-instance-beta",
        },
    )
    assert again.status_code == 200
    assert again.json()["installation_id"] == second_payload["installation_id"]
    assert again.json()["signature_reason"] == "known_machine"

    rows = {
        row.machine_instance_id: row.installation_id
        for row in db_session.query(UserMachineIdentity).filter(UserMachineIdentity.user_id == test_user.id).all()
    }
    assert rows["machine-instance-alpha"] == raw_slot
    assert rows["machine-instance-beta"] == second_payload["installation_id"]

    installations = {
        row.installation_id
        for row in db_session.query(UserInstallation).filter(UserInstallation.user_id == test_user.id).all()
    }
    assert raw_slot in installations
    assert second_payload["installation_id"] in installations


def test_bind_keeps_signed_slot_when_machine_identity_changes_after_ota(
    db_session_factory,
    test_user,
    other_user,
):
    first_client = _client_for_user(db_session_factory, test_user.id)
    second_client = _client_for_user(db_session_factory, other_user.id)
    raw_slot = "ota-copied-slot"

    assert first_client.post(
        "/api/installation-id/bind",
        headers={"X-Installation-Id": raw_slot},
        json={
            "installation_id": raw_slot,
            "device_id": raw_slot,
            "machine_instance_id": "machine-instance-alpha",
        },
    ).status_code == 200
    signed_payload = second_client.post(
        "/api/installation-id/bind",
        headers={"X-Installation-Id": raw_slot},
        json={
            "installation_id": raw_slot,
            "device_id": raw_slot,
            "machine_instance_id": "machine-instance-beta",
        },
    ).json()
    signed_slot = signed_payload["installation_id"]
    assert signed_slot != raw_slot

    repaired_again = second_client.post(
        "/api/installation-id/bind",
        headers={"X-Installation-Id": signed_slot},
        json={
            "installation_id": signed_slot,
            "device_id": raw_slot,
            "machine_instance_id": "machine-instance-after-ota",
        },
    )

    assert repaired_again.status_code == 200
    payload = repaired_again.json()
    assert payload["installation_id"] == signed_slot
    assert payload["signature_reason"] == "already_signed"


def test_h5_device_status_dispatch_and_online_claim_use_the_same_slot(
    db_session_factory,
    test_user,
):
    app = FastAPI()
    app.include_router(h5_chat_router)
    app.include_router(scheduled_tasks_router)

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    client = TestClient(app)
    token = create_access_token(access_token_claims(test_user))
    auth = {"Authorization": f"Bearer {token}", "X-Lobster-Brand": "bihuo"}
    slot_a = "signed-slot-alpha"
    slot_b = "signed-slot-beta"

    for slot, name in ((slot_a, "Online A"), (slot_b, "Online B")):
        heartbeat_body = {"display_name": name, "capabilities": ["asset_video_split_v1"]}
        if slot == slot_a:
            heartbeat_body["wechat_contacts"] = [
                {"display_name": "测试联系人", "wx_no": "wxid_contact_alpha"},
            ]
        resp = client.post(
            "/api/h5-chat/device-heartbeat",
            headers={**auth, "X-Installation-Id": slot},
            json=heartbeat_body,
        )
        assert resp.status_code == 200

    status = client.get("/api/h5-chat/devices/status", headers=auth)
    assert status.status_code == 200
    devices = status.json()["devices"]
    assert {row["installation_id"] for row in devices} >= {slot_a, slot_b}
    assert all(row["online"] for row in devices if row["installation_id"] in {slot_a, slot_b})
    slot_a_status = next(row for row in devices if row["installation_id"] == slot_a)
    assert len(slot_a_status["wechat_contacts"]) == 1
    assert slot_a_status["wechat_contacts"][0]["name"] == "测试联系人"
    assert slot_a_status["wechat_contacts"][0]["wx_no"] == "wxid_contact_alpha"

    created = client.post(
        "/api/scheduled-tasks/tasks",
        headers={**auth, "X-Installation-Id": slot_b},
        json={
            "title": "Targeted client task",
            "task_kind": "client_workflow",
            "content": "Run only on selected Online device",
            "payload": {"action": "noop"},
            "schedule_type": "once",
            "installation_ids": [slot_a],
        },
    )
    assert created.status_code == 200
    task = created.json()["task"]
    assert task["installation_ids"] == [slot_b]
    run = created.json()["runs"][0]
    assert run["installation_id"] == slot_b

    pending_a = client.get(
        "/api/scheduled-tasks/pending",
        headers={**auth, "X-Installation-Id": slot_a},
    )
    pending_b = client.get(
        "/api/scheduled-tasks/pending",
        headers={**auth, "X-Installation-Id": slot_b},
    )

    assert pending_a.status_code == 200
    assert pending_a.json()["items"] == []
    assert pending_b.status_code == 200
    assert [item["id"] for item in pending_b.json()["items"]] == [run["id"]]
