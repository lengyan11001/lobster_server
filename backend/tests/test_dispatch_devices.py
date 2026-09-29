"""可调度设备（系统设备）：管理后台配置 / H5 选择 / 只允许 AI 营销的兜底校验。"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.api import admin as admin_api
from backend.app.api import h5_chat as h5_chat_api
from backend.app.models import DispatchDevice, H5ChatDevicePresence
from backend.app.services import dispatch_devices


def _seed_presence(db, user, slot, minutes_ago=0):
    row = H5ChatDevicePresence(
        user_id=user.id,
        installation_id=slot,
        display_name=None,
        last_seen_at=datetime.utcnow() - timedelta(minutes=minutes_ago),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_admin_dispatch_device_crud_paging_and_search(db_session):
    for i in range(5):
        admin_api.admin_create_dispatch_device(
            admin_api.DispatchDeviceBody(slot="slot-%02d" % i, name="设备%d" % i, note="备注%d" % i),
            ctx=None,
            db=db_session,
        )
    assert db_session.query(DispatchDevice).count() == 5

    with pytest.raises(HTTPException) as dup:
        admin_api.admin_create_dispatch_device(
            admin_api.DispatchDeviceBody(slot="slot-00"), ctx=None, db=db_session
        )
    assert dup.value.status_code == 409

    page1 = admin_api.admin_list_dispatch_devices(
        page=1, page_size=2, q="", status_filter="", ctx=None, db=db_session
    )
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    assert page1["pages"] == 3

    page3 = admin_api.admin_list_dispatch_devices(
        page=3, page_size=2, q="", status_filter="", ctx=None, db=db_session
    )
    assert len(page3["items"]) == 1

    by_slot = admin_api.admin_list_dispatch_devices(
        page=1, page_size=20, q="slot-03", status_filter="", ctx=None, db=db_session
    )
    assert [d["installation_id"] for d in by_slot["items"]] == ["slot-03"]

    by_name = admin_api.admin_list_dispatch_devices(
        page=1, page_size=20, q="设备2", status_filter="", ctx=None, db=db_session
    )
    assert [d["installation_id"] for d in by_name["items"]] == ["slot-02"]

    target = page1["items"][0]
    admin_api.admin_update_dispatch_device(
        target["id"], admin_api.DispatchDevicePatchBody(status="disabled"), ctx=None, db=db_session
    )
    disabled = admin_api.admin_list_dispatch_devices(
        page=1, page_size=20, q="", status_filter="disabled", ctx=None, db=db_session
    )
    assert [d["id"] for d in disabled["items"]] == [target["id"]]

    with pytest.raises(HTTPException) as bad_status:
        admin_api.admin_update_dispatch_device(
            target["id"], admin_api.DispatchDevicePatchBody(status="nope"), ctx=None, db=db_session
        )
    assert bad_status.value.status_code == 400

    admin_api.admin_delete_dispatch_device(target["id"], ctx=None, db=db_session)
    assert db_session.query(DispatchDevice).count() == 4


def test_admin_list_reports_online_and_owner(db_session, test_user):
    device = admin_api.admin_create_dispatch_device(
        admin_api.DispatchDeviceBody(slot="sys-slot-online", name="系统设备A"), ctx=None, db=db_session
    )["device"]
    _seed_presence(db_session, test_user, "sys-slot-online")
    listed = admin_api.admin_list_dispatch_devices(
        page=1, page_size=20, q="sys-slot-online", status_filter="", ctx=None, db=db_session
    )
    row = listed["items"][0]
    assert row["id"] == device["id"]
    assert row["online"] is True
    assert row["owner_user_id"] == test_user.id


def test_devices_status_exposes_system_devices_and_selection(db_session, test_user):
    _seed_presence(db_session, test_user, "own-slot-1")
    admin_api.admin_create_dispatch_device(
        admin_api.DispatchDeviceBody(slot="sys-slot-1", name="系统设备1"), ctx=None, db=db_session
    )
    payload = h5_chat_api.h5_devices_status(current_user=test_user, db=db_session)
    assert [d["installation_id"] for d in payload["devices"]] == ["own-slot-1"]
    assert [d["installation_id"] for d in payload["system_devices"]] == ["sys-slot-1"]
    assert payload["selection"] == {"installation_id": "", "source": ""}
    assert payload["marketing_only"] is False


def test_h5_select_system_device_restricts_to_marketing(db_session, test_user):
    admin_api.admin_create_dispatch_device(
        admin_api.DispatchDeviceBody(slot="sys-slot-9", name="系统设备9"), ctx=None, db=db_session
    )
    picked = h5_chat_api.h5_save_device_selection(
        h5_chat_api.H5DeviceSelectionIn(installation_id="sys-slot-9", source="system"),
        current_user=test_user,
        db=db_session,
    )
    assert picked["marketing_only"] is True
    assert dispatch_devices.user_uses_system_device(db_session, test_user.id) is True
    with pytest.raises(HTTPException) as blocked:
        dispatch_devices.assert_marketing_only_allowed(db_session, test_user.id, "workflow")
    assert blocked.value.status_code == 403

    status = h5_chat_api.h5_devices_status(current_user=test_user, db=db_session)
    assert status["marketing_only"] is True
    assert status["selection"] == {"installation_id": "sys-slot-9", "source": "system"}

    _seed_presence(db_session, test_user, "own-slot-2")
    back = h5_chat_api.h5_save_device_selection(
        h5_chat_api.H5DeviceSelectionIn(installation_id="own-slot-2", source="own"),
        current_user=test_user,
        db=db_session,
    )
    assert back["marketing_only"] is False
    dispatch_devices.assert_marketing_only_allowed(db_session, test_user.id, "workflow")


def test_h5_rejects_unknown_system_device_and_foreign_device(db_session, test_user, other_user):
    with pytest.raises(HTTPException) as not_in_list:
        h5_chat_api.h5_save_device_selection(
            h5_chat_api.H5DeviceSelectionIn(installation_id="not-configured", source="system"),
            current_user=test_user,
            db=db_session,
        )
    assert not_in_list.value.status_code == 403

    _seed_presence(db_session, other_user, "other-slot")
    with pytest.raises(HTTPException) as foreign:
        h5_chat_api.h5_save_device_selection(
            h5_chat_api.H5DeviceSelectionIn(installation_id="other-slot", source="own"),
            current_user=test_user,
            db=db_session,
        )
    assert foreign.value.status_code == 403

    admin_api.admin_create_dispatch_device(
        admin_api.DispatchDeviceBody(slot="sys-off"), ctx=None, db=db_session
    )
    listed = admin_api.admin_list_dispatch_devices(
        page=1, page_size=20, q="sys-off", status_filter="", ctx=None, db=db_session
    )
    admin_api.admin_update_dispatch_device(
        listed["items"][0]["id"], admin_api.DispatchDevicePatchBody(status="disabled"), ctx=None, db=db_session
    )
    with pytest.raises(HTTPException) as disabled:
        h5_chat_api.h5_save_device_selection(
            h5_chat_api.H5DeviceSelectionIn(installation_id="sys-off", source="system"),
            current_user=test_user,
            db=db_session,
        )
    assert disabled.value.status_code == 403


def test_workflow_and_task_endpoints_call_the_marketing_only_guard():
    root = Path(__file__).resolve().parents[2]
    workflows = (root / "backend" / "app" / "api" / "h5_workflows.py").read_text(encoding="utf-8")
    tasks = (root / "backend" / "app" / "api" / "scheduled_tasks.py").read_text(encoding="utf-8")
    chat = (root / "backend" / "app" / "api" / "h5_chat.py").read_text(encoding="utf-8")
    assert workflows.count("assert_marketing_only_allowed") >= 2, "activate / activate-inline 都要拦"
    assert "assert_marketing_only_allowed" in tasks, "定时任务创建要拦"
    assert "system_devices" in chat and "marketing_only" in chat

def _mini_app(router, db_session_factory, user, *, admin=False):
    from fastapi import FastAPI

    from backend.app.db import get_db
    from backend.app.api.auth import get_current_user

    app = FastAPI()
    app.include_router(router)

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    if admin:
        app.dependency_overrides[admin_api._require_admin] = lambda: None
    else:
        app.dependency_overrides[get_current_user] = lambda: user
    return app


def test_admin_dispatch_device_routes_are_wired(db_session_factory, test_user):
    from fastapi.testclient import TestClient

    client = TestClient(_mini_app(admin_api.router, db_session_factory, test_user, admin=True))
    created = client.post("/admin/api/dispatch-devices", json={"slot": "slot-route-1", "name": "路由设备"})
    assert created.status_code == 200, created.text
    assert created.json()["device"]["installation_id"] == "slot-route-1"

    listed = client.get("/admin/api/dispatch-devices", params={"page": 1, "page_size": 10, "q": "route"})
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1 and len(body["items"]) == 1

    device_id = body["items"][0]["id"]
    patched = client.patch("/admin/api/dispatch-devices/%d" % device_id, json={"status": "disabled", "note": "停用原因"})
    assert patched.status_code == 200
    assert patched.json()["device"]["status"] == "disabled"
    assert patched.json()["device"]["note"] == "停用原因"

    deleted = client.delete("/admin/api/dispatch-devices/%d" % device_id)
    assert deleted.status_code == 200
    assert client.get("/admin/api/dispatch-devices", params={"q": "route"}).json()["total"] == 0


def test_h5_device_selection_routes_are_wired(db_session_factory, test_user):
    from fastapi.testclient import TestClient

    session = db_session_factory()
    try:
        admin_api.admin_create_dispatch_device(
            admin_api.DispatchDeviceBody(slot="slot-h5-1", name="H5 系统设备"), ctx=None, db=session
        )
    finally:
        session.close()

    client = TestClient(_mini_app(h5_chat_api.router, db_session_factory, test_user))
    status = client.get("/api/h5-chat/devices/status")
    assert status.status_code == 200, status.text
    payload = status.json()
    assert [d["installation_id"] for d in payload["system_devices"]] == ["slot-h5-1"]
    assert payload["marketing_only"] is False

    picked = client.post("/api/h5-chat/device-selection", json={"installation_id": "slot-h5-1", "source": "system"})
    assert picked.status_code == 200, picked.text
    assert picked.json()["marketing_only"] is True

    again = client.get("/api/h5-chat/device-selection")
    assert again.status_code == 200
    assert again.json()["installation_id"] == "slot-h5-1"
    assert again.json()["source"] == "system"

    blocked = client.post("/api/h5-chat/device-selection", json={"installation_id": "nope", "source": "system"})
    assert blocked.status_code == 403
