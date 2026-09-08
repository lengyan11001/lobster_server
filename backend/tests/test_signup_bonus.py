from decimal import Decimal

from backend.app.api.installation_slots import apply_installation_signup_bonus_for_new_user
from backend.app.models import User
from backend.app.services.brand_context import scoped_account_email


def _new_phone_user(db_session, phone: str, brand_mark: str, *, email: str | None = None) -> User:
    user = User(
        email=email or scoped_account_email(f"{phone}@sms.lobster.local", brand_mark),
        hashed_password="test",
        credits=Decimal("1000.0000"),
        role="user",
        preferred_model="sutui",
        brand_mark=brand_mark,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_signup_bonus_is_once_per_phone_and_oem(db_session, monkeypatch):
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)

    first = _new_phone_user(db_session, "13800138000", "bihuo")
    apply_installation_signup_bonus_for_new_user(db_session, first, phone="13800138000", brand_mark="bihuo")
    db_session.commit()
    assert first.credits == Decimal("1000.0000")

    # A different installation/device must not receive a second bonus.
    same_brand = _new_phone_user(
        db_session,
        "13800138000",
        "bihuo",
        email="another-device-account@test.local",
    )
    apply_installation_signup_bonus_for_new_user(
        db_session,
        same_brand,
        installation_id="another-device-slot",
        phone="13800138000",
        brand_mark="bihuo",
    )
    db_session.commit()
    assert same_brand.credits == Decimal("0")

    # The same number is a new user in another OEM and has its own bonus.
    other_brand = _new_phone_user(db_session, "13800138000", "hikong")
    apply_installation_signup_bonus_for_new_user(db_session, other_brand, phone="13800138000", brand_mark="hikong")
    db_session.commit()
    assert other_brand.credits == Decimal("1000.0000")
