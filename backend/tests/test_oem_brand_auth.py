from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
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

    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["brand_mark"] == "daka"
    assert accepted.json()["email"] == PHONE_EMAIL


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
        "X-Lobster-Brand": "daka",
    }

    users = client.get("/admin/api/users", headers=headers)
    daka_users = client.get("/admin/api/users?brand=daka", headers=headers)
    bihuo_detail = client.get(f"/admin/api/user/{bihuo_user.id}", headers=headers)
    wrong_owner = client.get(
        f"/admin/api/ip-content/template-options?owner_user_id={bihuo_user.id}",
        headers=headers,
    )

    assert users.status_code == 200
    assert [item["brand_mark"] for item in users.json()["users"]] == ["daka", "bihuo"]
    assert daka_users.status_code == 200
    assert [item["brand_mark"] for item in daka_users.json()["users"]] == ["daka"]
    assert daka_users.json()["users"][0]["email"] == "daka-user@example.com"
    assert bihuo_detail.status_code == 200
    assert bihuo_detail.json()["user"]["brand_mark"] == "bihuo"
    assert wrong_owner.status_code == 404


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
    assert [item["email"] for item in users.json()["users"]] == ["daka-child@example.com"]
    assert {item["brand_mark"] for item in users.json()["users"]} == {"daka"}
