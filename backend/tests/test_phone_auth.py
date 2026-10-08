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


def test_explicit_claim_installation_slot_does_not_steal_shared_raw_slot(db_session, db_session_factory, monkeypatch):
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
    assert claim.json()["transferred"] is False
    with db_session_factory() as s:
        owner_row = s.query(InstallationSlotOwner).filter_by(installation_id="explicit-claim-slot-001").one()
        assert owner_row.user_id == old_owner.id
        assert s.query(InstallationSlotOwner).filter_by(user_id=new_owner.id).count() == 1


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


def _auth_client(db_session_factory, monkeypatch, token: str):
    client = _client(db_session_factory, monkeypatch)
    client.headers.update({"Authorization": "Bearer " + token})
    return client


def _login_token(user) -> str:
    from backend.app.api.auth import access_token_claims, create_access_token

    return create_access_token(data=access_token_claims(user))


def _make_user(db_session, *, email: str, password: str = "old-password-1", brand: str = "bihuo"):
    from backend.app.api.auth import get_password_hash
    from backend.app.models import User

    user = User(
        email=email,
        hashed_password=get_password_hash(password),
        password_initialized=True,
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        brand_mark=brand,
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_change_password_requires_original_password(db_session, db_session_factory, monkeypatch):
    from backend.app.api.auth import verify_password
    from backend.app.models import User

    user = _make_user(db_session, email=PHONE_EMAIL)
    client = _auth_client(db_session_factory, monkeypatch, _login_token(user))

    bad = client.post("/auth/password/change", json={"old_password": "wrong-pass", "new_password": "brand-new-1"})
    assert bad.status_code == 400
    with db_session_factory() as s:
        assert verify_password("old-password-1", s.get(User, user.id).hashed_password)

    same = client.post("/auth/password/change", json={"old_password": "old-password-1", "new_password": "old-password-1"})
    assert same.status_code == 400

    ok = client.post("/auth/password/change", json={"old_password": "old-password-1", "new_password": "brand-new-1"})
    assert ok.status_code == 200
    with db_session_factory() as s:
        assert verify_password("brand-new-1", s.get(User, user.id).hashed_password)


def test_change_phone_migrates_bindings_and_signup_claim(db_session, db_session_factory, monkeypatch):
    from backend.app.api.auth import _create_auth_challenge
    from backend.app.models import AuthChallenge, InstallationSignupBonusClaim, MobileDeviceBinding, User

    new_phone = "13900139002"
    user = _make_user(db_session, email=PHONE_EMAIL)
    db_session.add(MobileDeviceBinding(
        user_id=user.id,
        phone=PHONE,
        device_id="device-abc12345",
        platform="wechat_miniprogram",
        created_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
    ))
    db_session.add(InstallationSignupBonusClaim(
        installation_id=f"phone:bihuo:{PHONE}",
        user_id=user.id,
        phone=PHONE,
        brand_mark="bihuo",
        created_at=datetime.utcnow(),
    ))
    _create_auth_challenge(db_session, kind="sms", target=new_phone, answer="246810", ttl_seconds=600)
    _create_auth_challenge(db_session, kind="sms", target=PHONE, answer="111111", ttl_seconds=600)
    db_session.commit()

    client = _auth_client(db_session_factory, monkeypatch, _login_token(user))
    res = client.post(
        "/auth/phone/change",
        json={"password": "old-password-1", "new_phone": new_phone, "code": "246810"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["phone"] == new_phone

    with db_session_factory() as s:
        stored = s.get(User, user.id)
        assert stored.email == f"{new_phone}@sms.lobster.local"
        bindings = s.query(MobileDeviceBinding).filter(MobileDeviceBinding.user_id == user.id).all()
        assert [row.phone for row in bindings] == [new_phone]
        claims = {row.installation_id: row.phone for row in s.query(InstallationSignupBonusClaim).all()}
        assert f"phone:bihuo:{new_phone}" in claims
        assert f"phone:bihuo:{PHONE}" not in claims
        assert s.query(AuthChallenge).filter(AuthChallenge.kind == "sms", AuthChallenge.target == PHONE).count() == 0


def test_change_phone_rejects_taken_and_fixed_agent_phones(db_session, db_session_factory, monkeypatch):
    from backend.app.api.auth import _create_auth_challenge
    from backend.app.services.brand_context import BRAND_FIXED_AGENT_PHONES

    other = _make_user(db_session, email="13800138007@sms.lobster.local")
    user = _make_user(db_session, email=PHONE_EMAIL)
    fixed_phone = str(list(BRAND_FIXED_AGENT_PHONES.values())[0])
    _create_auth_challenge(db_session, kind="sms", target=fixed_phone, answer="333333", ttl_seconds=600)
    db_session.commit()

    client = _auth_client(db_session_factory, monkeypatch, _login_token(user))

    taken = client.post(
        "/auth/phone/change",
        json={"password": "old-password-1", "new_phone": "13800138007", "code": "333333"},
    )
    assert taken.status_code == 409

    fixed = client.post(
        "/auth/phone/change",
        json={"password": "old-password-1", "new_phone": fixed_phone, "code": "333333"},
    )
    assert fixed.status_code == 403

    fixed_self = _make_user(db_session, email=f"{fixed_phone}@sms.lobster.local")
    fixed_client = _auth_client(db_session_factory, monkeypatch, _login_token(fixed_self))
    blocked = fixed_client.post(
        "/auth/phone/change",
        json={"password": "old-password-1", "new_phone": "13900139003", "code": "333333"},
    )
    assert blocked.status_code == 403


def test_send_phone_change_code_needs_password_and_skips_free_numbers(db_session, db_session_factory, monkeypatch):
    from backend.app.api import auth as auth_module

    user = _make_user(db_session, email=PHONE_EMAIL)
    client = _auth_client(db_session_factory, monkeypatch, _login_token(user))
    sent = []
    monkeypatch.setattr(auth_module, "_dispatch_sms_code", lambda db, mobile, brand: sent.append((mobile, brand)))

    wrong = client.post(
        "/auth/phone/change/send-code",
        json={"password": "nope", "new_phone": "13900139005"},
    )
    assert wrong.status_code == 400
    assert sent == []

    ok = client.post(
        "/auth/phone/change/send-code",
        json={"password": "old-password-1", "new_phone": "13900139005"},
    )
    assert ok.status_code == 200
    assert sent and sent[0][0] == "13900139005"


def test_rebound_phone_logs_into_same_account_without_second_bonus(db_session, db_session_factory, monkeypatch):
    from backend.app.api.auth import _create_auth_challenge
    from backend.app.models import User

    new_phone = "13900139006"
    user = _make_user(db_session, email=PHONE_EMAIL)
    _create_auth_challenge(db_session, kind="sms", target=new_phone, answer="654321", ttl_seconds=600)
    db_session.commit()

    client = _auth_client(db_session_factory, monkeypatch, _login_token(user))
    assert client.post(
        "/auth/phone/change",
        json={"password": "old-password-1", "new_phone": new_phone, "code": "654321"},
    ).status_code == 200

    _put_sms_code(db_session, new_phone)
    again = _client(db_session_factory, monkeypatch).post(
        "/auth/register-phone",
        json={"phone": new_phone, "code": "123456"},
    )
    assert again.status_code == 200
    with db_session_factory() as s:
        assert s.query(User).filter(User.email == f"{new_phone}@sms.lobster.local").count() == 1
