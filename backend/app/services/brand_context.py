from __future__ import annotations

import re
import hashlib
import json
import os
from pathlib import Path
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
    "hikong": {
        "mark": "hikong",
        "display_name": "海康AI智能体",
        "logo_primary": "海康",
        "logo_accent": "AI智能体",
        "document_title": "海康AI智能体",
        "icon_32": "/h5-static/hikong_32.png",
        "icon_128": "/h5-static/hikong_128.png",
        "icon_256": "/h5-static/hikong_256.png",
        "primary_color": "#0b8f8a",
        "domains": ["hikongai.com", "www.hikongai.com", "admin.hikongai.com"],
    },
}


def _manifest_oem_brands() -> dict[str, dict[str, Any]]:
    manifest_path = Path(__file__).resolve().parents[3] / "client_static" / "oem" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    raw_brands = manifest.get("brands") if isinstance(manifest, dict) else None
    if not isinstance(raw_brands, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_mark, entry in raw_brands.items():
        mark = str(raw_mark or "").strip().lower()
        profile = entry.get("profile") if isinstance(entry, dict) else None
        if not BRAND_MARK_RE.fullmatch(mark) or not isinstance(profile, dict):
            continue
        config = dict(profile)
        icons = config.get("icons") if isinstance(config.get("icons"), dict) else {}
        config["mark"] = mark
        config["icon_32"] = icons.get("favicon_32") or icons.get("logo_mark") or config.get("icon_32")
        config["icon_128"] = icons.get("loading_mark") or icons.get("apple_touch") or config.get("icon_128")
        config["icon_256"] = icons.get("apple_touch") or config.get("icon_256") or config.get("icon_128")
        result[mark] = config
    return result


BUILTIN_BRANDS.update(_manifest_oem_brands())
# Domains are deployment-owned routing signals, kept separate from the OEM asset
# manifest so a client branding refresh cannot remove a live web entry point.
BUILTIN_BRANDS.setdefault("hikong", {})["domains"] = [
    "hikongai.com",
    "www.hikongai.com",
    "admin.hikongai.com",
]


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


def resolve_brand_mark_candidates(
    *raw_values: Optional[str],
    default: Optional[str] = DEFAULT_BRAND_MARK,
) -> Optional[str]:
    """Resolve brand signals and reject requests that disagree about the brand."""
    marks: list[str] = []
    for raw in raw_values:
        value = str(raw or "").strip()
        if not value:
            continue
        mark = normalize_brand_mark(value)
        if mark not in marks:
            marks.append(mark)
    if len(marks) > 1:
        raise HTTPException(status_code=400, detail="请求中的品牌参数不一致")
    if marks:
        return marks[0]
    return normalize_brand_mark(default) if default is not None else None


def explicit_request_brand_mark(request: Request) -> Optional[str]:
    """Return a request's explicit brand without inventing a default."""
    domain_mark = request_domain_brand_mark(request)
    if domain_mark:
        return domain_mark
    return resolve_brand_mark_candidates(
        *request.headers.getlist("x-lobster-brand"),
        *request.query_params.getlist("brand"),
        *request.query_params.getlist("brand_mark"),
        default=None,
    )


def resolve_request_brand_mark(request: Request, *raw_values: Optional[str]) -> str:
    """Resolve body/form and transport brand values as one consistent context."""
    domain_mark = request_domain_brand_mark(request)
    if domain_mark:
        return domain_mark
    return str(
        resolve_brand_mark_candidates(
            *raw_values,
            *request.headers.getlist("x-lobster-brand"),
            *request.query_params.getlist("brand"),
            *request.query_params.getlist("brand_mark"),
        )
    )


def _domain_brand_map() -> dict[str, str]:
    """Return trusted OEM host mappings; URL parameters remain the fallback."""
    result: dict[str, str] = {}
    for mark, profile in BUILTIN_BRANDS.items():
        for raw_host in (profile.get("domains") or []) if isinstance(profile, dict) else []:
            host = str(raw_host or "").strip().lower().rstrip(".")
            if host:
                result[host] = mark
    raw = (os.environ.get("LOBSTER_OEM_DOMAIN_MAP") or os.environ.get("OEM_DOMAIN_MAP") or "").strip()
    if raw:
        try:
            configured = json.loads(raw)
        except (TypeError, ValueError):
            configured = {}
        if isinstance(configured, dict):
            for key, value in configured.items():
                key = str(key or "").strip().lower().rstrip(".")
                if not key:
                    continue
                # Accept either {"host": "mark"} or {"mark": ["host", ...]}.
                if isinstance(value, str):
                    mark = normalize_brand_mark(value, strict=False)
                    if "." in key:
                        result[key] = mark
                elif isinstance(value, (list, tuple, set)):
                    mark = normalize_brand_mark(key, strict=False)
                    for raw_host in value:
                        host = str(raw_host or "").strip().lower().rstrip(".")
                        if host:
                            result[host] = mark
    return result


def request_domain_brand_mark(request: Request) -> Optional[str]:
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",", 1)[0]
    host = host.split(":", 1)[0].strip().lower().rstrip(".")
    mark = _domain_brand_map().get(host)
    return normalize_brand_mark(mark, strict=False) if mark else None


def request_brand_mark(request: Request) -> str:
    return resolve_request_brand_mark(request)


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


def brand_short_name(db: Session, raw: Optional[str]) -> str:
    config = public_brand_config(db, raw)
    display_name = str(config.get("display_name") or config.get("document_title") or config.get("mark") or "").strip()
    short_name = re.sub(r"\s*(?:AI员工|AI智能体|AI助手)\s*$", "", display_name, flags=re.IGNORECASE).strip()
    return short_name or display_name or normalize_brand_mark(raw, strict=False)
