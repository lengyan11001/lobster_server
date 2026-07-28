from __future__ import annotations

import re
import hashlib
from typing import Any, Optional

from fastapi import HTTPException, Request
from sqlalchemy import inspect, or_, text
from sqlalchemy.orm import Session

from ..models import BrandConfig, User


DEFAULT_BRAND_MARK = "bihuo"
BRAND_MARK_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
PHONE_EMAIL_SUFFIX = "@sms.lobster.local"
BRAND_EMAIL_TAG = "+brand-"

BUILTIN_BRANDS: dict[str, dict[str, Any]] = {
    "bihuo": {
        "mark": "bihuo",
        "display_name": "必火AI员工",
        "logo_primary": "必火",
        "logo_accent": "AI员工",
        "document_title": "必火AI员工",
        "icon_32": "/h5-static/bihu_32.png",
        "icon_128": "/h5-static/bihu_128.png",
        "icon_256": "/h5-static/bihu_256.png",
        "primary_color": "#2f6fed",
    },
    "daka": {
        "mark": "daka",
        "display_name": "大咖AI员工",
        "logo_primary": "大咖",
        "logo_accent": "AI员工",
        "document_title": "大咖AI员工",
        "icon_32": "/h5-static/daka_32.png",
        "icon_128": "/h5-static/daka_128.png",
        "icon_256": "/h5-static/daka_256.png",
        "primary_color": "#00a9c7",
    },
}


def ensure_user_brand_schema(db_engine) -> None:
    inspector = inspect(db_engine)
    if not inspector.has_table("users"):
        return
    columns = {column["name"] for column in inspector.get_columns("users")}
    with db_engine.begin() as connection:
        if "brand_mark" not in columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN brand_mark VARCHAR(64) NOT NULL DEFAULT 'bihuo'"))
        connection.execute(text("UPDATE users SET brand_mark = 'bihuo' WHERE brand_mark IS NULL OR TRIM(brand_mark) = ''"))
        if db_engine.dialect.name == "postgresql":
            connection.execute(text("ALTER TABLE users ALTER COLUMN brand_mark SET DEFAULT 'bihuo'"))
            connection.execute(text("ALTER TABLE users ALTER COLUMN brand_mark SET NOT NULL"))


def seed_brand_configs(session_factory) -> None:
    db = session_factory()
    try:
        for mark, config in BUILTIN_BRANDS.items():
            row = db.query(BrandConfig).filter(BrandConfig.mark == mark).first()
            if row is None:
                db.add(
                    BrandConfig(
                        mark=mark,
                        display_name=str(config.get("display_name") or mark),
                        enabled=True,
                        config={},
                    )
                )
            elif not row.display_name:
                row.display_name = str(config.get("display_name") or mark)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def normalize_brand_mark(raw: Optional[str], *, strict: bool = True) -> str:
    mark = (raw or DEFAULT_BRAND_MARK).strip().lower() or DEFAULT_BRAND_MARK
    if not BRAND_MARK_RE.fullmatch(mark):
        if strict:
            raise HTTPException(status_code=400, detail="品牌参数格式无效")
        return DEFAULT_BRAND_MARK
    return mark


def request_brand_mark(request: Request) -> str:
    raw = (
        request.headers.get("x-lobster-brand")
        or request.query_params.get("brand")
        or request.query_params.get("brand_mark")
        or DEFAULT_BRAND_MARK
    )
    return normalize_brand_mark(raw)


def ensure_brand_enabled(db: Session, raw: Optional[str]) -> str:
    mark = normalize_brand_mark(raw)
    row = db.query(BrandConfig).filter(BrandConfig.mark == mark).first()
    # Isolated router tests and first-start migrations can run before the seed
    # transaction. Built-in brands remain usable until their persisted row is
    # created; once a row exists its enabled flag is authoritative.
    if row is None and mark in BUILTIN_BRANDS:
        return mark
    if row is None or not bool(row.enabled):
        raise HTTPException(status_code=403, detail="当前品牌未启用")
    return mark


def user_brand_mark(user: User) -> str:
    return normalize_brand_mark(getattr(user, "brand_mark", None), strict=False)


def scoped_account_email(account_email: str, raw_brand: Optional[str]) -> str:
    email = (account_email or "").strip().lower()
    mark = normalize_brand_mark(raw_brand)
    if not email or mark == DEFAULT_BRAND_MARK:
        return email
    local, separator, domain = email.partition("@")
    if not separator:
        return f"{mark}:{email}"
    return f"{local}{BRAND_EMAIL_TAG}{mark}@{domain}"


def unscoped_account_email(account_email: str) -> str:
    email = (account_email or "").strip().lower()
    local, separator, domain = email.partition("@")
    if separator and BRAND_EMAIL_TAG in local:
        local = local.rsplit(BRAND_EMAIL_TAG, 1)[0]
        return f"{local}@{domain}"
    if ":" in email and "@" not in email:
        prefix, value = email.split(":", 1)
        if BRAND_MARK_RE.fullmatch(prefix) and value:
            return value
    return email


def phone_from_account_email(account_email: str) -> str:
    email = unscoped_account_email(account_email)
    if not email.endswith(PHONE_EMAIL_SUFFIX):
        return ""
    mobile = email[: -len(PHONE_EMAIL_SUFFIX)]
    return mobile if re.fullmatch(r"1[3-9]\d{9}", mobile) else ""


def scoped_installation_id(installation_id: Optional[str], raw_brand: Optional[str]) -> Optional[str]:
    value = (installation_id or "").strip()
    if not value:
        return None
    mark = normalize_brand_mark(raw_brand)
    if mark == DEFAULT_BRAND_MARK:
        return value
    prefix = f"{mark}--"
    if value.startswith(prefix):
        return value
    if len(prefix) + len(value) <= 128:
        return prefix + value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}{digest}"


def user_for_account(db: Session, account_email: str, raw_brand: Optional[str]) -> Optional[User]:
    mark = normalize_brand_mark(raw_brand)
    scoped = scoped_account_email(account_email, mark)
    brand_filter = User.brand_mark == mark
    if mark == DEFAULT_BRAND_MARK:
        brand_filter = or_(User.brand_mark == mark, User.brand_mark.is_(None), User.brand_mark == "")
    return db.query(User).filter(User.email == scoped, brand_filter).first()


def public_brand_config(db: Session, raw: Optional[str]) -> dict[str, Any]:
    mark = ensure_brand_enabled(db, raw)
    row = db.query(BrandConfig).filter(BrandConfig.mark == mark).first()
    base = dict(BUILTIN_BRANDS.get(mark) or {"mark": mark, "display_name": row.display_name if row else mark})
    if row is not None and isinstance(row.config, dict):
        base.update({key: value for key, value in row.config.items() if value is not None})
    base["mark"] = mark
    base["display_name"] = (row.display_name if row else "") or base.get("display_name") or mark
    base["enabled"] = True
    return base
