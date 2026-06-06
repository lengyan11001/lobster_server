"""Generation record reporting and admin queries.

The online client saves generated assets locally, then reports only public
links here so admins can audit what users generated without storing media.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .admin import AdminContext, _agent_visible_user_ids, _assert_can_manage_user, _verify_admin_token
from .auth import get_current_user
from ..db import get_db
from ..models import GenerationRecord, User

router = APIRouter()


class GenerationRecordReportBody(BaseModel):
    client_asset_id: Optional[str] = None
    public_url: str
    original_url: Optional[str] = None
    dedupe_hint_url: Optional[str] = None
    media_type: str = "image"
    filename: Optional[str] = None
    file_size: Optional[int] = None
    prompt: Optional[str] = None
    model: Optional[str] = None
    tags: Optional[str] = None
    generation_task_id: Optional[str] = None
    dedupe_key: Optional[str] = None
    source: str = "save-url"
    meta: Optional[dict[str, Any]] = Field(default_factory=dict)


def _clean_text(value: Optional[str], max_len: int = 255) -> Optional[str]:
    s = (value or "").strip()
    if not s:
        return None
    return s[:max_len]


def _clean_long_text(value: Optional[str], max_len: int = 12000) -> Optional[str]:
    s = (value or "").strip()
    if not s:
        return None
    return s[:max_len]


def _clean_media_type(value: Optional[str]) -> str:
    mt = (value or "image").strip().lower()
    return mt if mt in {"image", "video", "audio"} else "image"


def _normalize_url_for_hash(value: Optional[str]) -> str:
    return (value or "").strip().split("#", 1)[0]


def _record_dedupe_key(body: GenerationRecordReportBody) -> str:
    explicit = (body.dedupe_key or "").strip()
    if explicit:
        return explicit[:128]
    basis = "|".join(
        [
            (body.generation_task_id or "").strip(),
            _normalize_url_for_hash(body.dedupe_hint_url),
            _normalize_url_for_hash(body.original_url),
            _normalize_url_for_hash(body.public_url),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _record_payload(row: GenerationRecord, user: Optional[User] = None) -> dict[str, Any]:
    account = ""
    if user is not None:
        account = (user.email or "").replace("@sms.lobster.local", "")
    return {
        "id": row.id,
        "user_id": row.user_id,
        "user_account": account,
        "client_asset_id": row.client_asset_id or "",
        "public_url": row.public_url or "",
        "original_url": row.original_url or "",
        "dedupe_hint_url": row.dedupe_hint_url or "",
        "media_type": row.media_type,
        "filename": row.filename or "",
        "file_size": row.file_size or 0,
        "prompt": row.prompt or "",
        "model": row.model or "",
        "tags": row.tags or "",
        "generation_task_id": row.generation_task_id or "",
        "source": row.source or "",
        "report_count": row.report_count or 1,
        "meta": row.meta or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "last_reported_at": row.last_reported_at.isoformat() if row.last_reported_at else None,
    }


def _apply_report_to_row(row: GenerationRecord, body: GenerationRecordReportBody) -> None:
    row.public_url = _clean_long_text(body.public_url, 4096) or row.public_url
    row.original_url = _clean_long_text(body.original_url, 4096)
    row.dedupe_hint_url = _clean_long_text(body.dedupe_hint_url, 4096)
    row.media_type = _clean_media_type(body.media_type)
    row.filename = _clean_text(body.filename)
    row.file_size = body.file_size if isinstance(body.file_size, int) and body.file_size >= 0 else None
    row.prompt = _clean_long_text(body.prompt)
    row.model = _clean_text(body.model, 128)
    row.tags = _clean_long_text(body.tags, 2048)
    row.generation_task_id = _clean_text(body.generation_task_id, 128)
    row.source = _clean_text(body.source, 64) or "save-url"
    meta = body.meta if isinstance(body.meta, dict) else {}
    row.meta = meta
    row.report_count = int(row.report_count or 0) + 1
    row.updated_at = datetime.utcnow()
    row.last_reported_at = datetime.utcnow()


@router.post("/api/generation-records/report", summary="上报生成素材记录")
def report_generation_record(
    body: GenerationRecordReportBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    public_url = _clean_long_text(body.public_url, 4096)
    if not public_url or not (public_url.startswith("http://") or public_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="public_url 必须是公网 http(s) 链接")
    client_asset_id = _clean_text(body.client_asset_id, 64)
    dedupe_key = _record_dedupe_key(body)

    row: Optional[GenerationRecord] = None
    if client_asset_id:
        row = (
            db.query(GenerationRecord)
            .filter(
                GenerationRecord.user_id == current_user.id,
                GenerationRecord.client_asset_id == client_asset_id,
            )
            .first()
        )
    if row is None and dedupe_key:
        row = (
            db.query(GenerationRecord)
            .filter(
                GenerationRecord.user_id == current_user.id,
                GenerationRecord.dedupe_key == dedupe_key,
            )
            .first()
        )

    created = row is None
    if row is None:
        now = datetime.utcnow()
        row = GenerationRecord(
            user_id=current_user.id,
            client_asset_id=client_asset_id,
            public_url=public_url,
            dedupe_key=dedupe_key,
            report_count=0,
            created_at=now,
            updated_at=now,
            last_reported_at=now,
        )
        db.add(row)
    _apply_report_to_row(row, body)
    row.public_url = public_url
    row.client_asset_id = client_asset_id
    row.dedupe_key = dedupe_key
    db.commit()
    db.refresh(row)
    return {"ok": True, "created": created, "record": _record_payload(row, current_user)}


@router.get("/admin/api/generation-records", summary="管理员/代理商查询生成记录")
def admin_list_generation_records(
    user_id: Optional[int] = None,
    media_type: str = "",
    q: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    query = db.query(GenerationRecord)
    if user_id:
        _assert_can_manage_user(db, ctx, int(user_id), allow_agent_self=True)
        query = query.filter(GenerationRecord.user_id == int(user_id))
    elif ctx.role == "agent":
        visible_ids = _agent_visible_user_ids(db, int(ctx.user_id or 0))
        query = query.filter(GenerationRecord.user_id.in_(visible_ids)) if visible_ids else query.filter(False)

    mt = _clean_media_type(media_type) if media_type else ""
    if mt:
        query = query.filter(GenerationRecord.media_type == mt)
    keyword = (q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            or_(
                GenerationRecord.public_url.ilike(like),
                GenerationRecord.original_url.ilike(like),
                GenerationRecord.prompt.ilike(like),
                GenerationRecord.model.ilike(like),
                GenerationRecord.tags.ilike(like),
                GenerationRecord.generation_task_id.ilike(like),
                GenerationRecord.client_asset_id.ilike(like),
            )
        )

    total = query.with_entities(func.count(GenerationRecord.id)).scalar() or 0
    rows = (
        query.order_by(GenerationRecord.created_at.desc(), GenerationRecord.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    user_ids = sorted({r.user_id for r in rows})
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    return {
        "items": [_record_payload(row, users.get(row.user_id)) for row in rows],
        "pagination": {
            "total": int(total),
            "limit": int(limit),
            "offset": int(offset),
            "has_prev": offset > 0,
            "has_next": offset + limit < int(total),
        },
    }
