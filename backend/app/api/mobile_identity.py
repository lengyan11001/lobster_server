from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

from ..models import MobileDeviceBinding, User
from ..services.brand_context import DEFAULT_BRAND_MARK, phone_from_account_email, user_brand_mark, user_for_account

_PHONE_EMAIL_SUFFIX = "@sms.lobster.local"
_CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")


def phone_email(mobile: str) -> str:
    return f"{(mobile or '').strip()}{_PHONE_EMAIL_SUFFIX}"


def phone_from_user_email(email: str) -> str:
    return phone_from_account_email(email)


def is_phone_account(user: Optional[User]) -> bool:
    return bool(user and phone_from_user_email(user.email or ""))


def is_wechat_session_user(user: Optional[User]) -> bool:
    if not user:
        return False
    return bool((getattr(user, "wechat_openid", None) or "").strip()) or str(user.email or "").endswith("@wechat.lobster.local")


def phone_account_user(db: Session, mobile: str, brand_mark: str = DEFAULT_BRAND_MARK) -> Optional[User]:
    mobile = (mobile or "").strip()
    if not mobile:
        return None
    return user_for_account(db, phone_email(mobile), brand_mark)


def latest_mobile_binding(db: Session, user: User) -> Optional[MobileDeviceBinding]:
    return (
        db.query(MobileDeviceBinding)
        .filter(MobileDeviceBinding.user_id == user.id)
        .order_by(MobileDeviceBinding.last_seen_at.desc())
        .first()
    )


def online_user_for_mobile_user(db: Session, current_user: User) -> User:
    """Resolve the online account used for devices/assets/tasks.

    Mini Program users are unique WeChat users.  A phone number is only a link
    to the desktop online account, so mobile routes that operate on desktop
    resources should transparently use the latest bound phone account.
    """
    if is_phone_account(current_user):
        return current_user
    row = latest_mobile_binding(db, current_user)
    if row and row.phone:
        phone_user = phone_account_user(db, row.phone, user_brand_mark(current_user))
        if phone_user:
            return phone_user
    return current_user


def online_user_for_mobile_binding(db: Session, current_user: User, binding: Optional[MobileDeviceBinding]) -> User:
    if is_phone_account(current_user):
        return current_user
    if binding and binding.phone:
        phone_user = phone_account_user(db, binding.phone, user_brand_mark(current_user))
        if phone_user:
            return phone_user
    return online_user_for_mobile_user(db, current_user)
