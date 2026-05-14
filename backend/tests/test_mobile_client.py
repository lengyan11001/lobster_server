from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


PHONE = "13800138000"
PHONE_EMAIL = f"{PHONE}@sms.lobster.local"
DEVICE_ID = "mp_test_device_001"


@pytest.fixture
def mobile_users(db_session):
    from backend.app.models import User

    phone_user = User(
        email=PHONE_EMAIL,
        hashed_password="x",
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    temp_user = User(
        email="wx_openid_test@wechat.lobster.local",
        hashed_password="x",
        credits=Decimal("0"),
        role="user",
        preferred_model="sutui",
        wechat_openid="openid_test",
        created_at=datetime.utcnow(),
    )
    db_session.add_all([phone_user, temp_user])
    db_session.commit()
    db_session.refresh(phone_user)
    db_session.refresh(temp_user)
    return phone_user, temp_user


@pytest.fixture
def mobile_client(db_session_factory, mobile_users, monkeypatch):
    from backend.app.api import mobile_client as mobile_module
    from backend.app.api.auth import get_current_user
    from backend.app.api.mobile_client import router as mobile_router
    from backend.app.core.config import settings
    from backend.app.db import get_db
    from backend.app.models import User

    _, temp_user = mobile_users
    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)
    monkeypatch.setattr(settings, "wechat_app_id", "wx-test", raising=False)
    monkeypatch.setattr(settings, "wechat_app_secret", "secret-test", raising=False)
    monkeypatch.setattr(mobile_module, "_exchange_wechat_phone_code", lambda code: PHONE)
    monkeypatch.setattr(mobile_module, "_exchange_wechat_login_code", lambda code: "openid_login")

    app = FastAPI()
    app.include_router(mobile_router, prefix="")

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    def _get_current_user_override():
        s = db_session_factory()
        try:
            return s.query(User).filter(User.id == temp_user.id).first()
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _get_current_user_override
    return TestClient(app)


def test_phone_status_reports_registered_online(mobile_client):
    res = mobile_client.get(f"/api/mobile/phone/status?phone={PHONE}")

    assert res.status_code == 200
    data = res.json()
    assert data["registered"] is True
    assert data["has_online"] is True


def test_phone_status_reports_missing_online(mobile_client):
    res = mobile_client.get("/api/mobile/phone/status?phone=13900139000")

    assert res.status_code == 200
    data = res.json()
    assert data["registered"] is False
    assert "没有 online 版本" in data["message"]


def test_bind_mobile_device_merges_wechat_session_into_phone_user(mobile_client, db_session_factory, mobile_users):
    phone_user, temp_user = mobile_users

    res = mobile_client.post(
        "/api/mobile/devices/bind",
        json={
            "phone_code": "wx-phone-code",
            "device_id": DEVICE_ID,
            "platform": "wechat_miniprogram",
            "display_name": "微信小程序",
        },
    )

    assert res.status_code == 200
    data = res.json()
    assert data["user_id"] == phone_user.id
    assert data["phone"] == PHONE
    assert data["phone_verified"] is True
    assert data["access_token"]

    from backend.app.models import MobileDeviceBinding, User

    with db_session_factory() as s:
        bound_user = s.query(User).filter(User.id == phone_user.id).first()
        old_temp = s.query(User).filter(User.id == temp_user.id).first()
        binding = s.query(MobileDeviceBinding).filter(MobileDeviceBinding.device_id == DEVICE_ID).first()
        assert bound_user.wechat_openid == "openid_test"
        assert old_temp.wechat_openid is None
        assert binding.user_id == phone_user.id
        assert binding.phone == PHONE


def test_downloads_require_bound_device(mobile_client):
    res = mobile_client.get(f"/api/mobile/downloads?device_id={DEVICE_ID}")

    assert res.status_code == 403
    assert "未绑定" in res.json()["detail"]


def test_downloads_collect_assets_runs_and_h5_results(db_session, db_session_factory, mobile_users, monkeypatch):
    from backend.app.api.auth import get_current_user
    from backend.app.api.mobile_client import router as mobile_router
    from backend.app.core.config import settings
    from backend.app.db import get_db
    from backend.app.models import Asset, H5ChatEvent, H5ChatMessage, MobileDeviceBinding, ScheduledTaskRun, User

    phone_user, _ = mobile_users
    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)
    now = datetime.utcnow()
    db_session.add(
        MobileDeviceBinding(
            user_id=phone_user.id,
            phone=PHONE,
            device_id=DEVICE_ID,
            platform="wechat_miniprogram",
            display_name="微信小程序",
            created_at=now,
            last_seen_at=now,
        )
    )
    db_session.add(
        Asset(
            asset_id="asset-video-1",
            user_id=phone_user.id,
            filename="asset-video-1.mp4",
            media_type="video",
            file_size=123,
            source_url="https://cdn.example.com/asset-video-1.mp4",
            prompt="产品宣传视频",
            model="seedance",
            tags="test",
            created_at=now,
        )
    )
    db_session.add(
        ScheduledTaskRun(
            id="run-1",
            task_id=1,
            user_id=phone_user.id,
            title="定时任务结果",
            status="completed",
            result_payload={"videos": [{"url": "https://cdn.example.com/run-video.mp4"}]},
            result_text="预览链接：https://cdn.example.com/run-image.jpg",
            created_at=now,
            updated_at=now,
            finished_at=now,
        )
    )
    db_session.add(
        H5ChatMessage(
            id="msg-1",
            user_id=phone_user.id,
            content="生成素材",
            status="completed",
            reply_text="生成完成 https://cdn.example.com/h5-video.mp4",
            created_at=now,
            updated_at=now,
            finished_at=now,
        )
    )
    db_session.add(
        H5ChatEvent(
            message_id="msg-1",
            user_id=phone_user.id,
            event_type="final",
            payload={"saved_assets": [{"url": "https://cdn.example.com/event-image.png"}]},
            created_at=now,
        )
    )
    db_session.commit()

    app = FastAPI()
    app.include_router(mobile_router, prefix="")

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    def _get_current_user_override():
        s = db_session_factory()
        try:
            return s.query(User).filter(User.id == phone_user.id).first()
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _get_current_user_override
    client = TestClient(app)

    res = client.get(f"/api/mobile/downloads?device_id={DEVICE_ID}&limit=20")

    assert res.status_code == 200
    data = res.json()
    urls = {item["url"] for item in data["items"]}
    assert "https://cdn.example.com/asset-video-1.mp4" in urls
    assert "https://cdn.example.com/run-video.mp4" in urls
    assert "https://cdn.example.com/run-image.jpg" in urls
    assert "https://cdn.example.com/h5-video.mp4" in urls
    assert "https://cdn.example.com/event-image.png" in urls
    assert all(item["preview_url"].startswith("http://testserver/api/h5-chat/media?") for item in data["items"])
    assert all(item["download_url"].startswith("http://testserver/api/h5-chat/media?") for item in data["items"])


def test_wechat_login_creates_temporary_user(mobile_client):
    res = mobile_client.post(
        "/api/mobile/wechat-login",
        json={"code": "wx-login-code", "device_id": DEVICE_ID, "platform": "wechat_miniprogram"},
    )

    assert res.status_code == 200
    data = res.json()
    assert data["access_token"]
    assert data["openid_bound"] is True
    assert data["needs_phone_bind"] is True


def test_wechat_helpers_parse_text_plain_json():
    import httpx

    from backend.app.api.auth import _response_json_any_content_type
    from backend.app.api.mobile_client import _wechat_json

    resp = httpx.Response(
        200,
        headers={"content-type": "text/plain"},
        text='{"openid":"openid_text_plain","errcode":0}',
    )

    assert _wechat_json(resp)["openid"] == "openid_text_plain"
    assert _response_json_any_content_type(resp)["openid"] == "openid_text_plain"
