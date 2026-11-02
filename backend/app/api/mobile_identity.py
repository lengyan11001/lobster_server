from __future__ import annotations

import re
from typing import Optional

import logging
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

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

def merge_wechat_user_into_phone(db: Session, wechat_user: "User", phone_user: "User") -> bool:
    """把微信临时账号合并到手机号账号：转移 openid、算力、以及所有 user_id 归属数据。"""
    if not wechat_user or not phone_user or wechat_user.id == phone_user.id:
        return False
    try:
        phone_user.credits = (phone_user.credits or 0) + (wechat_user.credits or 0)
        wechat_user.credits = 0
        wechat_openid = (getattr(wechat_user, "wechat_openid", None) or "").strip()
        if wechat_openid and not (getattr(phone_user, "wechat_openid", None) or "").strip():
            phone_user.wechat_openid = wechat_openid
        wechat_user.wechat_openid = None
        if not getattr(phone_user, "parent_user_id", None) and getattr(wechat_user, "parent_user_id", None):
            phone_user.parent_user_id = wechat_user.parent_user_id
        wechat_user.email = f"merged_wechat_{wechat_user.id}@merged.local"
        db.flush()
        rows = db.execute(text(
            "select table_name from information_schema.columns "
            "where table_schema='public' and column_name='user_id' and table_name<>'users'"
        )).fetchall()
        for (table,) in rows:
            sp = db.begin_nested()
            try:
                db.execute(text(f'update "{table}" set user_id=:new where user_id=:old'), {"new": phone_user.id, "old": wechat_user.id})
                sp.commit()
            except IntegrityError:
                # 新账号已有同键配置：丢弃旧账号这条，避免唯一键冲突
                sp.rollback()
                try:
                    db.execute(text(f'delete from "{table}" where user_id=:old'), {"old": wechat_user.id})
                    db.flush()
                except Exception:
                    logger.warning("merge cleanup failed: %s", table, exc_info=True)
                    db.rollback()
            except Exception:
                sp.rollback()
                logger.warning("merge table failed: %s", table, exc_info=True)
        return True
    except Exception:
        logger.warning("merge wechat user %s -> phone user %s failed", wechat_user.id, phone_user.id, exc_info=True)
        return False
