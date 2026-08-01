from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Asset, H5ChatApproval, H5ChatDevicePresence, H5ChatMessage, H5ChatSession, User
from .auth import get_current_user
from .h5_chat import _DEVICE_ONLINE_TTL_SECONDS, _add_event, _serialize_message
from .installation_slots import optional_installation_id_from_request
from .mobile_identity import online_user_for_mobile_user

router = APIRouter()


class MastraAttachment(BaseModel):
    asset_id: str = Field(default="", max_length=64)
    url: str = Field(default="", max_length=2000)
    name: str = Field(default="", max_length=255)
    media_type: str = Field(default="", max_length=32)
    content_type: str = Field(default="", max_length=128)
    size: int = Field(default=0, ge=0, le=1024 * 1024 * 1024)


class MastraMessageCreate(BaseModel):
    content: str = Field(default="", max_length=8000)
    installation_id: Optional[str] = Field(default=None, max_length=128)
    session_id: str = Field(default="", max_length=64)
    attachments: List[MastraAttachment] = Field(default_factory=list, max_length=8)


class MastraSessionCreate(BaseModel):
    title: str = Field(default="新会话", max_length=160)
    permission_mode: str = Field(default="confirm", max_length=32)


class MastraSessionUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=160)
    permission_mode: Optional[str] = Field(default=None, max_length=32)


class ApprovalRequestCreate(BaseModel):
    parent_message_id: str = Field(..., min_length=8, max_length=64)
    task: str = Field(..., min_length=1, max_length=12000)
    reason: str = Field(default="", max_length=2000)
    execution_target: str = Field(default="auto", max_length=32)


class ApprovalDecisionCreate(BaseModel):
    decision: str = Field(..., max_length=16)


class OnlineDispatchCreate(BaseModel):
    task: str = Field(..., min_length=1, max_length=12000)
    reason: str = Field(default="", max_length=1000)
    parent_message_id: str = Field(..., min_length=8, max_length=64)
    installation_id: Optional[str] = Field(default=None, max_length=128)
    approval_id: str = Field(default="", max_length=64)


def _mastra_base_url() -> str:
    return (os.environ.get("LOBSTER_MASTRA_URL") or "http://127.0.0.1:4111").strip().rstrip("/")


def _selected_installation(request: Request, explicit: Optional[str]) -> Optional[str]:
    return (explicit or optional_installation_id_from_request(request) or "").strip() or None


def _online_available(db: Session, user_id: int, installation_id: Optional[str]) -> bool:
    cutoff = datetime.utcnow() - timedelta(seconds=_DEVICE_ONLINE_TTL_SECONDS)
    query = db.query(H5ChatDevicePresence).filter(
        H5ChatDevicePresence.user_id == user_id,
        H5ChatDevicePresence.last_seen_at >= cutoff,
    )
    if installation_id:
        query = query.filter(H5ChatDevicePresence.installation_id == installation_id)
    return query.first() is not None


def _attachment_media_type(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw in {"image", "video", "audio", "document", "file"}:
        return raw
    return "file"


def _normalize_attachments(db: Session, user_id: int, values: List[MastraAttachment]) -> list[dict]:
    normalized: list[dict] = []
    seen: set[str] = set()
    for value in values[:8]:
        asset_id = (value.asset_id or "").strip()
        url = (value.url or "").strip()
        name = (value.name or "").strip()
        media_type = _attachment_media_type(value.media_type)
        size = int(value.size or 0)
        if asset_id:
            asset = (
                db.query(Asset)
                .filter(Asset.asset_id == asset_id, Asset.user_id == user_id)
                .first()
            )
            if not asset:
                raise HTTPException(status_code=400, detail=f"素材不存在或不属于当前账号：{asset_id}")
            url = (asset.source_url or "").strip()
            name = name or (asset.filename or "").strip()
            media_type = _attachment_media_type(asset.media_type or media_type)
            size = int(asset.file_size or size or 0)
        if not url:
            raise HTTPException(status_code=400, detail="素材缺少可读取地址")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="素材地址必须是公网 HTTP(S) 地址")
        identity = asset_id or url
        if identity in seen:
            continue
        seen.add(identity)
        normalized.append(
            {
                "asset_id": asset_id,
                "url": url,
                "name": (name or f"{media_type}-attachment")[:255],
                "media_type": media_type,
                "content_type": (value.content_type or "")[:128],
                "size": size,
            }
        )
    return normalized


def _permission_mode(value: str) -> str:
    return "full" if (value or "").strip().lower() == "full" else "confirm"


def _serialize_session(row: H5ChatSession, message_count: int = 0) -> dict:
    return {
        "id": row.id,
        "title": row.title or "新会话",
        "permission_mode": _permission_mode(row.permission_mode),
        "message_count": int(message_count or 0),
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "last_message_at": row.last_message_at.isoformat() if row.last_message_at else "",
    }


def _serialize_approval(row: H5ChatApproval) -> dict:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "message_id": row.message_id,
        "task": row.task,
        "reason": row.reason or "",
        "execution_target": row.execution_target,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _ensure_chat_session(db: Session, user_id: int, session_id: str = "") -> H5ChatSession:
    wanted = (session_id or "").strip()
    query = db.query(H5ChatSession).filter(
        H5ChatSession.user_id == user_id,
        H5ChatSession.archived_at.is_(None),
    )
    row = query.filter(H5ChatSession.id == wanted).first() if wanted else None
    if wanted and not row:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not row:
        row = query.order_by(H5ChatSession.last_message_at.desc(), H5ChatSession.updated_at.desc()).first()
    if not row:
        now = datetime.utcnow()
        row = H5ChatSession(
            id=uuid.uuid4().hex,
            user_id=user_id,
            title="新会话",
            permission_mode="confirm",
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
    db.query(H5ChatMessage).filter(
        H5ChatMessage.user_id == user_id,
        H5ChatMessage.session_id.is_(None),
        H5ChatMessage.parent_message_id.is_(None),
    ).update({H5ChatMessage.session_id: row.id}, synchronize_session=False)
    return row


@router.get("/api/mastra-chat/sessions", summary="列出 AI 调度会话")
def list_mastra_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    _ensure_chat_session(db, owner.id)
    db.commit()
    rows = (
        db.query(H5ChatSession)
        .filter(H5ChatSession.user_id == owner.id, H5ChatSession.archived_at.is_(None))
        .order_by(H5ChatSession.last_message_at.desc(), H5ChatSession.updated_at.desc())
        .all()
    )
    counts = dict(
        db.query(H5ChatMessage.session_id, func.count(H5ChatMessage.id))
        .filter(H5ChatMessage.user_id == owner.id, H5ChatMessage.parent_message_id.is_(None))
        .group_by(H5ChatMessage.session_id)
        .all()
    )
    return {"ok": True, "sessions": [_serialize_session(row, counts.get(row.id, 0)) for row in rows]}


@router.post("/api/mastra-chat/sessions", summary="创建 AI 调度会话")
def create_mastra_session(
    body: MastraSessionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    now = datetime.utcnow()
    row = H5ChatSession(
        id=uuid.uuid4().hex,
        user_id=owner.id,
        title=(body.title or "新会话").strip()[:160] or "新会话",
        permission_mode=_permission_mode(body.permission_mode),
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "session": _serialize_session(row)}


@router.patch("/api/mastra-chat/sessions/{session_id}", summary="更新 AI 调度会话")
def update_mastra_session(
    session_id: str,
    body: MastraSessionUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    row = _ensure_chat_session(db, owner.id, session_id)
    if body.title is not None:
        row.title = (body.title or "").strip()[:160] or "新会话"
    if body.permission_mode is not None:
        row.permission_mode = _permission_mode(body.permission_mode)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"ok": True, "session": _serialize_session(row)}


@router.delete("/api/mastra-chat/sessions/{session_id}", summary="删除 AI 调度会话")
def delete_mastra_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    row = _ensure_chat_session(db, owner.id, session_id)
    active = (
        db.query(H5ChatMessage.id)
        .filter(
            H5ChatMessage.user_id == owner.id,
            H5ChatMessage.session_id == row.id,
            H5ChatMessage.status.in_(("pending", "processing")),
        )
        .first()
    )
    if active:
        raise HTTPException(status_code=409, detail="当前会话仍有任务执行中，暂时不能删除")
    row.archived_at = datetime.utcnow()
    row.updated_at = row.archived_at
    db.commit()
    return {"ok": True}


@router.get("/api/mastra-chat/status", summary="AI 调度服务状态")
async def mastra_chat_status(current_user: User = Depends(get_current_user)):
    del current_user
    try:
        async with httpx.AsyncClient(timeout=2.5, trust_env=False) as client:
            response = await client.get(f"{_mastra_base_url()}/health")
        ready = response.status_code == 200
    except Exception:
        ready = False
    return {"ok": True, "ready": ready, "mode": "server_orchestrator"}


@router.post("/api/mastra-chat/messages", summary="创建 AI 调度会话消息")
def create_mastra_message(
    body: MastraMessageCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    session = _ensure_chat_session(db, owner.id, body.session_id)
    content = (body.content or "").strip()
    attachments = _normalize_attachments(db, owner.id, body.attachments)
    if not content and not attachments:
        raise HTTPException(status_code=400, detail="消息和素材不能同时为空")
    now = datetime.utcnow()
    row = H5ChatMessage(
        id=uuid.uuid4().hex,
        user_id=owner.id,
        session_id=session.id,
        installation_id=_selected_installation(request, body.installation_id),
        parent_message_id=None,
        mode="mastra",
        content=content,
        attachments=attachments or None,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    if session.title == "新会话":
        session.title = (content or (attachments[0]["name"] if attachments else "新会话"))[:36]
    session.last_message_at = now
    session.updated_at = now
    _add_event(db, row, "queued", {"text": "已进入 AI 调度队列"})
    db.commit()
    db.refresh(row)
    return {"ok": True, "message": _serialize_message(row), "events": []}


@router.post("/api/mastra-chat/approval-request", summary="创建执行确认")
def request_task_approval(
    body: ApprovalRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    parent = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.id == body.parent_message_id,
            H5ChatMessage.user_id == owner.id,
            H5ChatMessage.mode == "mastra",
        )
        .first()
    )
    if not parent:
        raise HTTPException(status_code=404, detail="调度会话不存在")
    session = _ensure_chat_session(db, owner.id, parent.session_id or "")
    if _permission_mode(session.permission_mode) == "full":
        return {"ok": True, "approved": True, "permission_mode": "full"}
    existing = (
        db.query(H5ChatApproval)
        .filter(
            H5ChatApproval.message_id == parent.id,
            H5ChatApproval.status.in_(("pending", "approved", "executing")),
        )
        .order_by(H5ChatApproval.created_at.desc())
        .first()
    )
    if existing:
        return {
            "ok": True,
            # An approval accepted after this planning run started is consumed by
            # the next claimed run. Never let the stale planning run execute it.
            "approved": False,
            "approval": _serialize_approval(existing),
        }
    now = datetime.utcnow()
    target = (body.execution_target or "auto").strip().lower()
    if target not in {"auto", "server", "online"}:
        target = "auto"
    approval = H5ChatApproval(
        id=uuid.uuid4().hex,
        user_id=owner.id,
        session_id=session.id,
        message_id=parent.id,
        task=(body.task or "").strip(),
        reason=(body.reason or "").strip() or None,
        execution_target=target,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db.add(approval)
    parent.status = "processing"
    parent.updated_at = now
    payload = _serialize_approval(approval)
    _add_event(db, parent, "approval_required", payload)
    db.commit()
    return {"ok": True, "approved": False, "approval": payload}


@router.get("/api/mastra-chat/approvals", summary="列出待确认执行")
def list_task_approvals(
    session_id: str = "",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    query = db.query(H5ChatApproval).filter(
        H5ChatApproval.user_id == owner.id,
        H5ChatApproval.status == "pending",
    )
    if session_id:
        query = query.filter(H5ChatApproval.session_id == session_id)
    rows = query.order_by(H5ChatApproval.created_at.asc()).limit(20).all()
    return {"ok": True, "approvals": [_serialize_approval(row) for row in rows]}


@router.post("/api/mastra-chat/approvals/{approval_id}/decision", summary="确认或取消执行")
def decide_task_approval(
    approval_id: str,
    body: ApprovalDecisionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    approval = (
        db.query(H5ChatApproval)
        .filter(H5ChatApproval.id == approval_id, H5ChatApproval.user_id == owner.id)
        .first()
    )
    if not approval:
        raise HTTPException(status_code=404, detail="确认请求不存在")
    if approval.status != "pending":
        return {"ok": True, "approval": _serialize_approval(approval), "deduplicated": True}
    parent = (
        db.query(H5ChatMessage)
        .filter(H5ChatMessage.id == approval.message_id, H5ChatMessage.user_id == owner.id)
        .first()
    )
    if not parent:
        raise HTTPException(status_code=404, detail="原始任务不存在")
    now = datetime.utcnow()
    decision = (body.decision or "").strip().lower()
    approval.decided_at = now
    approval.updated_at = now
    if decision in {"approve", "approved", "confirm"}:
        approval.status = "approved"
        parent.error = None
        parent.finished_at = None
        parent.updated_at = now
        planning_active = parent.claimed_by_installation_id == "mastra-server" and parent.claimed_at is not None
        if planning_active:
            parent.status = "processing"
            _add_event(db, parent, "progress", {"text": "已确认，正在完成执行准备", "approval_id": approval.id})
        else:
            parent.status = "pending"
            parent.claimed_at = None
            parent.claimed_by_installation_id = None
            _add_event(db, parent, "queued", {"text": "已确认执行，正在开始任务", "approval_id": approval.id})
    elif decision in {"reject", "rejected", "cancel"}:
        approval.status = "rejected"
        parent.status = "completed"
        parent.finished_at = now
        parent.updated_at = now
        base = (parent.reply_text or "").strip()
        parent.reply_text = f"{base}\n\n已按你的选择取消执行。".strip()
        _add_event(
            db,
            parent,
            "final",
            {"reply_text": parent.reply_text, "approval_id": approval.id, "cancelled_by_user": True},
        )
    else:
        raise HTTPException(status_code=400, detail="decision 必须是 approve 或 reject")
    db.commit()
    db.refresh(approval)
    return {"ok": True, "approval": _serialize_approval(approval), "message": _serialize_message(parent)}


@router.post("/api/mastra-chat/online-dispatch", summary="AI 调度下发 Online 子任务")
def dispatch_online_task(
    body: OnlineDispatchCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    parent = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.id == body.parent_message_id,
            H5ChatMessage.user_id == owner.id,
            H5ChatMessage.mode == "mastra",
        )
        .first()
    )
    if not parent:
        raise HTTPException(status_code=404, detail="调度会话不存在")
    task = (body.task or "").strip()
    installation_id = _selected_installation(request, body.installation_id) or parent.installation_id

    session = (
        db.query(H5ChatSession)
        .filter(
            H5ChatSession.id == parent.session_id,
            H5ChatSession.user_id == owner.id,
            H5ChatSession.archived_at.is_(None),
        )
        .first()
    )
    fully_authorized = bool(session and _permission_mode(session.permission_mode) == "full")
    executing_approval = None
    if not fully_authorized:
        executing_approval = (
            db.query(H5ChatApproval)
            .filter(
                H5ChatApproval.user_id == owner.id,
                H5ChatApproval.session_id == parent.session_id,
                H5ChatApproval.message_id == parent.id,
                H5ChatApproval.status == "executing",
                H5ChatApproval.execution_target.in_(("auto", "online")),
            )
            .order_by(H5ChatApproval.decided_at.desc(), H5ChatApproval.created_at.desc())
            .first()
        )
        supplied_approval_id = (body.approval_id or "").strip()
        if not executing_approval or supplied_approval_id != executing_approval.id:
            raise HTTPException(status_code=409, detail="该任务尚未获得当前会话的执行授权")

    existing = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.user_id == owner.id,
            H5ChatMessage.parent_message_id == parent.id,
            H5ChatMessage.content == task,
        )
        .order_by(H5ChatMessage.created_at.asc())
        .first()
    )
    if existing:
        return {
            "ok": True,
            "deduplicated": True,
            "online": _online_available(db, owner.id, existing.installation_id),
            "message": _serialize_message(existing),
        }

    now = datetime.utcnow()
    child = H5ChatMessage(
        id=uuid.uuid4().hex,
        user_id=owner.id,
        session_id=parent.session_id,
        installation_id=installation_id,
        parent_message_id=parent.id,
        mode="direct",
        content=task,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db.add(child)
    _add_event(
        db,
        child,
        "queued",
        {"reason": (body.reason or "").strip(), "source": "mastra", "parent_message_id": parent.id},
    )
    parent.status = "processing"
    parent.updated_at = now
    online = _online_available(db, owner.id, installation_id)
    _add_event(
        db,
        parent,
        "progress",
        {
            "text": "任务已下发到 Online" if online else "任务已下发，请启动 Online 客户端",
            "online": online,
            "online_message_id": child.id,
        },
    )
    db.commit()
    db.refresh(child)
    return {"ok": True, "online": online, "message": _serialize_message(child)}


@router.get("/api/mastra-chat/online-tasks/{message_id}", summary="查询 Online 子任务")
def get_online_task(
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    row = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.id == message_id,
            H5ChatMessage.user_id == owner.id,
            H5ChatMessage.parent_message_id.isnot(None),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Online 任务不存在")
    return {"ok": True, "task": _serialize_message(row), "online": _online_available(db, owner.id, row.installation_id)}
