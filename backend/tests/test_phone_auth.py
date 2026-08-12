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
    from backend.app.api.auth import verify_password
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
        stored = s.query(User).filter(User.email == PHONE_EMAIL).one()
        assert bool(stored.password_initialized) is True
        assert verify_password(PHONE[-6:], stored.hashed_password)


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
    from backend.app.api.auth import verify_password
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
        assert bool(user.password_initialized) is True
        assert verify_password(phone[-6:], user.hashed_password)


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

    with db_session_factory() as s:
        stored = s.query(User).filter(User.id == user.id).one()
        assert bool(stored.password_initialized) is True

    login_res = client.post(
        "/auth/login-phone-password",
        json={"phone": PHONE, "password": "abc123456"},
    )
    assert login_res.status_code == 200
    assert login_res.json()["access_token"]


def test_password_login_does_not_claim_execution_slot(db_session, db_session_factory, monkeypatch):
    from backend.app.api.auth import get_password_hash
    from backend.app.models import InstallationSlotOwner, User, UserInstallation

    owner = User(
        email="13900139110@sms.lobster.local",
        hashed_password=get_password_hash("owner-pass"),
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    login_user = User(
        email="13900139111@sms.lobster.local",
        hashed_password=get_password_hash("login-pass"),
        password_initialized=True,
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add_all([owner, login_user])
    db_session.flush()
    slot = InstallationSlotOwner(
        installation_id="shared-login-slot-001",
        user_id=owner.id,
        brand_mark="bihuo",
        lease_version=1,
        claimed_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(slot)
    db_session.commit()

    res = _client(db_session_factory, monkeypatch).post(
        "/auth/login-phone-password",
        json={"phone": "13900139111", "password": "login-pass"},
        headers={"X-Installation-Id": "shared-login-slot-001"},
    )

    assert res.status_code == 200
    with db_session_factory() as s:
        owner_row = s.query(InstallationSlotOwner).filter_by(installation_id="shared-login-slot-001").one()
        assert owner_row.user_id == owner.id
        assert (
            s.query(UserInstallation)
            .filter_by(user_id=login_user.id, installation_id="shared-login-slot-001")
            .count()
            == 1
        )


def test_sms_login_does_not_claim_execution_slot(db_session, db_session_factory, monkeypatch):
    from backend.app.api.auth import get_password_hash
    from backend.app.models import InstallationSlotOwner, User

    owner = User(
        email="13900139120@sms.lobster.local",
        hashed_password=get_password_hash("owner-pass"),
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    login_user = User(
        email="13900139121@sms.lobster.local",
        hashed_password=get_password_hash("custom-password"),
        password_initialized=True,
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add_all([owner, login_user])
    db_session.flush()
    db_session.add(
        InstallationSlotOwner(
            installation_id="shared-sms-slot-001",
            user_id=owner.id,
            brand_mark="bihuo",
            lease_version=1,
            claimed_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    db_session.commit()
    _put_sms_code(db_session, "13900139121")

    res = _client(db_session_factory, monkeypatch).post(
        "/auth/register-phone",
        json={"phone": "13900139121", "code": "123456"},
        headers={"X-Installation-Id": "shared-sms-slot-001"},
    )

    assert res.status_code == 200
    with db_session_factory() as s:
        owner_row = s.query(InstallationSlotOwner).filter_by(installation_id="shared-sms-slot-001").one()
        assert owner_row.user_id == owner.id


def test_login_ignores_deprecated_polluted_installation_id(db_session, db_session_factory, monkeypatch):
    from backend.app.api.auth import get_password_hash
    from backend.app.models import User, UserInstallation

    user = User(
        email="13900139125@sms.lobster.local",
        hashed_password=get_password_hash("custom-password"),
        password_initialized=True,
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()

    res = _client(db_session_factory, monkeypatch).post(
        "/auth/login-phone-password",
        json={"phone": "13900139125", "password": "custom-password"},
        headers={"X-Installation-Id": "2fc3f43f7a684411a442cb661898aa74"},
    )

    assert res.status_code == 200
    with db_session_factory() as s:
        assert (
            s.query(UserInstallation)
            .filter_by(user_id=user.id, installation_id="2fc3f43f7a684411a442cb661898aa74")
            .count()
            == 0
        )


def test_explicit_claim_installation_slot_still_transfers_owner(db_session, db_session_factory, monkeypatch):
    from backend.app.api.auth import get_password_hash
    from backend.app.models import InstallationSlotOwner, User

    old_owner = User(
        email="13900139130@sms.lobster.local",
        hashed_password=get_password_hash("owner-pass"),
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    new_owner = User(
        email="13900139131@sms.lobster.local",
        hashed_password=get_password_hash("new-pass"),
        password_initialized=True,
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add_all([old_owner, new_owner])
    db_session.flush()
    db_session.add(
        InstallationSlotOwner(
            installation_id="explicit-claim-slot-001",
            user_id=old_owner.id,
            brand_mark="bihuo",
            lease_version=1,
            claimed_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    db_session.commit()

    client = _client(db_session_factory, monkeypatch)
    login = client.post(
        "/auth/login-phone-password",
        json={"phone": "13900139131", "password": "new-pass"},
        headers={"X-Installation-Id": "explicit-claim-slot-001"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    claim = client.post(
        "/auth/claim-installation-slot",
        headers={"Authorization": f"Bearer {token}", "X-Installation-Id": "explicit-claim-slot-001"},
    )

    assert claim.status_code == 200
    assert claim.json()["transferred"] is True
    with db_session_factory() as s:
        owner_row = s.query(InstallationSlotOwner).filter_by(installation_id="explicit-claim-slot-001").one()
        assert owner_row.user_id == new_owner.id


def test_sms_login_does_not_replace_an_initialized_password(db_session, db_session_factory, monkeypatch):
    from backend.app.api.auth import get_password_hash, verify_password
    from backend.app.models import User

    user = User(
        email=PHONE_EMAIL,
        hashed_password=get_password_hash("custom-password"),
        password_initialized=True,
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()
    _put_sms_code(db_session, PHONE)

    res = _client(db_session_factory, monkeypatch).post(
        "/auth/register-phone",
        json={"phone": PHONE, "code": "123456"},
    )

    assert res.status_code == 200
    with db_session_factory() as s:
        stored = s.query(User).filter(User.id == user.id).one()
        assert verify_password("custom-password", stored.hashed_password)
        assert not verify_password(PHONE[-6:], stored.hashed_password)


def test_backfill_initializes_only_uninitialized_phone_users(db_session):
    from backend.app.api.auth import backfill_phone_default_passwords, get_password_hash, verify_password
    from backend.app.models import User

    legacy = User(
        email=PHONE_EMAIL,
        hashed_password="legacy-placeholder",
        password_initialized=False,
        credits=Decimal("1"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    custom = User(
        email="13900139002@sms.lobster.local",
        hashed_password=get_password_hash("keep-this-password"),
        password_initialized=True,
        credits=Decimal("1"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    non_phone = User(
        email="plain-user@example.com",
        hashed_password=get_password_hash("plain-password"),
        password_initialized=False,
        credits=Decimal("1"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add_all([legacy, custom, non_phone])
    db_session.commit()

    assert backfill_phone_default_passwords(db_session) == 1
    db_session.refresh(legacy)
    db_session.refresh(custom)
    db_session.refresh(non_phone)
    assert verify_password(PHONE[-6:], legacy.hashed_password)
    assert verify_password("keep-this-password", custom.hashed_password)
    assert verify_password("plain-password", non_phone.hashed_password)


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
