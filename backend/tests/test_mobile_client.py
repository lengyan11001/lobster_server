from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


PHONE = "13800138000"
PHONE_EMAIL = f"{PHONE}@sms.lobster.local"
NEW_PHONE = "13900139000"
NEW_PHONE_EMAIL = f"{NEW_PHONE}@sms.lobster.local"
DEVICE_ID = "mp_test_device_001"


def _put_mobile_sms_code(db_session_factory, phone: str, code: str = "123456") -> None:
    from backend.app.api.auth import SMS_CODE_TTL_SEC, _create_auth_challenge

    with db_session_factory() as s:
        _create_auth_challenge(s, kind="sms", target=phone, answer=code, ttl_seconds=SMS_CODE_TTL_SEC)


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
    res = mobile_client.get(f"/api/mobile/phone/status?phone={NEW_PHONE}")

    assert res.status_code == 200
    data = res.json()
    assert data["registered"] is False
    assert "验证码通过后会自动创建账号" in data["message"]


def test_send_mobile_sms_allows_new_phone(mobile_client, monkeypatch):
    from backend.app.api import mobile_client as mobile_module

    sent: list[str] = []
    monkeypatch.setattr(mobile_module, "_send_mobile_sms_code", lambda db, mobile: sent.append(mobile))

    res = mobile_client.post("/api/mobile/sms/send", json={"phone": NEW_PHONE})

    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert sent == [NEW_PHONE]


def test_bind_mobile_device_links_wechat_session_to_phone_without_merging_user(mobile_client, db_session_factory, mobile_users):
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
    assert data["user_id"] == temp_user.id
    assert data["phone_user_id"] == phone_user.id
    assert data["phone"] == PHONE
    assert data["phone_verified"] is True
    assert data["access_token"]

    from backend.app.models import MobileDeviceBinding, User

    with db_session_factory() as s:
        bound_user = s.query(User).filter(User.id == phone_user.id).first()
        old_temp = s.query(User).filter(User.id == temp_user.id).first()
        binding = s.query(MobileDeviceBinding).filter(MobileDeviceBinding.device_id == DEVICE_ID).first()
        assert bound_user.wechat_openid is None
        assert old_temp.wechat_openid == "openid_test"
        assert binding.user_id == temp_user.id
        assert binding.phone == PHONE


def test_bind_mobile_device_with_sms_code(mobile_client, db_session_factory, mobile_users):
    phone_user, temp_user = mobile_users
    _put_mobile_sms_code(db_session_factory, PHONE, "123456")

    res = mobile_client.post(
        "/api/mobile/devices/bind",
        json={
            "phone": PHONE,
            "sms_code": "123456",
            "device_id": DEVICE_ID,
            "platform": "wechat_miniprogram",
            "display_name": "微信小程序",
        },
    )

    assert res.status_code == 200
    data = res.json()
    assert data["user_id"] == temp_user.id
    assert data["phone_user_id"] == phone_user.id
    assert data["phone"] == PHONE
    assert data["phone_verified"] is True

    from backend.app.models import MobileDeviceBinding

    with db_session_factory() as s:
        binding = s.query(MobileDeviceBinding).filter(MobileDeviceBinding.device_id == DEVICE_ID).first()
        assert binding.user_id == temp_user.id
        assert binding.phone == PHONE


def test_bind_mobile_device_with_sms_code_creates_new_phone_user(mobile_client, db_session_factory, mobile_users):
    _, temp_user = mobile_users
    _put_mobile_sms_code(db_session_factory, NEW_PHONE, "654321")

    res = mobile_client.post(
        "/api/mobile/devices/bind",
        json={
            "phone": NEW_PHONE,
            "sms_code": "654321",
            "device_id": DEVICE_ID,
            "platform": "wechat_miniprogram",
            "display_name": "微信小程序",
        },
    )

    assert res.status_code == 200
    data = res.json()
    assert data["user_id"] == temp_user.id
    assert data["phone"] == NEW_PHONE
    assert data["phone_verified"] is True
    assert data["created_user"] is True
    assert data["access_token"]

    from backend.app.models import MobileDeviceBinding, SkillUnlock, User

    with db_session_factory() as s:
        created = s.query(User).filter(User.email == NEW_PHONE_EMAIL).first()
        old_temp = s.query(User).filter(User.id == temp_user.id).first()
        binding = s.query(MobileDeviceBinding).filter(MobileDeviceBinding.device_id == DEVICE_ID).first()
        unlocks = s.query(SkillUnlock).filter(SkillUnlock.user_id == created.id).all()
        assert created is not None
        assert created.hashed_password
        assert created.wechat_openid is None
        assert old_temp.wechat_openid == "openid_test"
        assert binding.user_id == temp_user.id
        assert binding.phone == NEW_PHONE
        assert {row.package_id for row in unlocks} >= {"sutui_mcp", "douyin_publish"}


def test_bind_mobile_allows_same_phone_for_another_wechat(mobile_client, db_session_factory, mobile_users):
    phone_user, temp_user = mobile_users
    with db_session_factory() as s:
        user = s.query(type(phone_user)).filter(type(phone_user).id == phone_user.id).first()
        user.wechat_openid = "openid_old_wechat"
        s.commit()

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
    assert data["user_id"] == temp_user.id
    assert data["phone_user_id"] == phone_user.id
    assert data["phone"] == PHONE


def test_mobile_devices_resolve_online_devices_by_bound_phone(db_session, db_session_factory, mobile_users, monkeypatch):
    from backend.app.api.auth import get_current_user
    from backend.app.api.mobile_client import router as mobile_router
    from backend.app.core.config import settings
    from backend.app.db import get_db
    from backend.app.models import H5ChatDevicePresence, MobileDeviceBinding, User

    phone_user, temp_user = mobile_users
    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)
    now = datetime.utcnow()
    db_session.add(
        MobileDeviceBinding(
            user_id=temp_user.id,
            phone=PHONE,
            device_id=DEVICE_ID,
            platform="wechat_miniprogram",
            display_name="微信小程序",
            created_at=now,
            last_seen_at=now,
        )
    )
    db_session.add(
        H5ChatDevicePresence(
            user_id=phone_user.id,
            installation_id="online-installation-1",
            display_name="电脑端 online",
            created_at=now,
            last_seen_at=now,
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
            return s.query(User).filter(User.id == temp_user.id).first()
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _get_current_user_override
    client = TestClient(app)

    res = client.get("/api/mobile/devices")

    assert res.status_code == 200
    data = res.json()
    assert data["online_available"] is True
    assert data["online_devices"][0]["installation_id"] == "online-installation-1"


def test_h5_messages_from_wechat_session_are_owned_by_bound_phone_user(db_session, db_session_factory, mobile_users, monkeypatch):
    from backend.app.api.auth import get_current_user
    from backend.app.api.h5_chat import router as h5_router
    from backend.app.core.config import settings
    from backend.app.db import get_db
    from backend.app.models import H5ChatDevicePresence, H5ChatMessage, MobileDeviceBinding, User

    phone_user, temp_user = mobile_users
    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)
    now = datetime.utcnow()
    db_session.add(
        MobileDeviceBinding(
            user_id=temp_user.id,
            phone=PHONE,
            device_id=DEVICE_ID,
            platform="wechat_miniprogram",
            created_at=now,
            last_seen_at=now,
        )
    )
    db_session.add(
        H5ChatDevicePresence(
            user_id=phone_user.id,
            installation_id="online-installation-1",
            display_name="电脑端 online",
            created_at=now,
            last_seen_at=now,
        )
    )
    db_session.commit()

    app = FastAPI()
    app.include_router(h5_router, prefix="")

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    def _current_user(user_id):
        def _get_current_user_override():
            s = db_session_factory()
            try:
                return s.query(User).filter(User.id == user_id).first()
            finally:
                s.close()

        return _get_current_user_override

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _current_user(temp_user.id)
    client = TestClient(app)

    created = client.post("/api/h5-chat/messages", json={"content": "生成一张海报"})
    assert created.status_code == 200
    message_id = created.json()["message"]["id"]

    with db_session_factory() as s:
        msg = s.query(H5ChatMessage).filter(H5ChatMessage.id == message_id).first()
        assert msg.user_id == phone_user.id

    app.dependency_overrides[get_current_user] = _current_user(phone_user.id)
    pending = client.get("/api/h5-chat/pending", headers={"X-Installation-Id": "online-installation-1"})

    assert pending.status_code == 200
    assert pending.json()["items"][0]["id"] == message_id


def test_asset_upload_from_wechat_session_is_owned_by_bound_phone_user(db_session, db_session_factory, mobile_users, monkeypatch):
    from backend.app.api.auth import get_current_user
    from backend.app.api.assets import router as assets_router
    from backend.app.core.config import settings
    from backend.app.db import get_db
    from backend.app.models import Asset, MobileDeviceBinding, User

    phone_user, temp_user = mobile_users
    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)
    now = datetime.utcnow()
    db_session.add(
        MobileDeviceBinding(
            user_id=temp_user.id,
            phone=PHONE,
            device_id=DEVICE_ID,
            platform="wechat_miniprogram",
            created_at=now,
            last_seen_at=now,
        )
    )
    db_session.commit()

    from backend.app.api import assets as assets_module

    monkeypatch.setattr(
        assets_module,
        "_save_bytes_or_tos",
        lambda data, ext, content_type="": ("asset-test", f"assets/asset-test{ext}", len(data), "https://cdn.example.com/asset-test.png"),
    )

    app = FastAPI()
    app.include_router(assets_router, prefix="")

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
    client = TestClient(app)

    res = client.post("/api/assets/upload", files={"file": ("demo.png", b"fakepng", "image/png")})

    assert res.status_code == 200
    with db_session_factory() as s:
        asset = s.query(Asset).filter(Asset.asset_id == "asset-test").first()
        assert asset.user_id == phone_user.id


def test_scheduled_task_from_wechat_session_targets_bound_phone_user(db_session, db_session_factory, mobile_users, monkeypatch):
    from backend.app.api.auth import get_current_user
    from backend.app.api.scheduled_tasks import router as scheduled_router
    from backend.app.core.config import settings
    from backend.app.db import get_db
    from backend.app.models import MobileDeviceBinding, ScheduledTask, User

    phone_user, temp_user = mobile_users
    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)
    now = datetime.utcnow()
    db_session.add(
        MobileDeviceBinding(
            user_id=temp_user.id,
            phone=PHONE,
            device_id=DEVICE_ID,
            platform="wechat_miniprogram",
            created_at=now,
            last_seen_at=now,
        )
    )
    db_session.commit()

    app = FastAPI()
    app.include_router(scheduled_router, prefix="")

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
    client = TestClient(app)

    res = client.post(
        "/api/scheduled-tasks/tasks",
        headers={"X-Installation-Id": "online-installation-1"},
        json={
            "title": "手机端一次性任务",
            "task_kind": "chat_message",
            "content": "帮我生成短视频文案",
            "schedule_type": "once",
        },
    )

    assert res.status_code == 200
    task_id = res.json()["task"]["id"]
    with db_session_factory() as s:
        task = s.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        assert task.user_id == phone_user.id
        assert task.created_by_user_id == temp_user.id


def test_wrong_mobile_sms_code_does_not_consume_challenge(mobile_client, db_session_factory, mobile_users):
    phone_user, _ = mobile_users
    _put_mobile_sms_code(db_session_factory, PHONE, "123456")

    bad = mobile_client.post(
        "/api/mobile/devices/bind",
        json={
            "phone": PHONE,
            "sms_code": "000000",
            "device_id": DEVICE_ID,
            "platform": "wechat_miniprogram",
        },
    )

    assert bad.status_code == 400

    ok = mobile_client.post(
        "/api/mobile/devices/bind",
        json={
            "phone": PHONE,
            "sms_code": "123456",
            "device_id": DEVICE_ID,
            "platform": "wechat_miniprogram",
        },
    )

    assert ok.status_code == 200
    data = ok.json()
    assert data["user_id"] != phone_user.id
    assert data["phone_verified"] is True


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
    assert all(item["preview_url"].startswith("https://cdn.example.com/") for item in data["items"])
    assert all(item["download_url"].startswith("https://cdn.example.com/") for item in data["items"])
    assert all(item["proxy_preview_url"].startswith("http://testserver/api/h5-chat/media?") for item in data["items"])
    assert all(item["proxy_download_url"].startswith("http://testserver/api/h5-chat/media?") for item in data["items"])


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


def test_wechat_login_binds_share_agent_for_unclaimed_user(mobile_client, db_session_factory):
    from backend.app.models import User

    with db_session_factory() as s:
        agent = User(
            email="13800138099@sms.lobster.local",
            hashed_password="x",
            credits=Decimal("0"),
            role="user",
            preferred_model="sutui",
            is_agent=True,
            agent_level=1,
            created_at=datetime.utcnow(),
        )
        s.add(agent)
        s.commit()
        agent_id = agent.id

    res = mobile_client.post(
        "/api/mobile/wechat-login",
        json={
            "code": "wx-login-code",
            "device_id": DEVICE_ID,
            "platform": "wechat_miniprogram",
            "ref_agent_user_id": agent_id,
        },
    )

    assert res.status_code == 200
    data = res.json()
    assert data["parent_user_id"] == agent_id
    assert data["ref_agent_bound"] is True

    with db_session_factory() as s:
        created = s.query(User).filter(User.wechat_openid == "openid_login").first()
        assert created.parent_user_id == agent_id


def test_phone_bind_binds_share_agent_without_overwriting_existing_parent(mobile_client, db_session_factory, mobile_users):
    from backend.app.models import User

    phone_user, _ = mobile_users
    with db_session_factory() as s:
        old_agent = User(
            email="13800138098@sms.lobster.local",
            hashed_password="x",
            credits=Decimal("0"),
            role="user",
            preferred_model="sutui",
            is_agent=True,
            agent_level=1,
            created_at=datetime.utcnow(),
        )
        new_agent = User(
            email="13800138097@sms.lobster.local",
            hashed_password="x",
            credits=Decimal("0"),
            role="user",
            preferred_model="sutui",
            is_agent=True,
            agent_level=1,
            created_at=datetime.utcnow(),
        )
        s.add_all([old_agent, new_agent])
        s.flush()
        user = s.query(User).filter(User.id == phone_user.id).first()
        user.parent_user_id = old_agent.id
        s.commit()
        old_agent_id = old_agent.id
        new_agent_id = new_agent.id

    _put_mobile_sms_code(db_session_factory, PHONE, "123456")

    res = mobile_client.post(
        "/api/mobile/devices/bind",
        json={
            "phone": PHONE,
            "sms_code": "123456",
            "device_id": DEVICE_ID,
            "platform": "wechat_miniprogram",
            "ref_agent_user_id": new_agent_id,
        },
    )

    assert res.status_code == 200
    data = res.json()
    assert data["parent_user_id"] == old_agent_id
    assert data["ref_agent_bound"] is False


def test_share_bind_endpoint_binds_logged_in_user(mobile_client, db_session_factory, mobile_users):
    from backend.app.models import User

    _, temp_user = mobile_users
    with db_session_factory() as s:
        agent = User(
            email="13800138096@sms.lobster.local",
            hashed_password="x",
            credits=Decimal("0"),
            role="user",
            preferred_model="sutui",
            is_agent=True,
            agent_level=2,
            created_at=datetime.utcnow(),
        )
        s.add(agent)
        s.commit()
        agent_id = agent.id

    res = mobile_client.post("/api/mobile/share-bind", json={"ref_agent_user_id": agent_id})

    assert res.status_code == 200
    data = res.json()
    assert data["parent_user_id"] == agent_id
    assert data["ref_agent_bound"] is True

    with db_session_factory() as s:
        user = s.query(User).filter(User.id == temp_user.id).first()
        assert user.parent_user_id == agent_id


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
