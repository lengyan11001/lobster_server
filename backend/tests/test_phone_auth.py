from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient


PHONE = "13800138000"
PHONE_EMAIL = f"{PHONE}@sms.lobster.local"


def _client(db_session_factory, monkeypatch):
    from backend.app.api.auth import router as auth_router
    from backend.app.core.config import settings
    from backend.app.db import get_db

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)

    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def _put_sms_code(db_session, phone: str, code: str = "123456") -> None:
    from backend.app.api.auth import _create_auth_challenge

    _create_auth_challenge(db_session, kind="sms", target=phone, answer=code, ttl_seconds=600)
    db_session.commit()


def test_register_phone_existing_user_logs_in_without_password(db_session, db_session_factory, monkeypatch):
    from backend.app.models import User

    user = User(
        email=PHONE_EMAIL,
        hashed_password="x",
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    _put_sms_code(db_session, PHONE)

    res = _client(db_session_factory, monkeypatch).post(
        "/auth/register-phone",
        json={"phone": PHONE, "code": "123456"},
    )

    assert res.status_code == 200
    assert res.json()["access_token"]
    with db_session_factory() as s:
        assert s.query(User).filter(User.email == PHONE_EMAIL).count() == 1


def test_wrong_sms_code_does_not_consume_challenge(db_session, db_session_factory, monkeypatch):
    from backend.app.models import AuthChallenge, User

    user = User(
        email=PHONE_EMAIL,
        hashed_password="x",
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()
    _put_sms_code(db_session, PHONE)

    client = _client(db_session_factory, monkeypatch)
    bad = client.post(
        "/auth/register-phone",
        json={"phone": PHONE, "code": "000000"},
    )
    assert bad.status_code == 400

    with db_session_factory() as s:
        assert s.query(AuthChallenge).filter(AuthChallenge.kind == "sms", AuthChallenge.target == PHONE).count() == 1

    ok = client.post(
        "/auth/register-phone",
        json={"phone": PHONE, "code": "123456"},
    )
    assert ok.status_code == 200
    assert ok.json()["access_token"]

    with db_session_factory() as s:
        assert s.query(AuthChallenge).filter(AuthChallenge.kind == "sms", AuthChallenge.target == PHONE).count() == 0


def test_register_phone_new_user_creates_and_logs_in_without_password(db_session, db_session_factory, monkeypatch):
    from backend.app.models import User

    phone = "13900139000"
    _put_sms_code(db_session, phone)

    res = _client(db_session_factory, monkeypatch).post(
        "/auth/register-phone",
        json={"phone": phone, "code": "123456"},
    )

    assert res.status_code == 200
    assert res.json()["access_token"]
    with db_session_factory() as s:
        user = s.query(User).filter(User.email == f"{phone}@sms.lobster.local").first()
        assert user is not None
        assert user.hashed_password


def test_register_phone_can_mark_overseas_user(db_session, db_session_factory, monkeypatch):
    from backend.app.models import User

    phone = "13900139001"
    _put_sms_code(db_session, phone)

    res = _client(db_session_factory, monkeypatch).post(
        "/auth/register-phone",
        json={"phone": phone, "code": "123456", "is_overseas_user": True},
    )

    assert res.status_code == 200
    assert res.json()["access_token"]
    with db_session_factory() as s:
        user = s.query(User).filter(User.email == f"{phone}@sms.lobster.local").first()
        assert user is not None
        assert bool(user.is_overseas_user) is True


def test_set_password_then_phone_password_login(db_session, db_session_factory, monkeypatch):
    from backend.app.api.auth import create_access_token, get_password_hash
    from backend.app.models import User

    user = User(
        email=PHONE_EMAIL,
        hashed_password=get_password_hash("phone-code-old"),
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    client = _client(db_session_factory, monkeypatch)
    token = create_access_token(data={"sub": str(user.id)})

    set_res = client.post(
        "/auth/set-password",
        json={"password": "abc123456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert set_res.status_code == 200
    assert set_res.json()["ok"] is True

    login_res = client.post(
        "/auth/login-phone-password",
        json={"phone": PHONE, "password": "abc123456"},
    )
    assert login_res.status_code == 200
    assert login_res.json()["access_token"]


def test_phone_password_login_rejects_wrong_password(db_session, db_session_factory, monkeypatch):
    from backend.app.api.auth import get_password_hash
    from backend.app.models import User

    user = User(
        email=PHONE_EMAIL,
        hashed_password=get_password_hash("right-pass"),
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()

    res = _client(db_session_factory, monkeypatch).post(
        "/auth/login-phone-password",
        json={"phone": PHONE, "password": "wrong-pass"},
    )
    assert res.status_code == 400


def test_password_login_accepts_non_phone_account(db_session, db_session_factory, monkeypatch):
    from backend.app.api.auth import get_password_hash
    from backend.app.models import User

    user = User(
        email="agent_demo",
        hashed_password=get_password_hash("right-pass"),
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()

    res = _client(db_session_factory, monkeypatch).post(
        "/auth/login-phone-password",
        json={"account": "agent_demo", "password": "right-pass"},
    )
    assert res.status_code == 200
    assert res.json()["access_token"]
