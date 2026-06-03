from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import SessionLocal, get_db
from ..models import H5ChatDevicePresence, H5ChatEvent, H5ChatMessage, User
from .auth import ALGORITHM, get_current_user
from .installation_slots import INSTALLATION_ID_HEADER, ensure_installation_slot, optional_installation_id_from_request
from .mobile_identity import online_user_for_mobile_user
from .sutui_chat_proxy import charge_chat_turn_once

logger = logging.getLogger(__name__)
router = APIRouter()

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_H5_INDEX = _ROOT / "h5_static" / "index.html"
_H5_STATIC_DIR = _ROOT / "h5_static"
_H5_UPLOAD_DIR = _ROOT / "temp_assets" / "h5_chat_uploads"
_VALID_MODES = {"direct"}
_FINAL_STATUSES = {"completed", "failed", "cancelled"}
_CHAT_TURN_BILLING_SUPPORT_HEADER = "X-Lobster-Chat-Turn-Billing"
_UPLOAD_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_H5_UPLOAD_BYTES = 15 * 1024 * 1024
_MAX_MEDIA_PROXY_BYTES = 1024 * 1024 * 1024
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


class H5DeviceDisplayNameIn(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=128)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


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
    return FileResponse(str(_H5_INDEX))


@router.get("/h5-static/{filename}", include_in_schema=False)
def h5_static_asset(filename: str):
    safe = _safe_upload_filename(filename)
    path = (_H5_STATIC_DIR / safe).resolve()
    root = _H5_STATIC_DIR.resolve()
    if root not in path.parents or not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(str(path), media_type=_image_media_type(path), headers={"Cache-Control": "public, max-age=86400"})


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
    db: Session = Depends(get_db),
):
    _user_from_query_token(db, token)
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    xi = _header_installation_id(request)
    if not xi:
        raise HTTPException(status_code=400, detail="缺少 X-Installation-Id")
    ensure_installation_slot(db, current_user.id, xi)
    now = datetime.utcnow()
    row = (
        db.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.user_id == current_user.id, H5ChatDevicePresence.installation_id == xi)
        .first()
    )
    if row:
        row.last_seen_at = now
        if body.display_name is not None and not (row.display_name or "").strip():
            row.display_name = body.display_name.strip()[:128] or None
    else:
        row = H5ChatDevicePresence(
            user_id=current_user.id,
            installation_id=xi,
            display_name=(body.display_name or "").strip()[:128] or None,
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
            "online": age <= 20,
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
                "online": age <= 20,
            }
        )
    return {"ok": True, "online": any(d["online"] for d in devices), "devices": devices}


@router.get("/api/h5-chat/pending", summary="本地 online 轮询领取 H5 消息")
def h5_pending_messages(
    request: Request,
    limit: int = Query(2, ge=1, le=10),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    xi = _header_installation_id(request)
    if xi:
        ensure_installation_slot(db, current_user.id, xi)
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
            H5ChatMessage.user_id == current_user.id,
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
        .filter(H5ChatMessage.user_id == current_user.id, H5ChatMessage.status == "pending")
        .filter(H5ChatMessage.mode != "scheduled_task")
        .filter(or_(H5ChatMessage.installation_id.is_(None), H5ChatMessage.installation_id == xi))
        .order_by(H5ChatMessage.created_at.asc())
    )
    rows = q.limit(limit).all()
    claimed_rows: List[H5ChatMessage] = []
    for row in rows:
        turn_id = f"h5:{row.id}"
        if turn_billing_supported:
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
        if turn_billing_supported:
            claimed_payload.update({"chat_turn_charged": True, "chat_turn_id": turn_id})
        _add_event(db, row, "claimed", claimed_payload)
        db.commit()
        claimed_rows.append(row)
    return {"ok": True, "items": [_serialize_message(r, include_reply=False, chat_turn_charged=turn_billing_supported) for r in claimed_rows]}


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
