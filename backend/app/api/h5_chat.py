from __future__ import annotations

import asyncio
import base64
import html
import ipaddress
import json
import logging
import re
import socket
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import SessionLocal, get_db
from ..models import (
    DouyinDashboardDeviceState,
    H5ChatDevicePresence,
    H5ChatEvent,
    H5ChatMessage,
    H5MountedAccountDefault,
    PublishAccount,
    User,
    UserInstallation,
)
from ..services.runtime_cache import cache_delete, cache_flag_recent, cache_mark_flag
from .auth import ALGORITHM, get_current_user, get_current_user_id_from_token
from .installation_slots import INSTALLATION_ID_HEADER, ensure_installation_slot, optional_installation_id_from_request
from .mobile_identity import online_user_for_mobile_user
from .publish import SUPPORTED_PLATFORMS
from .sutui_chat_proxy import charge_chat_turn_once

logger = logging.getLogger(__name__)
router = APIRouter()

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_H5_INDEX = _ROOT / "h5_static" / "index.html"
_H5_STATIC_DIR = _ROOT / "h5_static"
_H5_INDEX_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
_H5_UPLOAD_DIR = _ROOT / "temp_assets" / "h5_chat_uploads"
_H5_WEBCLIP_URL = "https://h5.bhzn.top/"
_H5_WEBCLIP_LABEL = "必火AI员工"
_VALID_MODES = {"direct"}
_H5_CLIENT_COMMAND_PREFIX = "__LOBSTER_H5_CLIENT_COMMAND__"
_FINAL_STATUSES = {"completed", "failed", "cancelled"}
_CHAT_TURN_BILLING_SUPPORT_HEADER = "X-Lobster-Chat-Turn-Billing"
_UPLOAD_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_H5_UPLOAD_BYTES = 15 * 1024 * 1024
_MAX_MEDIA_PROXY_BYTES = 1024 * 1024 * 1024
_DEVICE_ONLINE_TTL_SECONDS = 90
_DEVICE_HEARTBEAT_WRITE_MIN_SECONDS = 20
_DEVICE_HEARTBEAT_FAST_ACK_SECONDS = 55.0
_PENDING_INSTALLATION_TOUCH_MIN_SECONDS = 60
_PENDING_EMPTY_CACHE_SECONDS = 5.0
_pending_empty_cache: Dict[str, float] = {}
_heartbeat_ack_cache: Dict[str, float] = {}
_IMAGE_EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_MEDIA_TYPE_BY_EXT = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


class H5MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=8000)
    mode: str = "direct"
    installation_id: Optional[str] = None


class H5ChatEventIn(BaseModel):
    type: str = Field(..., min_length=1, max_length=32)
    payload: Dict[str, Any] = Field(default_factory=dict)


class H5ChatCompleteIn(BaseModel):
    reply_text: Optional[str] = None
    error: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class H5HeartbeatIn(BaseModel):
    display_name: Optional[str] = None
    publish_accounts: Optional[List[Dict[str, Any]]] = None


class H5DeviceDisplayNameIn(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=128)


class H5MountedAccountDefaultIn(BaseModel):
    scope: str = Field(..., min_length=1, max_length=32)
    account_key: str = Field(..., min_length=1, max_length=255)


class H5WechatAutoReplyIn(BaseModel):
    enabled: bool = False
    installation_id: Optional[str] = Field(default=None, max_length=128)
    account_key: Optional[str] = Field(default="wechat:pc-default", max_length=255)
    account_id: Optional[str] = Field(default="pc-wechat-default", max_length=160)
    interval_seconds: int = Field(default=1800, ge=300, le=86400)


def _normalize_publish_account_snapshot(accounts: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    if accounts is None:
        return None
    out: List[Dict[str, Any]] = []
    for item in accounts:
        if not isinstance(item, dict):
            continue
        platform = str(item.get("platform") or "").strip()
        nickname = str(item.get("nickname") or item.get("name") or "").strip()
        if not platform or not nickname:
            continue
        account_id = item.get("account_id", item.get("id"))
        row = {
            "id": str(account_id or "").strip(),
            "account_id": str(account_id or "").strip(),
            "platform": platform[:64],
            "platform_name": str(item.get("platform_name") or item.get("platform_label") or platform).strip()[:64],
            "nickname": nickname[:128],
            "status": str(item.get("status") or "").strip()[:64],
            "online": bool(item.get("online")),
        }
        managed_by = str(item.get("managed_by") or "").strip()
        if managed_by:
            row["managed_by"] = managed_by[:64]
        if item.get("is_origin_slot") is not None:
            row["is_origin_slot"] = bool(item.get("is_origin_slot"))
        out.append(row)
        if len(out) >= 200:
            break
    return out


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _device_payload(row: H5ChatDevicePresence, now: Optional[datetime] = None) -> Dict[str, Any]:
    current = now or datetime.utcnow()
    age = (current - row.last_seen_at).total_seconds() if row.last_seen_at else 999999
    return {
        "installation_id": row.installation_id,
        "device_name": row.display_name or row.installation_id,
        "last_seen_at": _iso(row.last_seen_at),
        "online": age <= _DEVICE_ONLINE_TTL_SECONDS,
    }


def _mounted_default_rows(db: Session, user_id: int) -> Dict[str, H5MountedAccountDefault]:
    rows = db.query(H5MountedAccountDefault).filter(H5MountedAccountDefault.user_id == user_id).all()
    return {str(row.scope or ""): row for row in rows if row.scope}


def _mounted_default_payload(row: H5MountedAccountDefault) -> Dict[str, Any]:
    return {
        "scope": row.scope,
        "account_key": row.account_key,
        "platform": row.platform or "",
        "account_id": row.account_id or "",
        "account_label": row.account_label or "",
        "installation_id": row.installation_id or "",
        "source": row.source or "",
        "updated_at": _iso(row.updated_at),
    }


def _publish_default_scope(platform: Any) -> str:
    platform_key = str(platform or "").strip().lower()
    return f"publish:{platform_key}" if platform_key else "publish"


def _default_scope_for_mounted_account(row: Dict[str, Any]) -> str:
    scope = str(row.get("scope") or "").strip().lower()
    if scope == "publish":
        return _publish_default_scope(row.get("platform"))
    return scope


def _default_pref_for_mounted_account(
    defaults: Dict[str, H5MountedAccountDefault],
    row: Dict[str, Any],
) -> Optional[H5MountedAccountDefault]:
    default_scope = _default_scope_for_mounted_account(row)
    pref = defaults.get(default_scope)
    if pref or str(row.get("scope") or "").strip().lower() != "publish":
        return pref

    legacy = defaults.get("publish")
    legacy_platform = str((legacy.platform if legacy else "") or "").strip().lower()
    row_platform = str(row.get("platform") or "").strip().lower()
    if legacy and legacy_platform == row_platform:
        return legacy
    return None


def _account_online_from_status(status: str, online: Any = None, *, device_online: bool = True) -> bool:
    if online is not None:
        return bool(online) and device_online
    return str(status or "").strip().lower() in {"active", "online", "logged_in", "ready", "success"} and device_online


def _mounted_account_row(
    *,
    scope: str,
    account_key: str,
    source: str,
    source_label: str,
    platform: str,
    platform_name: str,
    account_id: Any,
    nickname: str,
    status: str,
    online: bool,
    installation_id: str = "",
    device_name: str = "",
    last_seen_at: str = "",
    last_login: str = "",
    defaultable: bool = True,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    label = nickname or (f"{platform_name}账号" if platform_name else "账号")
    row = {
        "scope": scope,
        "account_key": account_key,
        "source": source,
        "source_label": source_label,
        "platform": platform,
        "platform_name": platform_name or platform,
        "account_id": str(account_id or "").strip(),
        "nickname": label,
        "status": status or ("online" if online else "offline"),
        "online": bool(online),
        "installation_id": installation_id,
        "device_name": device_name,
        "last_seen_at": last_seen_at,
        "last_login": last_login,
        "defaultable": bool(defaultable),
    }
    if extra:
        row.update(extra)
    return row


def _collect_device_publish_accounts(db: Session, user_id: int, now: datetime) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    devices = (
        db.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.user_id == user_id)
        .order_by(H5ChatDevicePresence.last_seen_at.desc())
        .limit(30)
        .all()
    )
    device_rows = [_device_payload(row, now) for row in devices]
    out: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for device, device_info in zip(devices, device_rows):
        payload = device.account_payload if isinstance(device.account_payload, dict) else {}
        accounts = payload.get("accounts") if isinstance(payload.get("accounts"), list) else []
        for item in accounts:
            if not isinstance(item, dict):
                continue
            platform = str(item.get("platform") or "").strip()
            account_id = str(item.get("account_id") or item.get("id") or "").strip()
            nickname = str(item.get("nickname") or item.get("name") or "").strip()
            if not platform or not (account_id or nickname):
                continue
            key_id = account_id or nickname
            account_key = f"{device.installation_id}:{platform}:{key_id}"
            if account_key in seen:
                continue
            seen.add(account_key)
            status = str(item.get("status") or "").strip()
            online = _account_online_from_status(status, item.get("online"), device_online=bool(device_info["online"]))
            out.append(
                _mounted_account_row(
                    scope="publish",
                    account_key=account_key,
                    source="publish_device",
                    source_label="发布中心",
                    platform=platform,
                    platform_name=str(item.get("platform_name") or item.get("platform_label") or SUPPORTED_PLATFORMS.get(platform, {}).get("name", platform)),
                    account_id=key_id,
                    nickname=nickname or key_id,
                    status=status,
                    online=online,
                    installation_id=device.installation_id,
                    device_name=str(device_info["device_name"] or ""),
                    last_seen_at=str(device_info["last_seen_at"] or ""),
                    extra={
                        "select_id": account_key,
                        "managed_by": str(item.get("managed_by") or "").strip(),
                        "is_origin_slot": bool(item.get("is_origin_slot")) if item.get("is_origin_slot") is not None else False,
                    },
                )
            )
    return out, device_rows


def _collect_server_publish_accounts(db: Session, user_id: int) -> list[Dict[str, Any]]:
    rows = (
        db.query(PublishAccount)
        .filter(PublishAccount.user_id == user_id)
        .order_by(PublishAccount.created_at.desc())
        .limit(200)
        .all()
    )
    return [
        _mounted_account_row(
            scope="publish",
            account_key=f"server:{row.id}",
            source="publish_server",
            source_label="发布中心",
            platform=row.platform,
            platform_name=SUPPORTED_PLATFORMS.get(row.platform, {}).get("name", row.platform),
            account_id=row.id,
            nickname=row.nickname,
            status=row.status,
            online=_account_online_from_status(row.status, None, device_online=True),
            last_login=_iso(row.last_login) or "",
            defaultable=False,
            extra={"select_id": f"server:{row.id}"},
        )
        for row in rows
    ]


def _collect_douyin_lead_accounts(db: Session, user_id: int, device_by_id: Dict[str, Dict[str, Any]]) -> list[Dict[str, Any]]:
    rows = (
        db.query(DouyinDashboardDeviceState)
        .filter(DouyinDashboardDeviceState.user_id == user_id)
        .order_by(DouyinDashboardDeviceState.updated_at.desc())
        .limit(30)
        .all()
    )
    out: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for state_row in rows:
        payload = state_row.payload if isinstance(state_row.payload, dict) else {}
        accounts = payload.get("accounts") if isinstance(payload.get("accounts"), list) else []
        for item in accounts:
            if not isinstance(item, dict):
                continue
            installation_id = str(item.get("installation_id") or state_row.installation_id or "").strip()
            account_id = str(item.get("account_id") or item.get("id") or "").strip()
            nickname = str(item.get("nickname") or item.get("name") or account_id or "").strip()
            if not (account_id or nickname):
                continue
            key_id = account_id or nickname
            account_key = f"{installation_id}:douyin:{key_id}"
            if account_key in seen:
                continue
            seen.add(account_key)
            device_info = device_by_id.get(installation_id) or {}
            device_online = bool(device_info.get("online"))
            status = str(item.get("status") or "").strip()
            online = _account_online_from_status(status, item.get("online"), device_online=device_online)
            out.append(
                _mounted_account_row(
                    scope="douyin",
                    account_key=account_key,
                    source="douyin_leads",
                    source_label="抖音获客",
                    platform="douyin",
                    platform_name="抖音",
                    account_id=key_id,
                    nickname=nickname or key_id,
                    status=status,
                    online=online,
                    installation_id=installation_id,
                    device_name=str(device_info.get("device_name") or installation_id),
                    last_seen_at=str(device_info.get("last_seen_at") or ""),
                    last_login=str(item.get("last_login") or "").strip(),
                    extra={"select_id": account_key},
                )
            )
    return out


def _collect_wechat_account(device_rows: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    if not device_rows:
        return []
    selected = next((row for row in device_rows if row.get("online")), device_rows[0])
    return [
        _mounted_account_row(
            scope="wechat",
            account_key="wechat:pc-default",
            source="pc_wechat",
            source_label="微信",
            platform="wechat",
            platform_name="微信",
            account_id="pc-wechat-default",
            nickname="本机微信",
            status="online" if selected.get("online") else "offline",
            online=bool(selected.get("online")),
            installation_id=str(selected.get("installation_id") or ""),
            device_name=str(selected.get("device_name") or ""),
            last_seen_at=str(selected.get("last_seen_at") or ""),
            defaultable=False,
        )
    ]


def _mounted_accounts_payload(db: Session, user_id: int) -> Dict[str, Any]:
    now = datetime.utcnow()
    publish_device_accounts, device_rows = _collect_device_publish_accounts(db, user_id, now)
    device_by_id = {str(row.get("installation_id") or ""): row for row in device_rows}
    accounts = (
        _collect_wechat_account(device_rows)
        + publish_device_accounts
        + _collect_server_publish_accounts(db, user_id)
        + _collect_douyin_lead_accounts(db, user_id, device_by_id)
    )
    defaults = _mounted_default_rows(db, user_id)
    default_payload = {scope: _mounted_default_payload(row) for scope, row in defaults.items()}
    auto_reply_pref = defaults.get("wechat_auto_reply")
    auto_reply_payload = auto_reply_pref.payload if auto_reply_pref and isinstance(auto_reply_pref.payload, dict) else {}
    for row in accounts:
        row["default_scope"] = _default_scope_for_mounted_account(row)
        pref = _default_pref_for_mounted_account(defaults, row)
        row["is_default"] = bool(pref and pref.account_key == row.get("account_key"))
        if row.get("scope") == "wechat":
            row["auto_reply_enabled"] = bool(auto_reply_payload.get("enabled") or auto_reply_payload.get("auto_reply_enabled"))
            row["auto_reply_interval_seconds"] = int(auto_reply_payload.get("interval_seconds") or 1800)
    return {"ok": True, "accounts": accounts, "defaults": default_payload, "devices": device_rows}


def _serialize_event(row: H5ChatEvent) -> Dict[str, Any]:
    return {
        "id": row.id,
        "message_id": row.message_id,
        "type": row.event_type,
        "payload": row.payload or {},
        "created_at": _iso(row.created_at),
    }


def _serialize_message(
    row: H5ChatMessage,
    *,
    include_reply: bool = True,
    chat_turn_charged: bool = False,
) -> Dict[str, Any]:
    data = {
        "id": row.id,
        "mode": row.mode,
        "content": row.content,
        "status": row.status,
        "installation_id": row.installation_id,
        "claimed_by_installation_id": row.claimed_by_installation_id,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "claimed_at": _iso(row.claimed_at),
        "finished_at": _iso(row.finished_at),
    }
    if chat_turn_charged and row.status == "processing":
        data["chat_turn_charged"] = True
        data["chat_turn_id"] = f"h5:{row.id}"
    if include_reply:
        data["reply_text"] = row.reply_text
        data["error"] = row.error
    return data


def _add_event(db: Session, message: H5ChatMessage, event_type: str, payload: Optional[Dict[str, Any]] = None) -> H5ChatEvent:
    row = H5ChatEvent(
        message_id=message.id,
        user_id=message.user_id,
        event_type=(event_type or "progress")[:32],
        payload=payload or {},
        created_at=datetime.utcnow(),
    )
    db.add(row)
    return row


def _message_for_user(db: Session, message_id: str, user_id: int) -> H5ChatMessage:
    row = db.query(H5ChatMessage).filter(H5ChatMessage.id == message_id, H5ChatMessage.user_id == user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    return row


def _user_from_query_token(db: Session, token: str) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    raw = (token or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw:
        raise credentials_exception
    try:
        payload = jwt.decode(raw, settings.secret_key, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise credentials_exception
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception
    return user


def _event_stream_line(row: H5ChatEvent) -> str:
    data = json.dumps(_serialize_event(row), ensure_ascii=False, separators=(",", ":"))
    return f"id: {row.id}\nevent: {row.event_type}\ndata: {data}\n\n"


def _header_installation_id(request: Request) -> str:
    return (
        request.headers.get(INSTALLATION_ID_HEADER)
        or request.headers.get("x-installation-id")
        or ""
    ).strip()


def _touch_installation_slot_lazy(db: Session, user_id: int, installation_id: str) -> None:
    if not installation_id:
        return
    now = datetime.utcnow()
    slot = (
        db.query(UserInstallation)
        .filter(UserInstallation.user_id == user_id, UserInstallation.installation_id == installation_id)
        .first()
    )
    if slot:
        last_seen_at = slot.last_seen_at
        if last_seen_at and (now - last_seen_at).total_seconds() < _PENDING_INSTALLATION_TOUCH_MIN_SECONDS:
            return
        slot.last_seen_at = now
        db.commit()
        return
    ensure_installation_slot(db, user_id, installation_id)


def _pending_cache_key(user_id: int, installation_id: str) -> str:
    return f"h5:pending-empty:{user_id}:{installation_id or '-'}"


def _pending_empty_recent(key: str) -> bool:
    if cache_flag_recent(key):
        return True
    ts = _pending_empty_cache.get(key)
    return bool(ts and (time.monotonic() - ts) < _PENDING_EMPTY_CACHE_SECONDS)


def _mark_pending_empty(key: str) -> None:
    cache_mark_flag(key, _PENDING_EMPTY_CACHE_SECONDS)
    _pending_empty_cache[key] = time.monotonic()
    if len(_pending_empty_cache) > 5000:
        cutoff = time.monotonic() - 30
        for old_key, ts in list(_pending_empty_cache.items())[:1000]:
            if ts < cutoff:
                _pending_empty_cache.pop(old_key, None)


def _clear_pending_empty(key: str) -> None:
    cache_delete(key)
    _pending_empty_cache.pop(key, None)


def _clear_pending_empty_for_target(user_id: int, installation_id: Optional[str]) -> None:
    _clear_pending_empty(_pending_cache_key(user_id, installation_id or ""))
    if installation_id:
        _clear_pending_empty(_pending_cache_key(user_id, ""))


def _heartbeat_cache_key(user_id: int, installation_id: str) -> str:
    return f"h5:heartbeat-fast-ack:{user_id}:{installation_id or '-'}"


def _heartbeat_fast_ack_recent(key: str) -> bool:
    if cache_flag_recent(key):
        return True
    ts = _heartbeat_ack_cache.get(key)
    return bool(ts and (time.monotonic() - ts) < _DEVICE_HEARTBEAT_FAST_ACK_SECONDS)


def _mark_heartbeat_fast_ack(key: str) -> None:
    cache_mark_flag(key, _DEVICE_HEARTBEAT_FAST_ACK_SECONDS)
    _heartbeat_ack_cache[key] = time.monotonic()
    if len(_heartbeat_ack_cache) > 5000:
        cutoff = time.monotonic() - (_DEVICE_HEARTBEAT_FAST_ACK_SECONDS * 2)
        for old_key, ts in list(_heartbeat_ack_cache.items())[:1000]:
            if ts < cutoff:
                _heartbeat_ack_cache.pop(old_key, None)


def _public_base_url(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",", 1)[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
        or "h5.bhzn.top"
    ).split(",", 1)[0].strip()
    return f"{proto}://{host}".rstrip("/")


def _safe_upload_filename(filename: str) -> str:
    name = (filename or "").strip()
    if not name or not _UPLOAD_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail="文件不存在")
    return name


def _image_media_type(path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(path.suffix.lower(), "application/octet-stream")


def _h5_static_media_type(path: Path) -> str:
    return {
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
    }.get(path.suffix.lower(), _image_media_type(path))


def _plist_text(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


def _plist_data(raw: bytes) -> str:
    encoded = base64.b64encode(raw).decode("ascii")
    return "\n".join(encoded[i : i + 68] for i in range(0, len(encoded), 68))


def _webclip_icon_xml() -> str:
    icon_path = _H5_STATIC_DIR / "bihu_256.png"
    if not icon_path.is_file():
        icon_path = _H5_STATIC_DIR / "bihu_32.png"
    if not icon_path.is_file():
        return ""
    return f"""
            <key>Icon</key>
            <data>
{_plist_data(icon_path.read_bytes())}
            </data>"""


def _ios_webclip_mobileconfig() -> str:
    webclip_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{_H5_WEBCLIP_URL}#webclip"))
    profile_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{_H5_WEBCLIP_URL}#profile"))
    identifier = "top.bhzn.h5.webclip"
    label = _plist_text(_H5_WEBCLIP_LABEL)
    webclip_url = _plist_text(_H5_WEBCLIP_URL)
    icon_xml = _webclip_icon_xml()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>PayloadContent</key>
    <array>
      <dict>
        <key>FullScreen</key>
        <true/>
        <key>IgnoreManifestScope</key>
        <true/>{icon_xml}
        <key>IsRemovable</key>
        <true/>
        <key>Label</key>
        <string>{label}</string>
        <key>Precomposed</key>
        <false/>
        <key>URL</key>
        <string>{webclip_url}</string>
        <key>PayloadDescription</key>
        <string>将必火AI员工添加到 iPhone 桌面。</string>
        <key>PayloadDisplayName</key>
        <string>{label}</string>
        <key>PayloadIdentifier</key>
        <string>{identifier}.clip</string>
        <key>PayloadOrganization</key>
        <string>必火智能</string>
        <key>PayloadType</key>
        <string>com.apple.webClip.managed</string>
        <key>PayloadUUID</key>
        <string>{webclip_uuid}</string>
        <key>PayloadVersion</key>
        <integer>1</integer>
      </dict>
    </array>
    <key>PayloadDescription</key>
    <string>安装后会在桌面创建必火AI员工快捷方式。</string>
    <key>PayloadDisplayName</key>
    <string>必火AI员工桌面入口</string>
    <key>PayloadIdentifier</key>
    <string>{identifier}</string>
    <key>PayloadOrganization</key>
    <string>必火智能</string>
    <key>PayloadRemovalDisallowed</key>
    <false/>
    <key>PayloadType</key>
    <string>Configuration</string>
    <key>PayloadUUID</key>
    <string>{profile_uuid}</string>
    <key>PayloadVersion</key>
    <integer>1</integer>
  </dict>
</plist>
"""


def _download_filename(value: str, url: str) -> str:
    raw = (value or "").strip()
    if not raw:
        parsed = urlparse(url)
        raw = unquote((parsed.path or "").rsplit("/", 1)[-1])
    name = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = re.sub(r"[\r\n\"]+", "", name)
    return (name or "lobster-media")[:160]


def _content_disposition(kind: str, filename: str) -> str:
    disposition = "attachment" if kind == "attachment" else "inline"
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "lobster-media"
    ascii_name = ascii_name.replace("\\", "_").replace("/", "_").replace('"', "")
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _assert_public_remote_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="下载链接无效")
    host = parsed.hostname.strip().lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(status_code=400, detail="不支持本机链接")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="下载域名无法解析") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise HTTPException(status_code=400, detail="不支持内网链接")
    return url


async def _remote_media_response(request: Request, url: str, disposition: str, filename: str) -> StreamingResponse:
    req_headers = {"User-Agent": "Lobster-H5/1.0", "Accept": "*/*"}
    range_header = request.headers.get("range")
    if range_header and disposition != "attachment":
        req_headers["Range"] = range_header
    timeout = httpx.Timeout(120.0, connect=10.0, read=120.0, write=30.0, pool=10.0)
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False)
    try:
        current_url = url
        resp: httpx.Response | None = None
        for _ in range(6):
            resp = await client.send(client.build_request("GET", current_url, headers=req_headers), stream=True)
            if resp.status_code in {301, 302, 303, 307, 308} and resp.headers.get("location"):
                location = urljoin(current_url, resp.headers["location"])
                await resp.aclose()
                current_url = _assert_public_remote_url(location)
                continue
            break
        if resp is None:
            await client.aclose()
            raise HTTPException(status_code=502, detail="远端素材下载失败")
        parsed = urlparse(current_url)
        ext = Path(parsed.path).suffix.lower()
        if resp.status_code >= 400:
            await resp.aclose()
            await client.aclose()
            raise HTTPException(status_code=502, detail=f"远端素材下载失败: HTTP {resp.status_code}")
        length_raw = (resp.headers.get("content-length") or "").strip()
        if length_raw.isdigit() and int(length_raw) > _MAX_MEDIA_PROXY_BYTES:
            await resp.aclose()
            await client.aclose()
            raise HTTPException(status_code=413, detail="素材文件过大")
        content_type = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if ext not in _MEDIA_TYPE_BY_EXT and not content_type.startswith(("video/", "audio/", "image/")):
            await resp.aclose()
            await client.aclose()
            raise HTTPException(status_code=400, detail="仅支持媒体素材链接")
        media_type = _MEDIA_TYPE_BY_EXT.get(ext) or content_type or "application/octet-stream"
        headers = {
            "Content-Disposition": _content_disposition(disposition, filename),
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        }
        for name in ("content-length", "content-range", "accept-ranges"):
            value = resp.headers.get(name)
            if value:
                headers["-".join(part.capitalize() for part in name.split("-"))] = value

        async def gen():
            try:
                async for chunk in resp.aiter_bytes(256 * 1024):
                    if chunk:
                        yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(gen(), status_code=resp.status_code, media_type=media_type, headers=headers)
    except HTTPException:
        raise
    except httpx.RequestError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail="远端素材下载失败") from exc


@router.get("/h5", include_in_schema=False)
def h5_page():
    if not _H5_INDEX.is_file():
        raise HTTPException(status_code=404, detail="H5 页面未打包")
    return FileResponse(str(_H5_INDEX), headers=_H5_INDEX_HEADERS)


@router.get("/h5-static/{filename}", include_in_schema=False)
def h5_static_asset(filename: str):
    safe = _safe_upload_filename(filename)
    path = (_H5_STATIC_DIR / safe).resolve()
    root = _H5_STATIC_DIR.resolve()
    if root not in path.parents or not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".css", ".js"}:
        raise HTTPException(status_code=404, detail="文件不存在")
    cache_control = "no-store, no-cache, must-revalidate, max-age=0" if path.suffix.lower() in {".css", ".js"} else "public, max-age=86400"
    return FileResponse(str(path), media_type=_h5_static_media_type(path), headers={"Cache-Control": cache_control})


@router.get("/install/ios-webclip.mobileconfig", include_in_schema=False)
def ios_webclip_mobileconfig():
    return Response(
        content=_ios_webclip_mobileconfig().encode("utf-8"),
        media_type="application/x-apple-aspen-config",
        headers={
            "Content-Disposition": "attachment; filename=\"bihuo-ai-webclip.mobileconfig\"",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/", include_in_schema=False)
def h5_root(request: Request):
    host = (request.headers.get("host") or "").split(":", 1)[0].lower()
    if host == "h5.bhzn.top" or host.startswith("h5."):
        return h5_page()
    raise HTTPException(status_code=404, detail="Not Found")


@router.post("/api/h5-chat/uploads", summary="H5 上传图片素材")
async def upload_h5_chat_image(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if content_type not in _IMAGE_EXT_BY_TYPE:
        raise HTTPException(status_code=400, detail="仅支持上传图片")
    raw = await file.read(_MAX_H5_UPLOAD_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="图片为空")
    if len(raw) > _MAX_H5_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="图片不能超过 15MB")
    _H5_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"u{owner_user.id}_{uuid.uuid4().hex}{_IMAGE_EXT_BY_TYPE[content_type]}"
    path = _H5_UPLOAD_DIR / filename
    path.write_bytes(raw)
    url = f"{_public_base_url(request)}/api/h5-chat/uploads/{filename}"
    return {
        "ok": True,
        "url": url,
        "filename": file.filename or filename,
        "content_type": content_type,
        "size": len(raw),
    }


@router.get("/api/h5-chat/uploads/{filename}", include_in_schema=False)
def get_h5_chat_upload(filename: str):
    safe = _safe_upload_filename(filename)
    path = (_H5_UPLOAD_DIR / safe).resolve()
    root = _H5_UPLOAD_DIR.resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    suffix = path.suffix.lower()
    media_type = _image_media_type(path)
    return FileResponse(str(path), media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/h5-chat/media", summary="H5 同源打开/下载远端素材")
async def proxy_h5_chat_media(
    request: Request,
    url: str = Query(..., min_length=8, max_length=2000),
    disposition: str = Query("inline"),
    filename: str = Query("", max_length=200),
    token: str = Query(""),
):
    db = SessionLocal()
    try:
        _user_from_query_token(db, token)
    finally:
        db.close()
    remote_url = _assert_public_remote_url(url)
    kind = "attachment" if (disposition or "").strip().lower() == "attachment" else "inline"
    safe_filename = _download_filename(filename, remote_url)
    return await _remote_media_response(request, remote_url, kind, safe_filename)


@router.post("/api/h5-chat/messages", summary="H5 创建远程会话消息")
def create_h5_message(
    body: H5MessageCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    mode = "direct"
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="消息不能为空")

    target_installation = (body.installation_id or "").strip() or None
    request_installation = optional_installation_id_from_request(request)
    if request_installation:
        ensure_installation_slot(db, owner_user.id, request_installation)

    row = H5ChatMessage(
        id=uuid.uuid4().hex,
        user_id=owner_user.id,
        installation_id=target_installation,
        mode=mode,
        content=content,
        status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    _add_event(db, row, "queued", {"mode": mode})
    _clear_pending_empty_for_target(owner_user.id, target_installation)
    db.commit()
    db.refresh(row)
    return {"ok": True, "message": _serialize_message(row), "events": []}


@router.get("/api/h5-chat/messages", summary="H5 list recent remote chat messages")
def list_h5_messages(
    limit: int = Query(40, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    rows = (
        db.query(H5ChatMessage)
        .filter(H5ChatMessage.user_id == owner_user.id)
        .order_by(H5ChatMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    rows = list(reversed(rows))
    message_ids = [row.id for row in rows]
    events_by_message: Dict[str, List[Dict[str, Any]]] = {message_id: [] for message_id in message_ids}
    if message_ids:
        events = (
            db.query(H5ChatEvent)
            .filter(H5ChatEvent.user_id == owner_user.id, H5ChatEvent.message_id.in_(message_ids))
            .order_by(H5ChatEvent.id.asc())
            .all()
        )
        for event in events:
            events_by_message.setdefault(event.message_id, []).append(_serialize_event(event))
    return {
        "ok": True,
        "messages": [
            {
                "message": _serialize_message(row),
                "events": events_by_message.get(row.id, []),
            }
            for row in rows
        ],
    }


@router.get("/api/h5-chat/messages/{message_id}", summary="H5 查询消息状态")
def get_h5_message(
    message_id: str,
    after_event_id: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    row = _message_for_user(db, message_id, owner_user.id)
    events = (
        db.query(H5ChatEvent)
        .filter(H5ChatEvent.message_id == row.id, H5ChatEvent.id > after_event_id)
        .order_by(H5ChatEvent.id.asc())
        .limit(200)
        .all()
    )
    return {
        "ok": True,
        "message": _serialize_message(row),
        "events": [_serialize_event(e) for e in events],
    }


@router.get("/api/h5-chat/messages/{message_id}/events", summary="H5 消息 SSE")
async def stream_h5_message_events(
    request: Request,
    message_id: str,
    token: str = Query(""),
    last_event_id: int = Query(0, ge=0),
):
    initial_last = last_event_id
    header_last = (request.headers.get("last-event-id") or "").strip()
    if header_last.isdigit():
        initial_last = max(initial_last, int(header_last))

    db0 = SessionLocal()
    try:
        user = _user_from_query_token(db0, token)
        owner_user = online_user_for_mobile_user(db0, user)
        msg = _message_for_user(db0, message_id, owner_user.id)
        user_id = owner_user.id
        if msg.status in _FINAL_STATUSES:
            initial_last = max(0, initial_last)
    finally:
        db0.close()

    async def gen():
        last_id = initial_last
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            db = SessionLocal()
            try:
                msg = _message_for_user(db, message_id, user_id)
                events: List[H5ChatEvent] = (
                    db.query(H5ChatEvent)
                    .filter(H5ChatEvent.message_id == message_id, H5ChatEvent.id > last_id)
                    .order_by(H5ChatEvent.id.asc())
                    .limit(100)
                    .all()
                )
                for ev in events:
                    last_id = max(last_id, int(ev.id))
                    yield _event_stream_line(ev)
                if msg.status in _FINAL_STATUSES and not events:
                    break
            except HTTPException:
                yield "event: error\ndata: {\"detail\":\"消息不存在\"}\n\n"
                break
            finally:
                db.close()

            idle += 1
            if idle % 15 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/h5-chat/device-heartbeat", summary="本地 online 上报 H5 远程会话在线状态")
def h5_device_heartbeat(
    body: H5HeartbeatIn,
    request: Request,
    current_user_id: int = Depends(get_current_user_id_from_token),
    db: Session = Depends(get_db),
):
    xi = _header_installation_id(request)
    if not xi:
        raise HTTPException(status_code=400, detail="缺少 X-Installation-Id")
    heartbeat_key = _heartbeat_cache_key(current_user_id, xi)
    account_snapshot = _normalize_publish_account_snapshot(body.publish_accounts)
    if account_snapshot is None and _heartbeat_fast_ack_recent(heartbeat_key):
        return {"ok": True, "installation_id": xi, "throttled": True}
    _mark_heartbeat_fast_ack(heartbeat_key)
    now = datetime.utcnow()
    slot = (
        db.query(UserInstallation)
        .filter(UserInstallation.user_id == current_user_id, UserInstallation.installation_id == xi)
        .first()
    )
    if slot:
        slot_seen_at = slot.last_seen_at
        if not slot_seen_at or (now - slot_seen_at).total_seconds() >= _DEVICE_HEARTBEAT_WRITE_MIN_SECONDS:
            slot.last_seen_at = now
            db.commit()
    else:
        ensure_installation_slot(db, current_user_id, xi)
    row = (
        db.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.user_id == current_user_id, H5ChatDevicePresence.installation_id == xi)
        .first()
    )
    if row:
        previous_seen_at = row.last_seen_at
        should_set_display_name = body.display_name is not None and not (row.display_name or "").strip()
        if (
            previous_seen_at
            and (now - previous_seen_at).total_seconds() < _DEVICE_HEARTBEAT_WRITE_MIN_SECONDS
            and not should_set_display_name
            and account_snapshot is None
        ):
            return {"ok": True, "installation_id": xi, "last_seen_at": _iso(previous_seen_at), "throttled": True}
        row.last_seen_at = now
        if should_set_display_name:
            row.display_name = body.display_name.strip()[:128] or None
        if account_snapshot is not None:
            row.account_payload = {"accounts": account_snapshot, "reported_at": now.isoformat()}
    else:
        row = H5ChatDevicePresence(
            user_id=current_user_id,
            installation_id=xi,
            display_name=(body.display_name or "").strip()[:128] or None,
            account_payload={"accounts": account_snapshot, "reported_at": now.isoformat()} if account_snapshot is not None else None,
            last_seen_at=now,
            created_at=now,
        )
        db.add(row)
    db.commit()
    return {"ok": True, "installation_id": xi, "last_seen_at": _iso(now)}


@router.patch("/api/h5-chat/devices/{installation_id}/display-name", summary="H5 设置 online 员工昵称")
def h5_update_device_display_name(
    installation_id: str,
    body: H5DeviceDisplayNameIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    iid = (installation_id or "").strip()
    if not iid:
        raise HTTPException(status_code=400, detail="缺少设备 ID")
    row = (
        db.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.user_id == owner_user.id, H5ChatDevicePresence.installation_id == iid)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="设备不存在")
    row.display_name = (body.display_name or "").strip()[:128] or None
    db.add(row)
    db.commit()
    db.refresh(row)
    now = datetime.utcnow()
    age = (now - row.last_seen_at).total_seconds() if row.last_seen_at else 999999
    return {
        "ok": True,
        "device": {
            "installation_id": row.installation_id,
            "display_name": row.display_name,
            "last_seen_at": _iso(row.last_seen_at),
            "online": age <= _DEVICE_ONLINE_TTL_SECONDS,
            "publish_account_count": len((row.account_payload or {}).get("accounts") or []) if isinstance(row.account_payload, dict) else 0,
        },
    }


@router.get("/api/h5-chat/devices/status", summary="H5 查询本地 online 是否在线")
def h5_devices_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = datetime.utcnow()
    owner_user = online_user_for_mobile_user(db, current_user)
    rows = (
        db.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.user_id == owner_user.id)
        .order_by(H5ChatDevicePresence.last_seen_at.desc())
        .limit(20)
        .all()
    )
    devices = []
    for r in rows:
        age = (now - r.last_seen_at).total_seconds() if r.last_seen_at else 999999
        devices.append(
            {
                "installation_id": r.installation_id,
                "display_name": r.display_name,
                "last_seen_at": _iso(r.last_seen_at),
                "online": age <= _DEVICE_ONLINE_TTL_SECONDS,
                "publish_account_count": len((r.account_payload or {}).get("accounts") or []) if isinstance(r.account_payload, dict) else 0,
            }
        )
    return {"ok": True, "online": any(d["online"] for d in devices), "devices": devices}


@router.get("/api/h5-chat/mounted-accounts", summary="H5 已挂载平台账号列表")
def h5_mounted_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    return _mounted_accounts_payload(db, owner_user.id)


@router.post("/api/h5-chat/mounted-accounts/wechat-auto-reply", summary="H5 设置个人微信自动回复")
def h5_set_wechat_auto_reply(
    body: H5WechatAutoReplyIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    payload = _mounted_accounts_payload(db, owner_user.id)
    target_installation = (body.installation_id or "").strip()
    account = next(
        (
            row
            for row in payload.get("accounts", [])
            if row.get("scope") == "wechat"
            and row.get("online")
            and (not target_installation or str(row.get("installation_id") or "") == target_installation)
        ),
        None,
    )
    if not account:
        raise HTTPException(status_code=404, detail="未找到在线微信设备")

    installation_id = str(account.get("installation_id") or "").strip()
    if not installation_id:
        raise HTTPException(status_code=400, detail="微信设备缺少 installation_id")

    account_key = (body.account_key or str(account.get("account_key") or "") or "wechat:pc-default").strip()
    account_id = (body.account_id or str(account.get("account_id") or "") or "pc-wechat-default").strip()
    interval_seconds = max(300, min(int(body.interval_seconds or 1800), 86400))
    now = datetime.utcnow()

    pref = (
        db.query(H5MountedAccountDefault)
        .filter(H5MountedAccountDefault.user_id == owner_user.id, H5MountedAccountDefault.scope == "wechat_auto_reply")
        .first()
    )
    if not pref:
        pref = H5MountedAccountDefault(user_id=owner_user.id, scope="wechat_auto_reply", created_at=now)
        db.add(pref)
    pref.account_key = account_key
    pref.platform = "wechat"
    pref.account_id = account_id
    pref.account_label = str(account.get("nickname") or "本机微信")[:255]
    pref.installation_id = installation_id
    pref.source = "pc_wechat"
    pref.payload = {
        "enabled": bool(body.enabled),
        "interval_seconds": interval_seconds,
        "account_id": account_id,
        "account_key": account_key,
    }
    pref.updated_at = now

    command = {
        "action": "native_wechat_auto_reply_config",
        "account_id": account_id,
        "enabled": bool(body.enabled),
        "interval_seconds": interval_seconds,
    }
    message = H5ChatMessage(
        id=uuid.uuid4().hex,
        user_id=owner_user.id,
        installation_id=installation_id,
        mode="client_command",
        content=_H5_CLIENT_COMMAND_PREFIX + json.dumps(command, ensure_ascii=False, separators=(",", ":")),
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db.add(message)
    _add_event(db, message, "queued", {"mode": "client_command", "action": command["action"]})
    _clear_pending_empty_for_target(owner_user.id, installation_id)
    db.commit()
    db.refresh(message)
    result = _mounted_accounts_payload(db, owner_user.id)
    return {
        "ok": True,
        "message": _serialize_message(message),
        "accounts": result.get("accounts", []),
        "defaults": result.get("defaults", {}),
    }


@router.post("/api/h5-chat/mounted-accounts/default", summary="H5 设置默认挂载账号")
def h5_set_mounted_account_default(
    body: H5MountedAccountDefaultIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    requested_scope = (body.scope or "").strip().lower()
    if requested_scope == "publish" or requested_scope.startswith("publish:"):
        account_scope = "publish"
    elif requested_scope == "douyin":
        account_scope = "douyin"
    else:
        raise HTTPException(status_code=400, detail="该类型不支持设置默认账号")
    account_key = (body.account_key or "").strip()
    payload = _mounted_accounts_payload(db, owner_user.id)
    account = next(
        (
            row
            for row in payload.get("accounts", [])
            if row.get("scope") == account_scope and row.get("account_key") == account_key and row.get("defaultable")
        ),
        None,
    )
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在或暂不可设为默认")
    platform = str(account.get("platform") or "").strip().lower()
    if account_scope == "publish":
        requested_platform = requested_scope.split(":", 1)[1].strip().lower() if ":" in requested_scope else ""
        if requested_platform and requested_platform != platform:
            raise HTTPException(status_code=400, detail="默认账号平台不匹配")
        scope = _publish_default_scope(platform)
    else:
        scope = account_scope
    now = datetime.utcnow()
    row = (
        db.query(H5MountedAccountDefault)
        .filter(H5MountedAccountDefault.user_id == owner_user.id, H5MountedAccountDefault.scope == scope)
        .first()
    )
    if not row:
        row = H5MountedAccountDefault(user_id=owner_user.id, scope=scope, created_at=now)
        db.add(row)
    row.account_key = account_key
    row.platform = platform[:64] or None
    row.account_id = str(account.get("account_id") or "")[:128] or None
    row.account_label = str(account.get("nickname") or "")[:255] or None
    row.installation_id = str(account.get("installation_id") or "")[:128] or None
    row.source = str(account.get("source") or "")[:64] or None
    row.payload = account
    row.updated_at = now
    db.commit()
    db.refresh(row)
    result = _mounted_accounts_payload(db, owner_user.id)
    return {"ok": True, "default": _mounted_default_payload(row), "accounts": result.get("accounts", []), "defaults": result.get("defaults", {})}


@router.get("/api/h5-chat/pending", summary="本地 online 轮询领取 H5 消息")
def h5_pending_messages(
    request: Request,
    limit: int = Query(2, ge=1, le=10),
    current_user_id: int = Depends(get_current_user_id_from_token),
    db: Session = Depends(get_db),
):
    xi = _header_installation_id(request)
    pending_key = _pending_cache_key(current_user_id, xi)
    if _pending_empty_recent(pending_key):
        return {"ok": True, "items": [], "throttled": True}
    if xi:
        _touch_installation_slot_lazy(db, current_user_id, xi)
    turn_billing_supported = (
        request.headers.get(_CHAT_TURN_BILLING_SUPPORT_HEADER)
        or request.headers.get(_CHAT_TURN_BILLING_SUPPORT_HEADER.lower())
        or ""
    ).strip().lower() in {"1", "true", "yes", "pre_deduct_v1", "chat_turn_pre_deduct"}

    now = datetime.utcnow()
    stale_cutoff = now - timedelta(minutes=10)
    stale_rows = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.user_id == current_user_id,
            H5ChatMessage.status == "processing",
            H5ChatMessage.claimed_at.isnot(None),
            H5ChatMessage.claimed_at < stale_cutoff,
        )
        .limit(20)
        .all()
    )
    for row in stale_rows:
        row.status = "pending"
        row.claimed_by_installation_id = None
        row.claimed_at = None
        row.updated_at = now
        _add_event(db, row, "queued", {"reason": "processing_timeout_requeued"})

    q = (
        db.query(H5ChatMessage)
        .filter(H5ChatMessage.user_id == current_user_id, H5ChatMessage.status == "pending")
        .filter(H5ChatMessage.mode != "scheduled_task")
        .filter(or_(H5ChatMessage.installation_id.is_(None), H5ChatMessage.installation_id == xi))
        .order_by(H5ChatMessage.created_at.asc())
    )
    rows = q.limit(limit).all()
    claimed_rows: List[H5ChatMessage] = []
    current_user: Optional[User] = None
    for row in rows:
        turn_id = f"h5:{row.id}"
        billable_message = turn_billing_supported and str(row.mode or "").strip() == "direct"
        if billable_message:
            if current_user is None:
                current_user = db.query(User).filter(User.id == current_user_id).first()
                if current_user is None:
                    raise HTTPException(status_code=401, detail="无法验证凭证")
            try:
                charge_chat_turn_once(
                    db,
                    current_user,
                    turn_id,
                    source="h5_chat",
                    session_id=f"h5-{row.id}",
                    context_id=f"h5-{row.id}",
                    message_id=row.id,
                )
            except HTTPException as exc:
                if exc.status_code != 402:
                    raise
                detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                row.status = "failed"
                row.error = detail
                row.finished_at = now
                row.updated_at = now
                _add_event(db, row, "error", {"error": detail, "billing": "insufficient_credits"})
                db.commit()
                continue
        row.status = "processing"
        row.claimed_by_installation_id = xi or "unknown"
        row.claimed_at = now
        row.updated_at = now
        claimed_payload = {"installation_id": xi or ""}
        if billable_message:
            claimed_payload.update({"chat_turn_charged": True, "chat_turn_id": turn_id})
        _add_event(db, row, "claimed", claimed_payload)
        db.commit()
        claimed_rows.append(row)
    if claimed_rows:
        _clear_pending_empty(pending_key)
    else:
        _mark_pending_empty(pending_key)
    return {
        "ok": True,
        "items": [
            _serialize_message(
                r,
                include_reply=False,
                chat_turn_charged=turn_billing_supported and str(r.mode or "").strip() == "direct",
            )
            for r in claimed_rows
        ],
    }


def _assert_worker_can_update(message: H5ChatMessage, xi: str) -> None:
    claimed = (message.claimed_by_installation_id or "").strip()
    if claimed and xi and claimed != xi:
        raise HTTPException(status_code=409, detail="消息已由其他设备处理")


@router.post("/api/h5-chat/messages/{message_id}/event", summary="本地 online 提交 H5 进度事件")
def h5_submit_event(
    message_id: str,
    body: H5ChatEventIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _message_for_user(db, message_id, current_user.id)
    _assert_worker_can_update(row, _header_installation_id(request))
    row.updated_at = datetime.utcnow()
    _add_event(db, row, body.type, body.payload)
    db.commit()
    return {"ok": True}


@router.post("/api/h5-chat/messages/{message_id}/complete", summary="本地 online 提交 H5 最终回复")
def h5_complete_message(
    message_id: str,
    body: H5ChatCompleteIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _message_for_user(db, message_id, current_user.id)
    _assert_worker_can_update(row, _header_installation_id(request))
    now = datetime.utcnow()
    error = (body.error or "").strip()
    reply = (body.reply_text or "").strip()
    row.status = "failed" if error else "completed"
    row.reply_text = reply or None
    row.error = error or None
    row.finished_at = now
    row.updated_at = now
    payload = dict(body.payload or {})
    if error:
        payload.setdefault("error", error)
        _add_event(db, row, "error", payload)
    else:
        payload.setdefault("reply_text", reply)
        _add_event(db, row, "final", payload)
    db.commit()
    return {"ok": True, "status": row.status}
