"""虚拟员工「槽位号」与「设备号 + 验证码」双方式绑定回归测试。

要求：两种添加方式都要把设备绑定到远程中继，且已有记录可以在两种方式之间切换。
"""
from __future__ import annotations

import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.api import manage as manage_api
from backend.app.manage_models import MAiEmployee
from backend.app.models import H5ChatDevicePresence

SLOT = "23b2de30fd27f0613b26b2dcd9e5b865"
DEVICE = "NUUI-2VSQ"
CODE = "679953"


@pytest.fixture
def relay(monkeypatch):
    box = {"devices": []}
    calls: list[tuple] = []

    def _fake_call(method, path, body=None):
        calls.append((method, path, body))
        if path == "/api/remote-admin/devices":
            if method == "POST":
                box["devices"] = [{
                    "id": DEVICE,
                    "online": True,
                    "lastSeen": int(time.time() * 1000),
                }]
                return {"ok": True, "device": box["devices"][0]}
            return {"devices": box["devices"]}
        if path == "/api/remote-admin/controller-session":
            return {"token": "relay-token-123", "expiresAt": "2026-11-27T10:00:00Z"}
        raise AssertionError(f"unexpected relay call: {method} {path}")

    monkeypatch.setattr(manage_api, "_remote_support_call", _fake_call)

    class _Company:
        id = 1

    monkeypatch.setattr(manage_api, "_require_company", lambda db, cid, user: _Company())
    monkeypatch.setattr(manage_api, "_require_plan_admin", lambda db, company, user: None)
    monkeypatch.setattr(manage_api, "_audit", lambda *a, **k: None)
    manage_api._invalidate_relay_cache()
    return box, calls


def _presence(db, *, slot=SLOT, device=DEVICE, code=CODE):
    row = H5ChatDevicePresence(
        user_id=1,
        installation_id=slot,
        display_name="测试设备",
        account_payload={"remote_support": {
            "enabled": True,
            "running": True,
            "device_id": device,
            "verification_code": code,
            "server": "https://todesk.bhos.online",
        }},
        last_seen_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    return row


def _add_slot(db):
    return manage_api.create_ai_employee(
        manage_api.AiEmployeeIn(company_id=1, installation_id=SLOT),
        user=object(),
        db=db,
    )


def test_slot_add_binds_with_heartbeat_code(db_session, relay):
    _box, calls = relay
    _presence(db_session)

    result = _add_slot(db_session)

    assert result["ok"] is True
    assert result["source"] == "slot"
    assert result["device_id"] == DEVICE
    assert result["remote_bound"] is True
    assert result["remote_error"] == ""
    assert any(
        method == "POST" and isinstance(body, dict) and body.get("verificationCode") == CODE
        for method, path, body in calls if path == "/api/remote-admin/devices"
    )
    row = db_session.query(MAiEmployee).one()
    assert row.source == "slot"
    assert row.device_id == DEVICE


def test_device_add_binds_and_reuses_slot_row(db_session, relay):
    _box, _calls = relay
    _presence(db_session)
    _add_slot(db_session)

    result = manage_api.create_ai_employee(
        manage_api.AiEmployeeIn(company_id=1, device_id=DEVICE, verification_code=CODE),
        user=object(),
        db=db_session,
    )

    assert result["source"] == "device"
    assert result["remote_bound"] is True
    rows = db_session.query(MAiEmployee).all()
    assert len(rows) == 1
    assert rows[0].installation_id == SLOT
    assert rows[0].device_id == DEVICE


def test_patch_slot_mode_also_binds(db_session, relay):
    _box, calls = relay
    _presence(db_session)
    row = MAiEmployee(company_id=1, name="老设备", installation_id="old-slot",
                      device_id="", source="slot", status="enabled")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    result = manage_api.update_ai_employee(
        row.id,
        manage_api.AiEmployeePatchIn(installation_id=SLOT),
        user=object(),
        db=db_session,
    )

    assert result["remote_bound"] is True
    assert result["source"] == "slot"
    assert any(method == "POST" for method, _path, _body in calls)
    db_session.refresh(row)
    assert row.installation_id == SLOT
    assert row.device_id == DEVICE


def test_patch_switches_slot_row_to_device_mode(db_session, relay):
    _box, calls = relay
    _presence(db_session)
    row = MAiEmployee(company_id=1, name="老设备", installation_id=SLOT,
                      device_id="", source="slot", status="enabled")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    result = manage_api.update_ai_employee(
        row.id,
        manage_api.AiEmployeePatchIn(device_id=DEVICE, verification_code=CODE),
        user=object(),
        db=db_session,
    )

    assert result["remote_bound"] is True
    assert result["source"] == "device"
    assert any(method == "POST" for method, _path, _body in calls)
    db_session.refresh(row)
    assert row.source == "device"
    assert row.device_id == DEVICE


def test_patch_rejects_wrong_verification_code(db_session, relay):
    _presence(db_session)
    row = MAiEmployee(company_id=1, name="老设备", installation_id=SLOT,
                      device_id="", source="slot", status="enabled")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    with pytest.raises(HTTPException) as err:
        manage_api.update_ai_employee(
            row.id,
            manage_api.AiEmployeePatchIn(device_id=DEVICE, verification_code="000000"),
            user=object(),
            db=db_session,
        )
    assert err.value.status_code == 403


def test_ai_row_reports_real_binding_status(db_session, relay):
    _presence(db_session)
    row = MAiEmployee(company_id=1, name="设备", installation_id=SLOT,
                      device_id=DEVICE, source="slot", status="enabled")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    company = SimpleNamespace(id=1)

    unbound = manage_api._ai_row_json(db_session, company, row, None, {}, {})
    assert unbound["remote_bound"] is False
    assert unbound["can_remote"] is False

    bound = manage_api._ai_row_json(db_session, company, row, None, {}, {DEVICE: True})
    assert bound["remote_bound"] is True
    assert bound["can_remote"] is True
