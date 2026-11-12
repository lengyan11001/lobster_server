from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.auth import get_current_user
from backend.app.api.settings_api import router as settings_router
from backend.app.db import get_db
from backend.app.models import User, UserMachineIdentity


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


def test_normalize_rejects_placeholder():
    from backend.app.api.settings_api import _normalize_installation_id

    assert _normalize_installation_id("__LOBSTER_INSTALLATION_ID__") == ""
    assert _normalize_installation_id("NULL") == ""
    assert _normalize_installation_id("826cbd8b91b14a339058d162ea634193") == "826cbd8b91b14a339058d162ea634193"


def test_bind_with_placeholder_never_returns_placeholder(db_session, db_session_factory, test_user):
    machine = "mw" + "a" * 48
    client = _client_for_user(db_session_factory, test_user.id)

    resp = client.post(
        "/api/installation-id/bind",
        json={
            "installation_id": "__LOBSTER_INSTALLATION_ID__",
            "device_id": "__LOBSTER_INSTALLATION_ID__",
            "machine_instance_id": machine,
        },
    )

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["installation_id"]
    assert payload["installation_id"] != "__LOBSTER_INSTALLATION_ID__"
    assert "INSTALLATION_ID" not in payload["installation_id"].upper()

    row = (
        db_session.query(UserMachineIdentity)
        .filter(UserMachineIdentity.user_id == test_user.id)
        .one()
    )
    assert row.installation_id != "__LOBSTER_INSTALLATION_ID__"
    assert "INSTALLATION_ID" not in row.installation_id.upper()


def test_bind_keeps_valid_current_slot_when_machine_row_is_placeholder(
    db_session, db_session_factory, test_user
):
    current = "826cbd8b91b14a339058d162ea634193"
    machine = "mw" + "b" * 48
    db_session.add(
        UserMachineIdentity(
            user_id=test_user.id,
            machine_instance_id=machine,
            installation_id="__LOBSTER_INSTALLATION_ID__",
            last_seen_at=datetime.utcnow(),
        )
    )
    db_session.commit()

    client = _client_for_user(db_session_factory, test_user.id)
    resp = client.post(
        "/api/installation-id/bind",
        json={
            "installation_id": current,
            "device_id": current,
            "machine_instance_id": machine,
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["installation_id"] == current


def test_machine_id_rotation_with_legacy_keeps_signed_slot(
    db_session, db_session_factory, test_user
):
    """machine_id 变化但带 legacy 旧机器时，必须保留原 signed 槽位，不能当成克隆机分槽。"""
    slot = f"u{test_user.id}-" + "a" * 32
    old_machine = "mw" + "1" * 48
    new_machine = "mw" + "2" * 48
    db_session.add(
        UserMachineIdentity(
            user_id=test_user.id,
            machine_instance_id=old_machine,
            installation_id=slot,
            last_seen_at=datetime.utcnow(),
        )
    )
    db_session.commit()

    client = _client_for_user(db_session_factory, test_user.id)
    resp = client.post(
        "/api/installation-id/bind",
        json={
            "installation_id": slot,
            "device_id": "b" * 32,
            "machine_instance_id": new_machine,
            "legacy_machine_instance_id": old_machine,
        },
    )

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["installation_id"] == slot
    assert payload["signature_reason"] != "duplicate_machine"
