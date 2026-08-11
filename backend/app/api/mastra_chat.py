from __future__ import annotations

import os
import io
import json
import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Asset,
    H5ChatApproval,
    H5ChatDevicePresence,
    H5ChatEvent,
    H5ChatMessage,
    H5ChatSession,
    IPContentScheduleTemplate,
    ScheduledTaskRun,
    User,
)
from .auth import get_current_user
from .capabilities import _read_capability_catalog_json
from ..services.device_presence import DEVICE_ONLINE_TTL_SECONDS
from ..services.h5_chat_sessions import (
    backfill_system_task_session,
    is_system_task_session_id,
    SYSTEM_TASK_MESSAGE_MODES,
    system_task_session_id,
)
from ..services.mastra_online_capabilities import (
    OnlineCapabilityParamsError,
    mastra_online_capabilities,
    mastra_online_capability,
    normalize_mastra_online_params,
)
from ..services.mastra_attachment_security import (
    UnsafeMastraImageError,
    assert_safe_mastra_image,
)
from .h5_chat import _add_event, _serialize_message
from .installation_slots import optional_installation_id_from_request
from .mobile_identity import online_user_for_mobile_user
from .skills import user_can_use_capability

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
    queue_mode: str = Field(default="normal", max_length=16)
    target_message_id: str = Field(default="", max_length=64)


class MastraMessageUpdate(BaseModel):
    content: str = Field(default="", max_length=8000)


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
    approval_id: str = Field(default="", max_length=64)


class ApprovalDecisionCreate(BaseModel):
    decision: str = Field(..., max_length=16)


class OnlineDispatchCreate(BaseModel):
    task: str = Field(..., min_length=1, max_length=12000)
    reason: str = Field(default="", max_length=1000)
    parent_message_id: str = Field(..., min_length=8, max_length=64)
    installation_id: Optional[str] = Field(default=None, max_length=128)
    approval_id: str = Field(default="", max_length=64)


class OnlineCapabilityDispatchCreate(BaseModel):
    capability_id: str = Field(..., min_length=3, max_length=128)
    params: Dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=1000)
    parent_message_id: str = Field(..., min_length=8, max_length=64)
    installation_id: Optional[str] = Field(default=None, max_length=128)
    approval_id: str = Field(default="", max_length=64)


class MastraAuthorizedWrite(BaseModel):
    parent_message_id: str = Field(..., min_length=8, max_length=64)
    approval_id: str = Field(default="", max_length=64)


class MastraMemoryTextCreate(MastraAuthorizedWrite):
    title: str = Field(default="个人记忆", max_length=160)
    notes: str = Field(default="由 AI 调度助手保存", max_length=500)
    content: str = Field(..., min_length=1, max_length=120000)


class MastraMemoryAssetCreate(MastraAuthorizedWrite):
    asset_id: str = Field(..., min_length=1, max_length=64)
    title: str = Field(default="", max_length=160)
    notes: str = Field(default="由 AI 调度助手整理素材", max_length=500)


class MastraPersonalProfileUpdate(MastraAuthorizedWrite):
    fields: Dict[str, Any] = Field(default_factory=dict)


class MastraWechatRuleCreate(MastraAuthorizedWrite):
    title: str = Field(default="微信接管规则", max_length=200)
    content: str = Field(..., min_length=1, max_length=4000)
    category: str = Field(default="general", max_length=32)
    account_id: str = Field(default="", max_length=160)
    contact_key: str = Field(default="", max_length=240)
    scope: str = Field(default="account", max_length=24)
    priority: int = Field(default=50, ge=0, le=100)
    risk_level: str = Field(default="medium", max_length=16)


def _mastra_base_url() -> str:
    return (os.environ.get("LOBSTER_MASTRA_URL") or "http://127.0.0.1:4111").strip().rstrip("/")


def _max_active_messages_per_user() -> int:
    try:
        return max(2, min(30, int(os.environ.get("LOBSTER_MASTRA_MAX_ACTIVE_PER_USER") or "8")))
    except (TypeError, ValueError):
        return 8


def _mastra_internal_secret() -> str:
    configured = (os.environ.get("LOBSTER_MASTRA_INTERNAL_SECRET") or "").strip()
    if configured:
        return configured
    app_secret = (os.environ.get("LOBSTER_SECRET_KEY") or os.environ.get("SECRET_KEY") or "").strip()
    if not app_secret:
        from ..core.config import settings

        app_secret = str(settings.secret_key or "").strip()
    return hashlib.sha256(f"{app_secret}:lobster-mastra".encode("utf-8")).hexdigest()


def _selected_installation(request: Request, explicit: Optional[str]) -> Optional[str]:
    return (explicit or optional_installation_id_from_request(request) or "").strip() or None


def _online_available(db: Session, user_id: int, installation_id: Optional[str]) -> bool:
    cutoff = datetime.utcnow() - timedelta(seconds=DEVICE_ONLINE_TTL_SECONDS)
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
    total_size = 0
    for value in values[:8]:
        asset_id = (value.asset_id or "").strip()
        url = (value.url or "").strip()
        name = (value.name or "").strip()
        media_type = _attachment_media_type(value.media_type)
        content_type = (value.content_type or "").strip()
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
            asset_meta = asset.meta if isinstance(asset.meta, dict) else {}
            content_type = content_type or str(
                asset_meta.get("content_type") or asset_meta.get("mime_type") or ""
            ).strip()
        if not url:
            raise HTTPException(status_code=400, detail="素材缺少可读取地址")
        try:
            assert_safe_mastra_image(filename=name, url=url, content_type=content_type)
        except UnsafeMastraImageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="素材地址必须是公网 HTTP(S) 地址")
        identity = asset_id or url
        if identity in seen:
            continue
        seen.add(identity)
        media_limits = {
            "image": 12 * 1024 * 1024,
            "document": 30 * 1024 * 1024,
            "audio": 100 * 1024 * 1024,
            "video": 150 * 1024 * 1024,
            "file": 30 * 1024 * 1024,
        }
        if size > media_limits.get(media_type, media_limits["file"]):
            raise HTTPException(status_code=400, detail=f"素材过大，无法加入对话：{name or asset_id}")
        total_size += max(0, size)
        if total_size > 200 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="本轮素材总大小不能超过 200 MB")
        normalized.append(
            {
                "asset_id": asset_id,
                "url": url,
                "name": (name or f"{media_type}-attachment")[:255],
                "media_type": media_type,
                "content_type": content_type[:128],
                "size": size,
            }
        )
    return normalized


def _permission_mode(value: str) -> str:
    return "full" if (value or "").strip().lower() == "full" else "confirm"


def _authorized_parent_and_approval(
    db: Session,
    *,
    owner_id: int,
    parent_message_id: str,
    approval_id: str,
    targets: tuple[str, ...] = ("auto", "server"),
) -> tuple[H5ChatMessage, Optional[H5ChatApproval]]:
    parent = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.id == parent_message_id,
            H5ChatMessage.user_id == owner_id,
            H5ChatMessage.mode == "mastra",
        )
        .first()
    )
    if parent is None:
        raise HTTPException(status_code=404, detail="调度会话不存在")
    session = (
        db.query(H5ChatSession)
        .filter(
            H5ChatSession.id == parent.session_id,
            H5ChatSession.user_id == owner_id,
            H5ChatSession.archived_at.is_(None),
        )
        .first()
    )
    if session and _permission_mode(session.permission_mode) == "full":
        return parent, None
    approval = (
        db.query(H5ChatApproval)
        .filter(
            H5ChatApproval.user_id == owner_id,
            H5ChatApproval.session_id == parent.session_id,
            H5ChatApproval.message_id == parent.id,
            H5ChatApproval.status == "executing",
            H5ChatApproval.execution_target.in_(targets),
        )
        .order_by(H5ChatApproval.decided_at.desc(), H5ChatApproval.created_at.desc())
        .first()
    )
    if approval is None or (approval_id or "").strip() != approval.id:
        raise HTTPException(status_code=409, detail="该修改尚未获得当前会话的执行授权")
    return parent, approval


_PERSONAL_PROFILE_BASIC_FIELDS = {
    "name",
    "gender",
    "profile_photo_asset_id",
    "profile_photo_url",
    "birth_era",
    "current_province",
    "current_city",
    "hometown",
    "role",
    "share_topic",
    "video_style",
    "after_view_action",
}
_PERSONAL_PROFILE_BUSINESS_FIELDS = {"product", "target_customer", "advantages"}


def _personal_profile_payload(db: Session, user_id: int) -> dict:
    row = (
        db.query(IPContentScheduleTemplate)
        .filter(
            IPContentScheduleTemplate.user_id == user_id,
            IPContentScheduleTemplate.name == "个人默认配置",
            IPContentScheduleTemplate.status == "active",
        )
        .order_by(IPContentScheduleTemplate.updated_at.desc(), IPContentScheduleTemplate.id.desc())
        .first()
    )
    requirements = dict(row.requirements or {}) if row else {}
    basic = requirements.get("basic_profile") if isinstance(requirements.get("basic_profile"), dict) else {}
    business = requirements.get("business_description") if isinstance(requirements.get("business_description"), dict) else {}
    fields = {
        key: requirements.get("profile_name" if key == "name" else key, basic.get(key, ""))
        for key in _PERSONAL_PROFILE_BASIC_FIELDS
    }
    fields.update({key: requirements.get(key, business.get(key, "")) for key in _PERSONAL_PROFILE_BUSINESS_FIELDS})
    return {
        "configured": row is not None,
        "fields": fields,
        "keyword_count": len(row.keyword_ids or []) if row else 0,
        "competitor_count": len(row.competitor_ids or []) if row else 0,
        "memory_document_count": len(row.memory_doc_ids or []) if row else 0,
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else "",
    }


def _serialize_session(row: H5ChatSession, message_count: int = 0) -> dict:
    return {
        "id": row.id,
        "title": row.title or "新会话",
        "permission_mode": _permission_mode(row.permission_mode),
        "message_count": int(message_count or 0),
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "last_message_at": row.last_message_at.isoformat() if row.last_message_at else "",
        "system_managed": is_system_task_session_id(row.id, row.user_id),
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
    backfill_system_task_session(db, user_id)
    wanted = (session_id or "").strip()
    query = db.query(H5ChatSession).filter(
        H5ChatSession.user_id == user_id,
        H5ChatSession.archived_at.is_(None),
    )
    row = query.filter(H5ChatSession.id == wanted).first() if wanted else None
    if wanted and not row:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not row:
        row = (
            query.filter(H5ChatSession.id != system_task_session_id(user_id))
            .order_by(H5ChatSession.last_message_at.desc(), H5ChatSession.updated_at.desc())
            .first()
        )
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
        ~H5ChatMessage.mode.in_(SYSTEM_TASK_MESSAGE_MODES),
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
    if body.title is not None and is_system_task_session_id(row.id, owner.id):
        raise HTTPException(status_code=409, detail="系统任务会话名称固定，不能重命名")
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
    if is_system_task_session_id(row.id, owner.id):
        raise HTTPException(status_code=409, detail="系统任务会话用于归集任务记录，不能删除")
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
async def mastra_chat_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user
    capacity: dict = {}
    try:
        async with httpx.AsyncClient(timeout=2.5, trust_env=False) as client:
            response = await client.get(f"{_mastra_base_url()}/health")
            capacity_response = await client.get(
                f"{_mastra_base_url()}/internal/capacity",
                headers={"X-Lobster-Mastra-Secret": _mastra_internal_secret()},
            )
        ready = response.status_code == 200
        if capacity_response.status_code == 200:
            capacity = capacity_response.json()
    except Exception:
        ready = False
    queue_counts = dict(
        db.query(H5ChatMessage.status, func.count(H5ChatMessage.id))
        .filter(H5ChatMessage.mode == "mastra", H5ChatMessage.status.in_(("pending", "processing")))
        .group_by(H5ChatMessage.status)
        .all()
    )
    oldest_pending = (
        db.query(func.min(H5ChatMessage.created_at))
        .filter(H5ChatMessage.mode == "mastra", H5ChatMessage.status == "pending")
        .scalar()
    )
    pending_age = max(0, int((datetime.utcnow() - oldest_pending).total_seconds())) if oldest_pending else 0
    return {
        "ok": True,
        "ready": ready,
        "mode": "server_orchestrator",
        "capacity": {key: value for key, value in capacity.items() if key != "ok"},
        "queue": {
            "pending": int(queue_counts.get("pending", 0)),
            "processing": int(queue_counts.get("processing", 0)),
            "oldest_pending_seconds": pending_age,
        },
    }


@router.get("/api/mastra-chat/capabilities", summary="AI orchestrator capability catalog")
def list_mastra_capabilities(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List unlocked capabilities without requiring an Online installation.

    Installation slot checks belong to desktop execution. This server-only
    discovery route still enforces user unlocks, and authentication enforces
    the request brand before this function runs.
    """
    owner = online_user_for_mobile_user(db, current_user)
    catalog = _read_capability_catalog_json()
    filtered: dict[str, Any] = {}
    for capability_id, definition in catalog.items():
        if not isinstance(definition, dict) or not definition.get("enabled", True):
            continue
        if not user_can_use_capability(
            db,
            owner.id,
            capability_id,
            require_installation=False,
        ):
            continue
        filtered[capability_id] = definition
    # Online workflows are authenticated and authorized when dispatched. They
    # must still be discoverable while the device is offline so the model can
    # plan accurately instead of claiming that an existing client ability is
    # unavailable.
    for capability_id, definition in mastra_online_capabilities().items():
        if isinstance(definition, dict) and definition.get("enabled", True):
            filtered[capability_id] = definition
    return {"ok": True, "capabilities": filtered, "source": "server_and_online"}


def _root_mastra_message(db: Session, user_id: int, message_id: str) -> H5ChatMessage:
    row = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.id == message_id,
            H5ChatMessage.user_id == user_id,
            H5ChatMessage.mode == "mastra",
            H5ChatMessage.parent_message_id.is_(None),
        )
        .with_for_update()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="调度消息不存在")
    return row


def _message_has_started_side_effects(db: Session, row: H5ChatMessage) -> bool:
    child_exists = (
        db.query(H5ChatMessage.id)
        .filter(H5ChatMessage.user_id == row.user_id, H5ChatMessage.parent_message_id == row.id)
        .first()
        is not None
    )
    if child_exists:
        return True
    approval_started = (
        db.query(H5ChatApproval.id)
        .filter(
            H5ChatApproval.message_id == row.id,
            H5ChatApproval.status.in_(("approved", "executing", "completed")),
        )
        .first()
        is not None
    )
    if approval_started:
        return True
    explicit_side_effect = (
        db.query(H5ChatEvent.id)
        .filter(H5ChatEvent.message_id == row.id, H5ChatEvent.event_type == "side_effect")
        .first()
        is not None
    )
    if explicit_side_effect:
        return True
    read_only_tools = {
        "list_system_capabilities",
        "list_personal_memory_documents",
        "read_personal_memory_document",
        "read_personal_memory",
        "read_personal_profile",
        "read_wechat_intelligence",
        "get_online_task_status",
        "request_task_approval",
    }
    tool_events = (
        db.query(H5ChatEvent.payload)
        .filter(H5ChatEvent.message_id == row.id, H5ChatEvent.event_type == "tool_start")
        .all()
    )
    for event in tool_events:
        payload = event[0] if event and isinstance(event[0], dict) else {}
        tool_id = str(payload.get("tool_id") or "").strip()
        if tool_id and tool_id not in read_only_tools:
            return True
    return False


def _cancel_root_message(
    db: Session,
    row: H5ChatMessage,
    *,
    reason: str,
    event_payload: Optional[dict] = None,
) -> bool:
    if row.status in ("completed", "failed", "cancelled"):
        return False
    now = datetime.utcnow()
    row.status = "cancelled"
    row.reply_text = reason
    row.error = None
    row.finished_at = now
    row.updated_at = now
    row.claimed_by_installation_id = None
    db.query(H5ChatApproval).filter(
        H5ChatApproval.message_id == row.id,
        H5ChatApproval.status.in_(("pending", "approved", "executing")),
    ).update(
        {
            H5ChatApproval.status: "rejected",
            H5ChatApproval.decided_at: now,
            H5ChatApproval.finished_at: now,
            H5ChatApproval.updated_at: now,
        },
        synchronize_session=False,
    )
    pending_children = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.user_id == row.user_id,
            H5ChatMessage.parent_message_id == row.id,
            H5ChatMessage.status == "pending",
        )
        .all()
    )
    for child in pending_children:
        child.status = "cancelled"
        child.reply_text = "父任务已取消"
        child.finished_at = now
        child.updated_at = now
        _add_event(db, child, "cancelled", {"text": "父任务已取消"})
    payload = {"text": reason, "cancelled_by_user": True}
    payload.update(event_payload or {})
    _add_event(db, row, "cancelled", payload)
    return True


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
    queue_mode = (body.queue_mode or "normal").strip().lower()
    if queue_mode not in ("normal", "steer"):
        raise HTTPException(status_code=400, detail="queue_mode 必须是 normal 或 steer")
    target = None
    if queue_mode == "steer":
        target_id = (body.target_message_id or "").strip()
        if not target_id:
            raise HTTPException(status_code=400, detail="引导当前任务时缺少目标消息")
        target = (
            db.query(H5ChatMessage)
            .filter(
                H5ChatMessage.id == target_id,
                H5ChatMessage.user_id == owner.id,
                H5ChatMessage.session_id == session.id,
                H5ChatMessage.mode == "mastra",
                H5ChatMessage.parent_message_id.is_(None),
            )
            .with_for_update()
            .first()
        )
        if target is None or target.status != "processing":
            raise HTTPException(status_code=409, detail="当前任务已结束，请将消息加入队列")
        if _message_has_started_side_effects(db, target):
            raise HTTPException(status_code=409, detail="当前任务已开始调用能力，请将补充要求加入队列")
        _cancel_root_message(
            db,
            target,
            reason="已收到补充要求，正在调整当前任务",
            event_payload={"steered": True},
        )
    active_count = (
        db.query(func.count(H5ChatMessage.id))
        .filter(
            H5ChatMessage.user_id == owner.id,
            H5ChatMessage.parent_message_id.is_(None),
            H5ChatMessage.mode == "mastra",
            H5ChatMessage.status.in_(("pending", "processing")),
        )
        .scalar()
        or 0
    )
    if int(active_count) >= _max_active_messages_per_user():
        raise HTTPException(status_code=429, detail="当前账号已有较多任务等待处理，请等前面的任务完成后再发送")
    now = datetime.utcnow()
    row = H5ChatMessage(
        id=uuid.uuid4().hex,
        user_id=owner.id,
        session_id=session.id,
        installation_id=_selected_installation(request, body.installation_id),
        parent_message_id=None,
        mode="mastra",
        queue_mode=queue_mode,
        queue_priority=100 if queue_mode == "steer" else 0,
        target_message_id=target.id if target is not None else None,
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
    if target is not None:
        _add_event(db, row, "queued", {"text": "补充要求已优先处理", "target_message_id": target.id})
        target_event = (
            db.query(H5ChatEvent)
            .filter(H5ChatEvent.message_id == target.id, H5ChatEvent.event_type == "cancelled")
            .order_by(H5ChatEvent.id.desc())
            .first()
        )
        if target_event:
            target_payload = dict(target_event.payload or {})
            target_payload["steered_to_message_id"] = row.id
            target_event.payload = target_payload
    else:
        _add_event(db, row, "queued", {"text": "已进入 AI 调度队列"})
    db.commit()
    db.refresh(row)
    return {"ok": True, "message": _serialize_message(row), "events": []}


@router.get("/api/mastra-chat/queue", summary="查询当前用户的 AI 调度队列")
def get_mastra_queue(
    session_id: str = "",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    wanted_session = (session_id or "").strip()
    query = db.query(H5ChatMessage).filter(
        H5ChatMessage.user_id == owner.id,
        H5ChatMessage.mode == "mastra",
        H5ChatMessage.parent_message_id.is_(None),
        H5ChatMessage.status.in_(("pending", "processing")),
    )
    rows = query.order_by(
        H5ChatMessage.queue_priority.desc(),
        H5ChatMessage.created_at.asc(),
        H5ChatMessage.id.asc(),
    ).all()
    pending_positions = {
        row.id: index
        for index, row in enumerate((item for item in rows if item.status == "pending"), start=1)
    }
    processing = []
    pending = []
    for row in rows:
        if wanted_session and row.session_id != wanted_session:
            continue
        payload = _serialize_message(row)
        if row.status == "processing":
            payload["can_steer"] = not _message_has_started_side_effects(db, row)
            processing.append(payload)
        else:
            payload["queue_position"] = pending_positions.get(row.id, 0)
            pending.append(payload)
    return {
        "ok": True,
        "processing": processing,
        "pending": pending,
        "active_count": len(processing) + len(pending),
    }


@router.patch("/api/mastra-chat/messages/{message_id}", summary="编辑待执行的 AI 调度消息")
def update_mastra_message(
    message_id: str,
    body: MastraMessageUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    row = _root_mastra_message(db, owner.id, message_id)
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="只有排队中的消息可以编辑")
    content = (body.content or "").strip()
    if not content and not (row.attachments or []):
        raise HTTPException(status_code=400, detail="消息和素材不能同时为空")
    row.content = content
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"ok": True, "message": _serialize_message(row)}


@router.delete("/api/mastra-chat/messages/{message_id}", summary="删除待执行的 AI 调度消息")
def delete_mastra_message(
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    row = _root_mastra_message(db, owner.id, message_id)
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="只有排队中的消息可以删除")
    db.query(H5ChatEvent).filter(H5ChatEvent.message_id == row.id).delete(synchronize_session=False)
    db.query(H5ChatApproval).filter(H5ChatApproval.message_id == row.id).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted_id": message_id}


@router.post("/api/mastra-chat/messages/{message_id}/cancel", summary="停止 AI 调度消息")
def cancel_mastra_message(
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    row = _root_mastra_message(db, owner.id, message_id)
    side_effects_may_continue = _message_has_started_side_effects(db, row)
    running_children = (
        db.query(H5ChatMessage.id)
        .filter(
            H5ChatMessage.user_id == owner.id,
            H5ChatMessage.parent_message_id == row.id,
            H5ChatMessage.status.in_(("processing", "completed")),
        )
        .first()
        is not None
    )
    changed = _cancel_root_message(db, row, reason="已停止当前任务")
    if changed:
        db.commit()
        db.refresh(row)
    return {
        "ok": True,
        "deduplicated": not changed,
        "side_effects_may_continue": running_children or side_effects_may_continue,
        "message": _serialize_message(row),
    }


@router.get("/api/mastra-chat/personal-profile", summary="读取 AI 调度可用的个人 IP 人设")
def get_mastra_personal_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    return {"ok": True, "profile": _personal_profile_payload(db, owner.id)}


@router.patch("/api/mastra-chat/personal-profile", summary="由 AI 调度修改个人 IP 人设")
def update_mastra_personal_profile(
    body: MastraPersonalProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    parent, _ = _authorized_parent_and_approval(
        db,
        owner_id=owner.id,
        parent_message_id=body.parent_message_id,
        approval_id=body.approval_id,
    )
    allowed = _PERSONAL_PROFILE_BASIC_FIELDS | _PERSONAL_PROFILE_BUSINESS_FIELDS
    unknown = sorted(set(body.fields) - allowed)
    if unknown:
        raise HTTPException(status_code=400, detail=f"不支持的人设字段：{', '.join(unknown[:8])}")
    cleaned: dict[str, str] = {}
    for key, value in body.fields.items():
        if isinstance(value, (dict, list)):
            raise HTTPException(status_code=400, detail=f"人设字段必须是文本：{key}")
        cleaned[key] = str(value or "").strip()[:2000]
    if not cleaned:
        raise HTTPException(status_code=400, detail="没有需要修改的人设字段")
    if sum(len(value) for value in cleaned.values()) > 12000:
        raise HTTPException(status_code=400, detail="本次人设内容过长，请分次保存")
    photo_asset_id = cleaned.get("profile_photo_asset_id", "")
    if photo_asset_id and not db.query(Asset.id).filter(Asset.asset_id == photo_asset_id, Asset.user_id == owner.id).first():
        raise HTTPException(status_code=400, detail="人物照片素材不存在或不属于当前账号")

    row = (
        db.query(IPContentScheduleTemplate)
        .filter(IPContentScheduleTemplate.user_id == owner.id, IPContentScheduleTemplate.name == "个人默认配置")
        .order_by(IPContentScheduleTemplate.id.desc())
        .first()
    )
    if row is None:
        row = IPContentScheduleTemplate(user_id=owner.id, name="个人默认配置")
        db.add(row)
    requirements = dict(row.requirements or {})
    basic = dict(requirements.get("basic_profile") or {})
    business = dict(requirements.get("business_description") or {})
    for key, value in cleaned.items():
        if key in _PERSONAL_PROFILE_BASIC_FIELDS:
            basic[key] = value
            requirements["profile_name" if key == "name" else key] = value
        else:
            business[key] = value
            requirements[key] = value
    requirements["basic_profile"] = basic
    requirements["business_description"] = business
    row.requirements = requirements
    row.meta = {**(row.meta or {}), "source": "mastra_personal_profile", "is_personal_default": True}
    row.status = "active"
    row.updated_at = datetime.utcnow()
    _add_event(db, parent, "side_effect", {"kind": "personal_profile_updated", "fields": sorted(cleaned)})
    db.commit()
    return {"ok": True, "profile": _personal_profile_payload(db, owner.id), "updated_fields": sorted(cleaned)}


@router.post("/api/mastra-chat/wechat-intelligence/teach", summary="由 AI 调度教授个微接管长期规则")
def teach_mastra_wechat_rule(
    body: MastraWechatRuleCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from .wechat_intelligence import _serialize_rule, create_strategy_rule

    owner = online_user_for_mobile_user(db, current_user)
    parent, _ = _authorized_parent_and_approval(
        db,
        owner_id=owner.id,
        parent_message_id=body.parent_message_id,
        approval_id=body.approval_id,
    )
    rule = create_strategy_rule(
        db,
        user_id=owner.id,
        account_id=(body.account_id or "").strip(),
        contact_key=(body.contact_key or "").strip(),
        scope=(body.scope or "account").strip().lower(),
        title=body.title,
        content=body.content,
        category=body.category,
        priority=body.priority,
        risk_level=body.risk_level,
        source_type="mastra_user_teaching",
        source_ref=body.parent_message_id,
    )
    _add_event(
        db,
        parent,
        "side_effect",
        {
            "kind": "wechat_strategy_rule_created",
            "rule_id": rule.id,
            "category": rule.category,
            "scope": rule.scope,
        },
    )
    db.commit()
    return {"ok": True, "rule": _serialize_rule(rule), "message": "个微接管规则已生效"}


@router.post("/api/mastra-chat/memory/save-text", summary="由 AI 调度保存个人记忆文本")
def save_mastra_memory_text(
    body: MastraMemoryTextCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from .h5_personal_settings import _create_document, _doc_summary, _short_title
    from .installation_slots import ensure_installation_slot

    owner = online_user_for_mobile_user(db, current_user)
    parent, _ = _authorized_parent_and_approval(
        db,
        owner_id=owner.id,
        parent_message_id=body.parent_message_id,
        approval_id=body.approval_id,
    )
    installation_id = _selected_installation(request, None)
    if not installation_id:
        raise HTTPException(status_code=400, detail="请先选择在线设备")
    ensure_installation_slot(db, owner.id, installation_id)
    title = _short_title(body.title, "个人记忆")
    row = _create_document(
        db,
        target_user=owner,
        uploader_user=current_user,
        installation_id=installation_id,
        title=title,
        filename=f"{title}.txt",
        notes=(body.notes or "由 AI 调度助手保存").strip(),
        content_text=body.content,
        meta={"source": "mastra_chat", "parent_message_id": body.parent_message_id},
    )
    _add_event(db, parent, "side_effect", {"kind": "memory_created", "document_id": row.doc_id})
    db.commit()
    return {"ok": True, "document": _doc_summary(row, include_content=False)}


@router.post("/api/mastra-chat/memory/import-asset", summary="由 AI 调度把已上传素材整理为个人记忆")
async def import_mastra_memory_asset(
    body: MastraMemoryAssetCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from .h5_personal_settings import (
        _collect_sources,
        _create_document,
        _describe_visual_sources,
        _doc_summary,
        _download_media_url,
        _short_title,
    )
    from .installation_slots import ensure_installation_slot

    owner = online_user_for_mobile_user(db, current_user)
    parent, _ = _authorized_parent_and_approval(
        db,
        owner_id=owner.id,
        parent_message_id=body.parent_message_id,
        approval_id=body.approval_id,
    )
    asset = db.query(Asset).filter(Asset.asset_id == body.asset_id, Asset.user_id == owner.id).first()
    if asset is None or not (asset.source_url or "").strip():
        raise HTTPException(status_code=404, detail="素材不存在、无可读取地址或不属于当前账号")
    if int(asset.file_size or 0) > 80 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="素材超过 80 MB，不能直接整理为记忆文件")
    installation_id = _selected_installation(request, None)
    if not installation_id:
        raise HTTPException(status_code=400, detail="请先选择在线设备")
    ensure_installation_slot(db, owner.id, installation_id)
    asset_url = (asset.source_url or "").strip()
    asset_filename = asset.filename or f"{asset.asset_id}.bin"
    asset_id = asset.asset_id
    # Downloading and understanding an asset must not retain approval/asset reads.
    db.commit()
    try:
        data = await _download_media_url(asset_url, max_bytes=80 * 1024 * 1024)
        upload = UploadFile(file=io.BytesIO(data), filename=asset_filename)
        source_text, visual_blocks, source_images = await _collect_sources(
            request,
            installation_id,
            [upload],
            "",
            "",
            db=db,
            stt_user=owner,
        )
        source_text = await _describe_visual_sources(request, installation_id, source_text, visual_blocks)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"素材读取失败：{str(exc)[:300]}") from exc
    title = _short_title(body.title, asset_filename or "素材记忆")
    row = _create_document(
        db,
        target_user=owner,
        uploader_user=current_user,
        installation_id=installation_id,
        title=title,
        filename=f"{title}.txt",
        notes=(body.notes or "由 AI 调度助手整理素材").strip(),
        content_text=source_text,
        meta={
            "source": "mastra_chat_asset",
            "source_asset_id": asset_id,
            "source_images": source_images,
            "parent_message_id": body.parent_message_id,
        },
    )
    _add_event(db, parent, "side_effect", {"kind": "memory_imported", "document_id": row.doc_id})
    db.commit()
    return {"ok": True, "document": _doc_summary(row, include_content=False)}


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
    supplied_approval_id = (body.approval_id or "").strip()
    if supplied_approval_id:
        granted = (
            db.query(H5ChatApproval)
            .filter(
                H5ChatApproval.id == supplied_approval_id,
                H5ChatApproval.user_id == owner.id,
                H5ChatApproval.session_id == session.id,
                H5ChatApproval.message_id == parent.id,
                H5ChatApproval.status == "executing",
            )
            .first()
        )
        if granted:
            return {"ok": True, "approved": True, "approval": _serialize_approval(granted)}
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


def _authorized_online_dispatch(
    *,
    db: Session,
    owner: User,
    request: Request,
    parent_message_id: str,
    installation_id: Optional[str],
    approval_id: str,
) -> tuple[H5ChatMessage, Optional[str]]:
    parent = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.id == parent_message_id,
            H5ChatMessage.user_id == owner.id,
            H5ChatMessage.mode == "mastra",
        )
        .first()
    )
    if not parent:
        raise HTTPException(status_code=404, detail="调度会话不存在")
    selected_installation = _selected_installation(request, installation_id) or parent.installation_id

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
        supplied_approval_id = (approval_id or "").strip()
        if not executing_approval or supplied_approval_id != executing_approval.id:
            raise HTTPException(status_code=409, detail="该任务尚未获得当前会话的执行授权")
    return parent, selected_installation


@router.post("/api/mastra-chat/online-dispatch", summary="AI 调度下发 Online 子任务")
def dispatch_online_task(
    body: OnlineDispatchCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    parent, installation_id = _authorized_online_dispatch(
        db=db,
        owner=owner,
        request=request,
        parent_message_id=body.parent_message_id,
        installation_id=body.installation_id,
        approval_id=body.approval_id,
    )
    task = (body.task or "").strip()

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
    _add_event(
        db,
        parent,
        "side_effect",
        {
            "kind": "online_freeform_dispatched",
            "online_message_id": child.id,
            "task_fingerprint": hashlib.sha256(task.encode("utf-8")).hexdigest()[:12],
        },
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


def _online_scheduled_run_payload(db: Session, message_id: str) -> Optional[Dict[str, Any]]:
    row = (
        db.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.h5_message_id == message_id)
        .order_by(ScheduledTaskRun.created_at.desc())
        .first()
    )
    if row is None:
        return None
    return {
        "id": row.id,
        "task_id": row.task_id,
        "title": row.title,
        "task_kind": row.task_kind,
        "status": row.status,
        "progress": row.progress or {},
        "result_text": row.result_text or "",
        "result_payload": row.result_payload or {},
        "error": row.error or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


@router.post("/api/mastra-chat/online-capability-dispatch", summary="AI 调度结构化下发 Online 能力")
def dispatch_online_capability(
    body: OnlineCapabilityDispatchCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    definition = mastra_online_capability(body.capability_id)
    if not definition:
        raise HTTPException(status_code=400, detail="不支持的 Online 能力")
    parent, installation_id = _authorized_online_dispatch(
        db=db,
        owner=owner,
        request=request,
        parent_message_id=body.parent_message_id,
        installation_id=body.installation_id,
        approval_id=body.approval_id,
    )
    try:
        params = normalize_mastra_online_params(body.capability_id, dict(body.params or {}))
    except OnlineCapabilityParamsError as exc:
        raise HTTPException(status_code=400, detail=f"Online 能力参数无效：{exc}") from exc
    canonical_params = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(canonical_params) > 100_000:
        raise HTTPException(status_code=400, detail="Online 能力参数过大")
    capability_id = str(body.capability_id or "").strip().lower()
    action = str(definition.get("action") or "").strip()
    if not action:
        raise HTTPException(status_code=500, detail="Online 能力缺少执行动作")
    fingerprint = hashlib.sha256(f"{capability_id}\n{canonical_params}".encode("utf-8")).hexdigest()[:12]
    child_content = f"[AI调度能力] {definition.get('name') or capability_id} #{fingerprint}"
    existing = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.user_id == owner.id,
            H5ChatMessage.parent_message_id == parent.id,
            H5ChatMessage.content == child_content,
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
            "run": _online_scheduled_run_payload(db, existing.id),
        }

    # Reuse the same client_workflow queue as H5. Online therefore receives the
    # exact action and parameters that its own workbench already understands.
    from .scheduled_tasks import ScheduledTaskCreate, _create_task_row

    task = _create_task_row(
        db,
        ScheduledTaskCreate(
            title=str(definition.get("name") or capability_id)[:160],
            task_kind="client_workflow",
            content=child_content,
            payload={
                "action": action,
                "params": params,
                "h5_context": {
                    "source": "mastra",
                    "parent_message_id": parent.id,
                    "capability_id": capability_id,
                },
            },
            schedule_type="once",
            installation_ids=[installation_id] if installation_id else [],
        ),
        target_user_id=owner.id,
        created_by_user_id=current_user.id,
        created_by_role="mastra",
    )
    run = (
        db.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.id == task.last_run_id, ScheduledTaskRun.user_id == owner.id)
        .first()
    )
    if run is None or not run.h5_message_id:
        raise HTTPException(status_code=500, detail="Online 能力任务未成功入队")
    child = db.query(H5ChatMessage).filter(H5ChatMessage.id == run.h5_message_id).first()
    if child is None:
        raise HTTPException(status_code=500, detail="Online 能力消息未成功创建")
    child.parent_message_id = parent.id
    child.session_id = parent.session_id
    child.content = child_content
    child.updated_at = datetime.utcnow()
    _add_event(
        db,
        parent,
        "side_effect",
        {
            "kind": "online_capability_dispatched",
            "online_message_id": child.id,
            "run_id": run.id,
            "capability_id": capability_id,
            "action": action,
            "param_keys": sorted(params)[:50],
            "param_fingerprint": fingerprint,
        },
    )
    parent.status = "processing"
    parent.updated_at = datetime.utcnow()
    online = _online_available(db, owner.id, installation_id)
    _add_event(
        db,
        parent,
        "progress",
        {
            "text": "任务已按结构化参数下发到 Online" if online else "任务已入队，请启动 Online 客户端",
            "online": online,
            "online_message_id": child.id,
            "capability_id": capability_id,
            "action": action,
        },
    )
    db.commit()
    db.refresh(child)
    return {
        "ok": True,
        "online": online,
        "capability_id": capability_id,
        "action": action,
        "message": _serialize_message(child),
        "run": _online_scheduled_run_payload(db, child.id),
    }


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
    return {
        "ok": True,
        "task": _serialize_message(row),
        "run": _online_scheduled_run_payload(db, row.id),
        "online": _online_available(db, owner.id, row.installation_id),
    }


def _status_counts(rows: List[Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        status = str(getattr(row, "status", "") or "unknown").strip().lower() or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


@router.get("/api/mastra-chat/diagnostics", summary="查询 AI 调度质量与 Online 执行闭环")
def get_mastra_diagnostics(
    days: int = Query(7, ge=1, le=30),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)
    roots = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.user_id == owner.id,
            H5ChatMessage.mode == "mastra",
            H5ChatMessage.parent_message_id.is_(None),
            H5ChatMessage.created_at >= cutoff,
        )
        .order_by(H5ChatMessage.created_at.desc())
        .limit(5000)
        .all()
    )
    root_ids = [row.id for row in roots]
    children = []
    if root_ids:
        children = (
            db.query(H5ChatMessage)
            .filter(
                H5ChatMessage.user_id == owner.id,
                H5ChatMessage.parent_message_id.in_(root_ids),
            )
            .order_by(H5ChatMessage.created_at.desc())
            .limit(5000)
            .all()
        )
    child_ids = [row.id for row in children]
    runs = []
    if child_ids:
        runs = (
            db.query(ScheduledTaskRun)
            .filter(
                ScheduledTaskRun.user_id == owner.id,
                ScheduledTaskRun.h5_message_id.in_(child_ids),
            )
            .order_by(ScheduledTaskRun.created_at.desc())
            .all()
        )
    run_by_message = {row.h5_message_id: row for row in runs if row.h5_message_id}

    capabilities: Dict[str, Dict[str, Any]] = {}
    freeform: List[H5ChatMessage] = []
    structured_total = 0
    structured_latencies: List[float] = []
    for child in children:
        run = run_by_message.get(child.id)
        context = {}
        if run and isinstance(run.payload, dict) and isinstance(run.payload.get("h5_context"), dict):
            context = run.payload["h5_context"]
        capability_id = str(context.get("capability_id") or "").strip().lower()
        if str(context.get("source") or "").strip().lower() == "mastra" and capability_id:
            structured_total += 1
            status = str((run.status if run else child.status) or "unknown").strip().lower()
            bucket = capabilities.setdefault(
                capability_id,
                {"capability_id": capability_id, "total": 0, "statuses": {}, "latency_seconds": []},
            )
            bucket["total"] += 1
            bucket["statuses"][status] = bucket["statuses"].get(status, 0) + 1
            started = (run.started_at or run.claimed_at or run.created_at) if run else child.created_at
            finished = (run.finished_at if run else child.finished_at)
            if started and finished:
                latency = max(0.0, (finished - started).total_seconds())
                bucket["latency_seconds"].append(latency)
                structured_latencies.append(latency)
        elif child.mode == "direct":
            freeform.append(child)

    capability_rows: List[Dict[str, Any]] = []
    for bucket in capabilities.values():
        latencies = bucket.pop("latency_seconds")
        bucket["average_latency_seconds"] = round(sum(latencies) / len(latencies), 2) if latencies else None
        bucket["failure_rate"] = round(bucket["statuses"].get("failed", 0) / bucket["total"], 4)
        capability_rows.append(bucket)
    capability_rows.sort(key=lambda row: (-row["total"], row["capability_id"]))

    approvals = []
    if root_ids:
        approvals = (
            db.query(H5ChatApproval)
            .filter(H5ChatApproval.user_id == owner.id, H5ChatApproval.message_id.in_(root_ids))
            .all()
        )
    stale_pending = [
        row
        for row in children
        if row.status in ("pending", "processing") and (now - row.updated_at).total_seconds() >= 300
    ]
    failed_runs = [row for row in runs if row.status == "failed"]
    conversation_latencies = [
        max(0.0, (row.finished_at - row.created_at).total_seconds())
        for row in roots
        if row.finished_at and row.created_at
    ]
    dispatch_total = structured_total + len(freeform)
    suggestions: List[str] = []
    if freeform:
        suggestions.append(f"有 {len(freeform)} 次请求未命中结构化能力，应优先补充能力目录或关键词。")
    if failed_runs:
        suggestions.append(f"有 {len(failed_runs)} 个 Online 能力执行失败，应按能力和错误类型逐项处理。")
    if stale_pending:
        suggestions.append(f"有 {len(stale_pending)} 个子任务超过 5 分钟未结束，需要检查设备在线与客户端消费。")
    if not suggestions:
        suggestions.append("当前窗口内未发现结构化命中、失败或排队方面的明显异常。")

    return {
        "ok": True,
        "window": {"days": days, "start": cutoff.isoformat(), "end": now.isoformat()},
        "conversations": {
            "total": len(roots),
            "statuses": _status_counts(roots),
            "average_completion_seconds": round(sum(conversation_latencies) / len(conversation_latencies), 2)
            if conversation_latencies
            else None,
        },
        "dispatch": {
            "total": dispatch_total,
            "structured": structured_total,
            "freeform": len(freeform),
            "structured_rate": round(structured_total / dispatch_total, 4) if dispatch_total else None,
            "average_structured_execution_seconds": round(sum(structured_latencies) / len(structured_latencies), 2)
            if structured_latencies
            else None,
            "stale_over_5_minutes": len(stale_pending),
        },
        "approvals": {"total": len(approvals), "statuses": _status_counts(approvals)},
        "capabilities": capability_rows,
        "recent_failures": [
            {
                "run_id": row.id,
                "capability_id": str(((row.payload or {}).get("h5_context") or {}).get("capability_id") or ""),
                "error": str(row.error or "未知错误")[:500],
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in failed_runs[:20]
        ],
        "unmatched_requests": [
            {
                "message_id": row.id,
                "task_preview": str(row.content or "")[:160],
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in freeform[:20]
        ],
        "suggestions": suggestions,
    }
