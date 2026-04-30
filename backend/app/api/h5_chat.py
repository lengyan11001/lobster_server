from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
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

logger = logging.getLogger(__name__)
router = APIRouter()

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_H5_INDEX = _ROOT / "h5_static" / "index.html"
_VALID_MODES = {"direct", "openclaw"}
_FINAL_STATUSES = {"completed", "failed", "cancelled"}


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


def _serialize_message(row: H5ChatMessage, *, include_reply: bool = True) -> Dict[str, Any]:
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


@router.get("/h5", include_in_schema=False)
def h5_page():
    if not _H5_INDEX.is_file():
        raise HTTPException(status_code=404, detail="H5 页面未打包")
    return FileResponse(str(_H5_INDEX))


@router.get("/", include_in_schema=False)
def h5_root(request: Request):
    host = (request.headers.get("host") or "").split(":", 1)[0].lower()
    if host == "h5.bhzn.top" or host.startswith("h5."):
        return h5_page()
    raise HTTPException(status_code=404, detail="Not Found")


@router.post("/api/h5-chat/messages", summary="H5 创建远程会话消息")
def create_h5_message(
    body: H5MessageCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mode = (body.mode or "direct").strip().lower()
    if mode not in _VALID_MODES:
        mode = "direct"
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="消息不能为空")

    target_installation = (body.installation_id or "").strip() or None
    request_installation = optional_installation_id_from_request(request)
    if request_installation:
        ensure_installation_slot(db, current_user.id, request_installation)

    row = H5ChatMessage(
        id=uuid.uuid4().hex,
        user_id=current_user.id,
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
    rows = (
        db.query(H5ChatMessage)
        .filter(H5ChatMessage.user_id == current_user.id)
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
            .filter(H5ChatEvent.user_id == current_user.id, H5ChatEvent.message_id.in_(message_ids))
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
    row = _message_for_user(db, message_id, current_user.id)
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
        msg = _message_for_user(db0, message_id, user.id)
        user_id = user.id
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
        if body.display_name is not None:
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


@router.get("/api/h5-chat/devices/status", summary="H5 查询本地 online 是否在线")
def h5_devices_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = datetime.utcnow()
    rows = (
        db.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.user_id == current_user.id)
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
        .filter(or_(H5ChatMessage.installation_id.is_(None), H5ChatMessage.installation_id == xi))
        .order_by(H5ChatMessage.created_at.asc())
    )
    rows = q.limit(limit).all()
    for row in rows:
        row.status = "processing"
        row.claimed_by_installation_id = xi or "unknown"
        row.claimed_at = now
        row.updated_at = now
        _add_event(db, row, "claimed", {"installation_id": xi or ""})
    db.commit()
    return {"ok": True, "items": [_serialize_message(r, include_reply=False) for r in rows]}


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
