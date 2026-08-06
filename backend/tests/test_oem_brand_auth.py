from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi import Depends, HTTPException, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, inspect, text


PHONE = "13800138009"
PHONE_EMAIL = f"{PHONE}@sms.lobster.local"


def test_brand_schema_backfills_legacy_users(tmp_path):
    from backend.app.services.brand_context import ensure_user_brand_schema

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}", future=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255) NOT NULL)"))
        connection.execute(text("INSERT INTO users (id, email) VALUES (1, 'legacy@example.com')"))

    ensure_user_brand_schema(engine)

    assert "brand_mark" in {column["name"] for column in inspect(engine).get_columns("users")}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT brand_mark FROM users WHERE id = 1")).scalar_one() == "bihuo"


def _auth_client(db_session_factory, monkeypatch):
    from backend.app.api.auth import router as auth_router
    from backend.app.core.config import settings
    from backend.app.db import get_db

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)
    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")

    def _get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def _put_brand_sms_code(db_session_factory, brand_mark: str, code: str):
    from backend.app.api.auth import _create_auth_challenge, _sms_challenge_target

    with db_session_factory() as session:
        _create_auth_challenge(
            session,
            kind="sms",
            target=_sms_challenge_target(PHONE, brand_mark),
            answer=code,
            ttl_seconds=600,
        )
        session.commit()


def test_same_phone_registers_as_two_brand_users(db_session_factory, monkeypatch):
    from backend.app.api.auth import verify_password
    from backend.app.models import User

    client = _auth_client(db_session_factory, monkeypatch)
    _put_brand_sms_code(db_session_factory, "bihuo", "111111")
    bihuo = client.post(
        "/auth/register-phone",
        headers={"X-Lobster-Brand": "bihuo"},
        json={"phone": PHONE, "code": "111111", "brand_mark": "bihuo"},
    )
    _put_brand_sms_code(db_session_factory, "daka", "222222")
    daka = client.post(
        "/auth/register-phone",
        headers={"X-Lobster-Brand": "daka"},
        json={"phone": PHONE, "code": "222222", "brand_mark": "daka"},
    )

    assert bihuo.status_code == 200
    assert daka.status_code == 200, daka.text
    assert bihuo.json()["access_token"] != daka.json()["access_token"]
    with db_session_factory() as session:
        rows = session.query(User).filter(User.brand_mark.in_(["bihuo", "daka"])).all()
        matching = [row for row in rows if row.email.startswith(PHONE)]
        assert {(row.brand_mark, row.email) for row in matching} == {
            ("bihuo", PHONE_EMAIL),
            ("daka", f"{PHONE}+brand-daka@sms.lobster.local"),
        }
        assert all(row.password_initialized for row in matching)
        assert all(verify_password(PHONE[-6:], row.hashed_password) for row in matching)


def test_password_login_is_scoped_by_brand(db_session, db_session_factory, monkeypatch):
    from backend.app.api.auth import get_password_hash
    from backend.app.models import User

    db_session.add_all(
        [
            User(
                email=PHONE_EMAIL,
                hashed_password=get_password_hash("bihuo-pass"),
                credits=Decimal("1"),
                role="user",
                preferred_model="sutui",
                brand_mark="bihuo",
                created_at=datetime.utcnow(),
            ),
            User(
                email=f"{PHONE}+brand-daka@sms.lobster.local",
                hashed_password=get_password_hash("daka-pass"),
                credits=Decimal("1"),
                role="user",
                preferred_model="sutui",
                brand_mark="daka",
                created_at=datetime.utcnow(),
            ),
        ]
    )
    db_session.commit()
    client = _auth_client(db_session_factory, monkeypatch)

    wrong_brand = client.post(
        "/auth/login-phone-password",
        headers={"X-Lobster-Brand": "daka"},
        json={"phone": PHONE, "password": "bihuo-pass", "brand_mark": "daka"},
    )
    correct_brand = client.post(
        "/auth/login-phone-password",
        headers={"X-Lobster-Brand": "daka"},
        json={"phone": PHONE, "password": "daka-pass", "brand_mark": "daka"},
    )

    assert wrong_brand.status_code == 400
    assert correct_brand.status_code == 200


def test_brand_token_rejected_under_other_brand(db_session, db_session_factory, monkeypatch):
    from backend.app.api.auth import access_token_claims, create_access_token, get_password_hash
    from backend.app.models import User

    user = User(
        email=f"{PHONE}+brand-daka@sms.lobster.local",
        hashed_password=get_password_hash("daka-pass"),
        credits=Decimal("1"),
        role="user",
        preferred_model="sutui",
        brand_mark="daka",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(access_token_claims(user))
    client = _auth_client(db_session_factory, monkeypatch)

    rejected = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}", "X-Lobster-Brand": "bihuo"},
    )
    accepted = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}", "X-Lobster-Brand": "daka"},
    )
    accepted_without_explicit_brand = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert accepted_without_explicit_brand.status_code == 200
    assert accepted.json()["brand_mark"] == "daka"
    assert accepted.json()["email"] == PHONE_EMAIL


def test_oem_background_heartbeat_accepts_signed_token_brand_without_header(
    db_session, db_session_factory, monkeypatch
):
    from backend.app.api.auth import access_token_claims, create_access_token, get_password_hash
    from backend.app.api.h5_chat import router as h5_chat_router
    from backend.app.db import get_db
    from backend.app.models import H5ChatDevicePresence, User, UserInstallation

    user = User(
        email=f"{PHONE}+brand-daka@sms.lobster.local",
        hashed_password=get_password_hash("daka-pass"),
        credits=Decimal("1"),
        role="user",
        preferred_model="sutui",
        brand_mark="daka",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(access_token_claims(user))

    app = FastAPI()
    app.include_router(h5_chat_router)

    def _get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db_override
    client = TestClient(app)
    response = client.post(
        "/api/h5-chat/device-heartbeat",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Installation-Id": "same-device",
        },
        json={"display_name": "local-online"},
    )

    assert response.status_code == 200, response.text
    with db_session_factory() as session:
        presence = (
            session.query(H5ChatDevicePresence)
            .filter(
                H5ChatDevicePresence.user_id == user.id,
                H5ChatDevicePresence.installation_id == "same-device",
            )
            .one()
        )
        assert presence.display_name == "local-online"
        installation_ids = {
            row.installation_id
            for row in session.query(UserInstallation).filter(UserInstallation.user_id == user.id).all()
        }
        assert installation_ids == {"daka--same-device"}


def test_h5_message_list_can_skip_event_payloads(db_session, db_session_factory):
    from backend.app.api.auth import access_token_claims, create_access_token, get_password_hash
    from backend.app.api.h5_chat import router as h5_chat_router
    from backend.app.db import get_db
    from backend.app.models import H5ChatEvent, H5ChatMessage, User

    user = User(
        email=f"{PHONE}+brand-daka@sms.lobster.local",
        hashed_password=get_password_hash("daka-pass"),
        credits=Decimal("1"),
        role="user",
        preferred_model="sutui",
        brand_mark="daka",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.flush()
    message = H5ChatMessage(id="compact-history-message", user_id=user.id, content="hello")
    db_session.add(message)
    db_session.add(
        H5ChatEvent(
            message_id=message.id,
            user_id=user.id,
            event_type="final",
            payload={"large_result": "payload"},
        )
    )
    db_session.commit()
    token = create_access_token(access_token_claims(user))

    app = FastAPI()
    app.include_router(h5_chat_router)

    def _get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db_override
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    compact = client.get("/api/h5-chat/messages?limit=40&include_events=false", headers=headers)
    detailed = client.get("/api/h5-chat/messages?limit=40&include_events=true", headers=headers)

    assert compact.status_code == 200, compact.text
    assert compact.json()["messages"][0]["events"] == []
    assert detailed.status_code == 200, detailed.text
    assert detailed.json()["messages"][0]["events"][0]["payload"] == {"large_result": "payload"}


def test_oem_scheduled_worker_callbacks_accept_signed_token_brand_without_header(
    db_session, db_session_factory, monkeypatch
):
    from backend.app.api.auth import access_token_claims, create_access_token, get_password_hash
    from backend.app.api.scheduled_tasks import router as scheduled_tasks_router
    from backend.app.db import get_db
    from backend.app.models import ScheduledTaskRun, User

    user = User(
        email=f"{PHONE}+brand-daka@sms.lobster.local",
        hashed_password=get_password_hash("daka-pass"),
        credits=Decimal("1"),
        role="user",
        preferred_model="sutui",
        brand_mark="daka",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add_all(
        [
            ScheduledTaskRun(
                id="oem-event-run",
                user_id=user.id,
                title="event",
                status="processing",
                claimed_by_installation_id="same-device",
            ),
            ScheduledTaskRun(
                id="oem-complete-run",
                user_id=user.id,
                title="complete",
                status="processing",
                claimed_by_installation_id="same-device",
            ),
            ScheduledTaskRun(
                id="oem-publish-run",
                user_id=user.id,
                title="publish",
                status="completed",
                result_payload={
                    "publish_draft": {
                        "status": "processing",
                        "claimed_by_installation_id": "same-device",
                    }
                },
            ),
        ]
    )
    db_session.commit()
    token = create_access_token(access_token_claims(user))

    app = FastAPI()
    app.include_router(scheduled_tasks_router)

    def _get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db_override
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Installation-Id": "same-device",
    }

    event = client.post(
        "/api/scheduled-tasks/runs/oem-event-run/event",
        headers=headers,
        json={"type": "progress", "payload": {"text": "running"}},
    )
    complete = client.post(
        "/api/scheduled-tasks/runs/oem-complete-run/complete",
        headers=headers,
        json={"result_text": "done", "result_payload": {"ok": True}},
    )
    publish = client.post(
        "/api/scheduled-tasks/runs/oem-publish-run/publish-complete",
        headers=headers,
        json={"publish_result": {"ok": True}},
    )

    assert event.status_code == 200, event.text
    assert complete.status_code == 200, complete.text
    assert publish.status_code == 200, publish.text


def test_ip_content_internal_llm_request_forwards_brand(monkeypatch):
    from backend.app.api import ip_content_studio as module
    from backend.app.api.auth import create_access_token

    captured = {}

    async def _fake_post(*, payload, headers, attempts=3, timeout_seconds=150.0):
        captured.update(headers)
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"items":[{"title":"title","hook":"hook","body":"body","cta":"cta"}]}'
                    }
                }
            ]
        }

    monkeypatch.setattr(module, "_post_llm_with_retry", _fake_post)
    token = create_access_token({"sub": "99", "brand_mark": "daka"})
    result = asyncio.run(
        module._call_ip_content_llm(
            auth_token=f"Bearer {token}",
            brand_mark="daka",
            task="industry_hot_oral",
            platform="douyin",
            count=1,
            rows=[],
            memories=[],
            extra_requirements="",
            fallback_sources=[{"title": "seed", "description": "source"}],
        )
    )

    assert result["drafts"]
    assert captured["X-Lobster-Brand"] == "daka"


def test_oem_nonstandard_token_transports_share_brand_validation(
    db_session, db_session_factory, monkeypatch
):
    from backend.app.api import comfly_proxy, h5_chat, h5_voice
    from backend.app.api.auth import (
        access_token_claims,
        create_access_token,
        get_messenger_user_id,
        get_password_hash,
    )
    from backend.app.db import get_db
    from backend.app.models import User

    user = User(
        email=f"{PHONE}+brand-daka@sms.lobster.local",
        hashed_password=get_password_hash("daka-pass"),
        credits=Decimal("1"),
        role="user",
        preferred_model="sutui",
        brand_mark="daka",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(access_token_claims(user))

    assert h5_chat._user_from_query_token(db_session, token).id == user.id
    assert h5_voice._user_from_query_token(db_session, token).id == user.id
    with pytest.raises(HTTPException) as media_error:
        h5_chat._user_from_query_token(db_session, token, "bihuo")
    assert media_error.value.status_code == 403

    monkeypatch.setattr(comfly_proxy, "SessionLocal", db_session_factory)
    app = FastAPI()

    def _get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    @app.get("/messenger-oem-test")
    def _messenger_oem_test(user_id: int = Depends(get_messenger_user_id)):
        return {"user_id": user_id}

    @app.get("/comfly-oem-test")
    def _comfly_oem_test(request: Request):
        request_user_id, billing_user_id = comfly_proxy._resolve_proxy_user_ids_from_request(request)
        return {"request_user_id": request_user_id, "billing_user_id": billing_user_id}

    app.dependency_overrides[get_db] = _get_db_override
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {token}"}

    assert client.get("/messenger-oem-test", headers=auth).status_code == 200
    assert client.get("/comfly-oem-test", headers=auth).status_code == 200
    assert client.get(
        "/messenger-oem-test", headers={**auth, "X-Lobster-Brand": "bihuo"}
    ).status_code == 403
    assert client.get(
        "/comfly-oem-test", headers={**auth, "X-Lobster-Brand": "bihuo"}
    ).status_code == 403


def test_disabled_brand_rejects_login(db_session, db_session_factory, monkeypatch):
    from backend.app.models import BrandConfig

    db_session.add(BrandConfig(mark="daka", display_name="大咖AI员工", enabled=False, config={}))
    db_session.commit()
    client = _auth_client(db_session_factory, monkeypatch)
    response = client.post(
        "/auth/login-phone-password",
        headers={"X-Lobster-Brand": "daka"},
        json={"phone": PHONE, "password": "anything", "brand_mark": "daka"},
    )
    assert response.status_code == 403


def test_miniprogram_openid_is_scoped_by_brand(db_session_factory, monkeypatch):
    from backend.app.api import mobile_client as mobile_module
    from backend.app.api.mobile_client import router as mobile_router
    from backend.app.db import get_db
    from backend.app.models import User

    monkeypatch.setattr(mobile_module, "_exchange_wechat_login_code", lambda _: "same-openid")
    app = FastAPI()
    app.include_router(mobile_router)

    def _get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db_override
    client = TestClient(app)
    bihuo = client.post(
        "/api/mobile/wechat-login",
        headers={"X-Lobster-Brand": "bihuo"},
        json={"code": "code-1", "brand_mark": "bihuo"},
    )
    daka = client.post(
        "/api/mobile/wechat-login",
        headers={"X-Lobster-Brand": "daka"},
        json={"code": "code-2", "brand_mark": "daka"},
    )

    assert bihuo.status_code == 200
    assert daka.status_code == 200
    assert bihuo.json()["user_id"] != daka.json()["user_id"]
    with db_session_factory() as session:
        rows = session.query(User).filter(User.wechat_openid == "same-openid").all()
        assert {row.brand_mark for row in rows} == {"bihuo", "daka"}


def test_admin_can_list_filter_and_open_users_across_brands(db_session, db_session_factory, monkeypatch):
    from backend.app.api.admin import router as admin_router
    from backend.app.api.auth import get_password_hash
    from backend.app.core.config import settings
    from backend.app.db import get_db
    from backend.app.models import User

    monkeypatch.setattr(settings, "lobster_admin_username", "admin", raising=False)
    monkeypatch.setattr(settings, "lobster_admin_password", "oem-test-password", raising=False)
    db_session.add_all(
        [
            User(
                email="bihuo-user@example.com",
                hashed_password=get_password_hash("password-1"),
                credits=Decimal("1"),
                role="user",
                preferred_model="sutui",
                brand_mark="bihuo",
                created_at=datetime.utcnow(),
            ),
            User(
                email="daka-user+brand-daka@example.com",
                hashed_password=get_password_hash("password-2"),
                credits=Decimal("1"),
                role="user",
                preferred_model="sutui",
                brand_mark="daka",
                created_at=datetime.utcnow(),
            ),
        ]
    )
    db_session.commit()
    bihuo_user = db_session.query(User).filter(User.brand_mark == "bihuo").first()
    daka_user = db_session.query(User).filter(User.brand_mark == "daka").first()

    app = FastAPI()
    app.include_router(admin_router)

    def _get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db_override
    client = TestClient(app)
    headers = {
        "X-Admin-Token": "lobster-admin-oem-test-password",
        "X-Lobster-Brand": "bihuo",
    }

    bihuo_login = client.post(
        "/admin/api/login",
        json={"username": "admin", "password": "oem-test-password", "brand_mark": "bihuo"},
    )
    daka_login = client.post(
        "/admin/api/login",
        json={"username": "admin", "password": "oem-test-password", "brand_mark": "daka"},
    )
    daka_token_access = client.get(
        "/admin/api/users",
        headers={
            "X-Admin-Token": "lobster-admin-oem-test-password",
            "X-Lobster-Brand": "daka",
        },
    )
    users = client.get("/admin/api/users", headers=headers)
    daka_users = client.get("/admin/api/users?brand=daka", headers=headers)
    bihuo_detail = client.get(f"/admin/api/user/{bihuo_user.id}", headers=headers)
    daka_owner = client.get(
        f"/admin/api/ip-content/template-options?owner_user_id={daka_user.id}",
        headers=headers,
    )
    stats = client.get("/admin/api/stats?days=30", headers=headers)

    assert bihuo_login.status_code == 200
    assert bihuo_login.json()["role"] == "admin"
    assert daka_login.status_code == 401
    assert daka_token_access.status_code == 403
    assert users.status_code == 200
    assert [item["brand_mark"] for item in users.json()["users"]] == ["daka", "bihuo"]
    assert daka_users.status_code == 200
    assert [item["brand_mark"] for item in daka_users.json()["users"]] == ["daka"]
    assert daka_users.json()["users"][0]["email"] == "daka-user@example.com"
    assert bihuo_detail.status_code == 200
    assert bihuo_detail.json()["user"]["brand_mark"] == "bihuo"
    assert daka_owner.status_code == 200
    assert stats.status_code == 200
    assert stats.json()["overview"]["total_users"] == 2


def test_admin_brand_is_driven_by_url_without_brand_selectors():
    html = (Path(__file__).resolve().parents[1] / "app" / "static" / "admin.html").read_text(encoding="utf-8")

    assert 'id="loginBrandSelect"' not in html
    assert 'id="adminBrandSelect"' not in html
    assert "new URLSearchParams(location.search).get('brand')" in html
    assert "new Set(['bihuo', 'daka', 'jinghai', 'hikong'])" in html
    assert "ADMIN_BRAND_MARKS.has(REQUESTED_BRAND_MARK) ? REQUESTED_BRAND_MARK : 'bihuo'" in html
    assert "jinghai: { name: '鲸海AI员工'" in html
    assert "icon32: '/client/oem/jinghai/icon_32_v3.png'" in html
    assert "hikong: { name: '海康AI智能体'" in html
    assert "icon32: '/client/oem/hikong/icon_32_v3.png'" in html


def test_admin_first_html_is_rendered_with_requested_brand(db_session_factory):
    from backend.app.api.admin import router as admin_router
    from backend.app.db import get_db

    app = FastAPI()
    app.include_router(admin_router)

    def _get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db_override
    response = TestClient(app).get("/admin?brand=jinghai")

    assert response.status_code == 200
    assert '<html lang="zh-CN" data-brand="jinghai">' in response.text
    assert "<title>鲸海AI员工管理后台</title>" in response.text
    assert 'id="loginBrandIcon" src="/client/oem/jinghai/icon_64_v3.png"' in response.text
    assert 'id="sidebarBrandIcon" src="/client/oem/jinghai/icon_32_v3.png"' in response.text
    assert "__ADMIN_BRAND_" not in response.text


def test_agent_user_list_remains_scoped_to_its_brand(db_session, db_session_factory, monkeypatch):
    from backend.app.api.admin import router as admin_router
    from backend.app.api.auth import get_password_hash
    from backend.app.core.config import settings
    from backend.app.db import get_db
    from backend.app.models import User

    monkeypatch.setattr(settings, "lobster_admin_username", "admin", raising=False)
    monkeypatch.setattr(settings, "lobster_admin_password", "oem-test-password", raising=False)
    agent = User(
        email="13900000000+brand-daka@sms.lobster.local",
        hashed_password=get_password_hash("agent-password"),
        credits=Decimal("1"),
        role="user",
        preferred_model="sutui",
        brand_mark="daka",
        is_agent=True,
        agent_level=1,
        created_at=datetime.utcnow(),
    )
    db_session.add(agent)
    db_session.flush()
    db_session.add_all(
        [
            User(
                email="daka-child@example.com",
                hashed_password=get_password_hash("password-1"),
                credits=Decimal("1"),
                role="user",
                preferred_model="sutui",
                brand_mark="daka",
                parent_user_id=agent.id,
                created_at=datetime.utcnow(),
            ),
            User(
                email="bihuo-child@example.com",
                hashed_password=get_password_hash("password-2"),
                credits=Decimal("1"),
                role="user",
                preferred_model="sutui",
                brand_mark="bihuo",
                parent_user_id=agent.id,
                created_at=datetime.utcnow(),
            ),
        ]
    )
    db_session.commit()

    app = FastAPI()
    app.include_router(admin_router)

    def _get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db_override
    client = TestClient(app)
    login = client.post(
        "/admin/api/login",
        json={"username": "13900000000", "password": "agent-password", "brand_mark": "daka"},
    )
    assert login.status_code == 200
    headers = {
        "X-Admin-Token": login.json()["token"],
        "X-Lobster-Brand": "daka",
    }

    users = client.get("/admin/api/users?brand=bihuo", headers=headers)

    assert users.status_code == 200
    assert [item["email"] for item in users.json()["users"]] == [
        "13900000000@sms.lobster.local",
        "daka-child@example.com",
    ]
    assert {item["brand_mark"] for item in users.json()["users"]} == {"daka"}
