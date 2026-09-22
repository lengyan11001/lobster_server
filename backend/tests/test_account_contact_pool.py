"""账号级微信联系方式池：抖音私信接管上报 → 个微自动加好友（同账号跨机器）领取。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.auth import get_current_user
from backend.app.api.wechat_contact_pool import router as pool_router
from backend.app.db import get_db
from backend.app.models import User, WechatContactReport


def _client(db_session_factory, user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(pool_router)

    def get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    def current_user_override():
        with db_session_factory() as session:
            return session.get(User, user_id)

    app.dependency_overrides[get_db] = get_db_override
    app.dependency_overrides[get_current_user] = current_user_override
    return TestClient(app)


def test_report_claim_ack_flow_works_across_devices(db_session_factory, db_session, test_user):
    client = _client(db_session_factory, test_user.id)
    device_a = {"X-Installation-Id": "u22-device-a"}
    device_b = {"X-Installation-Id": "u22-device-b"}
    device_c = {"X-Installation-Id": "u22-device-c"}

    reported = client.post(
        "/api/wechat-contact-pool/report",
        json={
            "platform": "douyin",
            "account_label": "抖音账号 1",
            "items": [
                {"value": "138 0013 8000", "kind": "mobile", "username": "小王", "conversation_id": "chat_inbox"},
                {"value": "13800138000", "kind": "mobile", "username": "小王"},
                {"value": "wxid_abc123", "kind": "wechat_id", "username": "小李"},
                {"value": "不是号码", "kind": "mobile"},
            ],
        },
        headers=device_a,
    )
    assert reported.status_code == 200
    body = reported.json()
    assert body["created"] == 2, body
    assert body["skipped"] == 2, body
    assert body["pending"] == 2, body

    rows = db_session.query(WechatContactReport).all()
    assert {row.value for row in rows} == {"13800138000", "wxid_abc123"}
    assert all(row.reported_by == "u22-device-a" for row in rows)

    claimed = client.post(
        "/api/wechat-contact-pool/claim",
        json={"platform": "douyin", "limit": 10},
        headers=device_b,
    )
    assert claimed.status_code == 200
    payload = claimed.json()
    assert [item["value"] for item in payload["items"]] == ["13800138000", "wxid_abc123"]
    assert payload["pending"] == 0

    # 已经在设备 B 手里，设备 C 不会重复领到同一条
    second_claim = client.post(
        "/api/wechat-contact-pool/claim",
        json={"platform": "douyin", "limit": 10},
        headers=device_c,
    )
    assert second_claim.json()["claimed"] == 0

    acked = client.post(
        "/api/wechat-contact-pool/ack",
        json={"platform": "douyin", "added": ["13800138000"], "failed": ["wxid_abc123"], "error": "微信没有登录"},
        headers=device_b,
    )
    assert acked.status_code == 200
    assert acked.json() == {"ok": True, "added": 1, "released": 1}

    stats = client.get(
        "/api/wechat-contact-pool/stats",
        params={"platform": "douyin"},
        headers=device_a,
    ).json()
    assert stats["added"] == 1
    assert stats["pending"] == 1

    # 失败的那条放回池子，同账号的另一台机器能重新领到
    reclaim = client.post(
        "/api/wechat-contact-pool/claim",
        json={"platform": "douyin", "limit": 10},
        headers=device_c,
    )
    assert [item["value"] for item in reclaim.json()["items"]] == ["wxid_abc123"]

    # 已经加过的号码再上报也不会回炉
    again = client.post(
        "/api/wechat-contact-pool/report",
        json={"platform": "douyin", "items": [{"value": "13800138000", "kind": "mobile"}]},
        headers=device_a,
    )
    assert again.json()["created"] == 0
    assert again.json()["skipped"] == 1
