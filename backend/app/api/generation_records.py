"""Generation record reporting and admin queries.

The online client saves generated assets locally, then reports only public
links here so admins can audit what users generated without storing media.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .admin import AdminContext, _agent_visible_user_ids, _assert_can_manage_user, _verify_admin_token
from .assets import RegisterAssetUrlReq, _registered_asset_payload, upsert_registered_assets
from .auth import get_current_user
from ..db import get_db
from ..models import Asset, DataMigrationMarker, GenerationRecord, User

logger = logging.getLogger(__name__)
router = APIRouter()

_GENERATION_ASSET_BACKFILL = "generation_records_to_assets_v1"
_GENERATED_ASSET_ORIGIN_REPAIR = "generated_asset_origin_repair_v1"


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


def _asset_registration_from_generation(row: GenerationRecord) -> RegisterAssetUrlReq:
    return RegisterAssetUrlReq(
        url=row.public_url or "",
        media_type=row.media_type or "image",
        filename=row.filename or "",
        file_size=int(row.file_size or 0),
        source_asset_id=row.client_asset_id or "",
        asset_origin="generated",
        prompt=row.prompt or "",
        creative_prompt=row.prompt or "",
        model=row.model or "",
        tags=row.tags or "",
        generation_task_id=row.generation_task_id or "",
        generation_record_id=row.id,
        source_created_at=row.created_at.isoformat() if row.created_at else None,
    )


def backfill_generation_records_to_assets(bind, *, batch_size: int = 500) -> dict[str, int | bool]:
    """Materialize historical Online generation reports once for H5 listing."""
    from sqlalchemy.orm import sessionmaker

    session_factory = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=bind)
    db = session_factory()
    created = 0
    updated = 0
    scanned = 0
    try:
        marker = db.query(DataMigrationMarker).filter(DataMigrationMarker.name == _GENERATION_ASSET_BACKFILL).first()
        if marker is not None:
            return {"applied": False, "scanned": 0, "created": 0, "updated": 0}
        cursor = 0
        size = max(50, min(int(batch_size or 500), 1000))
        while True:
            records = (
                db.query(GenerationRecord)
                .filter(GenerationRecord.id > cursor)
                .order_by(GenerationRecord.id.asc())
                .limit(size)
                .all()
            )
            if not records:
                break
            cursor = int(records[-1].id)
            scanned += len(records)
            grouped: dict[int, list[RegisterAssetUrlReq]] = {}
            for row in records:
                if not str(row.public_url or "").strip().startswith(("http://", "https://")):
                    continue
                grouped.setdefault(int(row.user_id), []).append(_asset_registration_from_generation(row))
            for user_id, bodies in grouped.items():
                _, batch_created, batch_updated = upsert_registered_assets(
                    db,
                    user_id,
                    bodies,
                    registered_from="generation_record_backfill",
                )
                created += batch_created
                updated += batch_updated
            db.commit()
        db.add(DataMigrationMarker(
            name=_GENERATION_ASSET_BACKFILL,
            meta={"scanned": scanned, "created": created, "updated": updated},
            applied_at=datetime.utcnow(),
        ))
        db.commit()
        logger.info(
            "generation asset backfill completed scanned=%s created=%s updated=%s",
            scanned,
            created,
            updated,
        )
        return {"applied": True, "scanned": scanned, "created": created, "updated": updated}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def repair_generated_asset_origins(bind, *, batch_size: int = 500) -> dict[str, int | bool]:
    """Repair rows whose generation metadata was later overwritten as a user upload."""
    from sqlalchemy.orm import sessionmaker

    session_factory = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=bind)
    db = session_factory()
    scanned = 0
    updated = 0
    try:
        marker = db.query(DataMigrationMarker).filter(
            DataMigrationMarker.name == _GENERATED_ASSET_ORIGIN_REPAIR
        ).first()
        if marker is not None:
            return {"applied": False, "scanned": 0, "updated": 0}

        cursor = 0
        size = max(50, min(int(batch_size or 500), 1000))
        while True:
            rows = (
                db.query(Asset)
                .filter(Asset.id > cursor)
                .order_by(Asset.id.asc())
                .limit(size)
                .all()
            )
            if not rows:
                break
            cursor = int(rows[-1].id)
            scanned += len(rows)
            for row in rows:
                meta = row.meta if isinstance(row.meta, dict) else {}
                origin = str(meta.get("asset_origin") or meta.get("origin") or "").strip().lower()
                generated_signal = (
                    meta.get("generation_record_id") is not None
                    or str(meta.get("registered_from") or "").strip()
                    in {"online_generation_report", "generation_record_backfill"}
                )
                if origin != "user_upload" or not generated_signal:
                    continue
                repaired_meta = dict(meta)
                repaired_meta["asset_origin"] = "generated"
                repaired_meta["asset_origin_repaired_from"] = "user_upload"
                row.meta = repaired_meta
                db.add(row)
                updated += 1

        db.add(DataMigrationMarker(
            name=_GENERATED_ASSET_ORIGIN_REPAIR,
            meta={"scanned": scanned, "updated": updated},
            applied_at=datetime.utcnow(),
        ))
        db.commit()
        logger.info("generated asset origin repair completed scanned=%s updated=%s", scanned, updated)
        return {"applied": True, "scanned": scanned, "updated": updated}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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
    db.flush()
    assets, _, _ = upsert_registered_assets(
        db,
        current_user.id,
        [_asset_registration_from_generation(row)],
        registered_from="online_generation_report",
    )
    db.commit()
    db.refresh(row)
    asset = assets[0] if assets else None
    if asset is not None:
        db.refresh(asset)
    return {
        "ok": True,
        "created": created,
        "record": _record_payload(row, current_user),
        "asset": _registered_asset_payload(asset) if asset is not None else None,
    }


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
