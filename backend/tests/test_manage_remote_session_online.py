"""manage 远程画面「设备不在线」误判的回归测试。

线上现象（2026-09-17，设备 WI93-R1P9，虚拟员工 id=71）：
  manage「虚拟员工」里显示**在线**（客户端心跳 < 5 分钟），点「远程」却提示
  「远程客户端当前不在线（设备号 WI93-R1P9）」，而同机同时刻中继 /api/remote-admin/devices
  返回的是 `id=WI93-R1P9 online=True lastSeen=…`。

根因：中继在设备在线时返回 deviceView（顶层 online=true，**没有 lastDevice 字段**），
只有离线时才回落到绑定时快照（lastDevice.online=false）；manage 只读 lastDevice.online，
于是把在线设备一律判成离线。

覆盖：
1. 中继「在线」响应形态（顶层 online）→ 允许开远程会话（200，返回 token）；
2. 中继「离线」响应形态（只有 lastDevice.online=false）→ 409 且带最后一次在线时间；
3. 两种形态都存在时以顶层为准；只有 lastSeen 很新时也算在线（字段改名兜底）；
4. lastSeen 很旧（超出宽限）且 online=false → 判离线。
"""
from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from backend.app.api import manage as manage_api
from backend.app.manage_models import MAiEmployee

DEVICE_ID = "WI93-R1P9"


class _Admin:
    id = 0
    email = "platform-admin@local"
    brand_mark = "bihuo"
    role = "admin"


def _live_device(**overrides):
    """中继在设备在线时的真实返回（deviceView：顶层 online、无 lastDevice）。"""
    data = {
        "id": DEVICE_ID,
        "name": "BHZN Windows PC-20260112OPIH",
        "platform": "windows",
        "online": True,
        "supportEnabled": True,
        "controlEnabled": True,
        "lastSeen": int(time.time() * 1000) - 5_000,
        "agentVersion": "0.2.10-rs",
    }
    data.update(overrides)
    return data


def _offline_device(**overrides):
    """中继在设备离线时的返回（绑定快照：lastDevice.online=false）。"""
    stale_ms = int(time.time() * 1000) - 36 * 3600 * 1000
    data = {
        "id": DEVICE_ID,
        "online": False,
        "lastSeen": stale_ms,
        "bindingId": "admin_bind_c1818f3247307d411519e659",
        "label": "BHZN Windows",
        "lastDevice": {
            "id": DEVICE_ID,
            "online": False,
            "lastSeen": stale_ms,
            "name": "BHZN Windows",
        },
    }
    data.update(overrides)
    return data


@pytest.fixture
def patch_remote(monkeypatch, db_session):
    """把中继调用、心跳、公司权限、审计替换成可控实现；虚拟员工用真实表行。"""
    calls: list[tuple] = []
    box: dict = {"devices": [_live_device()]}

    def _fake_call(method, path, body=None):
        calls.append((method, path))
        if path == "/api/remote-admin/devices":
            return {"devices": box["devices"]}
        if path == "/api/remote-admin/controller-session":
            return {"token": "relay-token-123", "expiresAt": "2026-09-17T10:00:00Z"}
        raise AssertionError(f"unexpected relay call: {path}")

    monkeypatch.setattr(manage_api, "_remote_support_call", _fake_call)
    monkeypatch.setattr(manage_api, "_slot_presence", lambda db, inst: (None, {}, True, "2026-09-17T01:21:20"))
    class _Company:
        id = 1

    monkeypatch.setattr(manage_api, "_require_company", lambda db, cid, user, write=False: _Company())
    monkeypatch.setattr(manage_api, "_require_plan_admin", lambda db, company, user: None)
    monkeypatch.setattr(manage_api, "_audit", lambda *a, **k: None)

    employee = MAiEmployee(
        company_id=1,
        name="老板主机-远程验证",
        installation_id="u22-81ec542adefcee408ca6322ce54f2a17",
        device_id=DEVICE_ID,
        source="device",
        status="enabled",
    )
    db_session.add(employee)
    db_session.commit()
    db_session.refresh(employee)
    box["employee_id"] = employee.id
    return box, calls


def _session(db, admin, ai_id):
    return manage_api.ai_employee_remote_session(ai_id, user=admin, db=db)


def test_relay_online_helper_accepts_live_shape():
    assert manage_api._relay_device_online(_live_device()) is True


def test_relay_online_helper_accepts_legacy_nested_shape():
    nested = _offline_device(lastDevice={"id": DEVICE_ID, "online": True})
    assert manage_api._relay_device_online(nested) is True


def test_relay_online_helper_uses_recent_lastseen_when_flags_missing():
    fresh = {"id": DEVICE_ID, "lastSeen": int(time.time() * 1000) - 30_000}
    assert manage_api._relay_device_online(fresh) is True
    stale = {"id": DEVICE_ID, "lastSeen": int(time.time() * 1000) - 3_600_000}
    assert manage_api._relay_device_online(stale) is False
    assert manage_api._relay_device_online({}) is False
    assert manage_api._relay_device_online(None) is False


def test_remote_session_allowed_while_device_is_online(db_session, patch_remote):
    """线上 bug 的核心断言：中继说在线 → 必须能开远程会话，不能再报 409。"""
    box, _calls = patch_remote
    result = _session(db_session, _Admin(), box["employee_id"])
    assert result["ok"] is True
    assert result["token"] == "relay-token-123"
    assert result["device_id"] == DEVICE_ID


def test_remote_session_reports_offline_with_last_seen(db_session, patch_remote):
    box, _calls = patch_remote
    box["devices"] = [_offline_device()]
    with pytest.raises(HTTPException) as err:
        _session(db_session, _Admin(), box["employee_id"])
    assert err.value.status_code == 409
    detail = str(err.value.detail)
    assert "不在线" in detail
    assert DEVICE_ID in detail
    assert "最后一次在线" in detail


def test_remote_session_reports_unbound_device(db_session, patch_remote):
    box, _calls = patch_remote
    box["devices"] = []
    with pytest.raises(HTTPException) as err:
        _session(db_session, _Admin(), box["employee_id"])
    assert err.value.status_code == 409
    assert "还没连上中继" in str(err.value.detail)
