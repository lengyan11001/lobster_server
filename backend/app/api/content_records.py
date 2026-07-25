from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import get_current_user
from .mobile_identity import online_user_for_mobile_user
from ..db import get_db
from ..models import IPContentDraftRecord, User, UserContentRecord


router = APIRouter()

_CONTENT_KINDS = {"article", "wechat_article", "ppt"}
_SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_IP_TASK_LABELS = {
    "moments_candidate": "朋友圈内容",
    "douyin_copy": "抖音文案",
    "xiaohongshu_copy": "小红书文案",
    "wechat_article": "公众号文章",
    "article": "IP日更文章",
}


class ContentRecordSyncItem(BaseModel):
    source: str
    source_id: str
    kind: str
    title: str = ""
    summary: str = ""
    content: str = ""
    cover_url: str = ""
    file_url: str = ""
    filename: str = ""
    status: str = "completed"
    source_created_at: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ContentRecordSyncBody(BaseModel):
    records: list[ContentRecordSyncItem] = Field(default_factory=list)


def _clean_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _parse_source_time(value: Optional[str]) -> datetime:
    raw = _clean_text(value, 80)
    if not raw:
        return datetime.utcnow()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        return datetime.utcnow()


def _compact_summary(value: Any, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _validate_sync_item(item: ContentRecordSyncItem) -> tuple[str, str, str]:
    source = _clean_text(item.source, 64).lower()
    source_id = _clean_text(item.source_id, 128)
    kind = _clean_text(item.kind, 32).lower()
    if not _SOURCE_RE.match(source):
        raise HTTPException(status_code=400, detail="内容来源格式无效")
    if not source_id:
        raise HTTPException(status_code=400, detail="内容来源 ID 不能为空")
    if kind not in _CONTENT_KINDS:
        raise HTTPException(status_code=400, detail="内容类型无效")
    return source, source_id, kind


def _apply_sync_item(row: UserContentRecord, item: ContentRecordSyncItem, *, kind: str) -> None:
    row.kind = kind
    row.title = _clean_text(item.title, 500) or None
    row.summary = _clean_text(item.summary, 4000) or None
    row.content = _clean_text(item.content, 300000) or None
    row.cover_url = _clean_text(item.cover_url, 4096) or None
    row.file_url = _clean_text(item.file_url, 4096) or None
    row.filename = _clean_text(item.filename, 255) or None
    row.status = _clean_text(item.status, 32).lower() or "completed"
    row.meta = item.meta if isinstance(item.meta, dict) else {}
    row.source_created_at = _parse_source_time(item.source_created_at)
    row.updated_at = datetime.utcnow()


def _synced_payload(row: UserContentRecord) -> dict[str, Any]:
    content_id = f"content:{row.source}:{row.source_id}"
    return {
        "id": row.id,
        "record_id": content_id,
        "asset_id": content_id,
        "source": row.source,
        "source_id": row.source_id,
        "kind": row.kind,
        "title": row.title or "",
        "summary": row.summary or "",
        "content": row.content or "",
        "cover_url": row.cover_url or "",
        "file_url": row.file_url or "",
        "source_url": row.file_url or row.cover_url or "",
        "filename": row.filename or "",
        "media_type": "document",
        "status": row.status or "completed",
        "tags": f"content-record,{row.kind},{row.source}",
        "prompt": row.summary or "",
        "meta": row.meta or {},
        "created_at": row.source_created_at.isoformat() if row.source_created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "_content_record": True,
    }


def _ip_draft_payload(row: IPContentDraftRecord) -> dict[str, Any]:
    meta = dict(row.meta) if isinstance(row.meta, dict) else {}
    images = meta.get("images") if isinstance(meta.get("images"), list) else []
    cover_url = row.image_url or ""
    if not cover_url:
        for image in images:
            if isinstance(image, dict):
                cover_url = _clean_text(image.get("image_url") or image.get("url"), 4096)
                if cover_url:
                    break
    content = row.content or ""
    title = row.title or _IP_TASK_LABELS.get(row.task, "IP日更内容")
    content_id = f"ip-daily:{row.record_id}"
    meta.update({"task": row.task, "platform": row.platform, "image_asset_id": row.image_asset_id or ""})
    return {
        "id": row.id,
        "record_id": content_id,
        "asset_id": content_id,
        "source": "ip_daily",
        "source_id": row.record_id,
        "kind": "article",
        "title": title,
        "summary": _compact_summary(content),
        "content": content,
        "cover_url": cover_url,
        "file_url": "",
        "source_url": cover_url,
        "filename": "",
        "media_type": "document",
        "status": "completed",
        "tags": f"content-record,article,ip-daily,{row.task}",
        "prompt": row.image_prompt or "",
        "meta": meta,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "_content_record": True,
    }


@router.post("/api/content-records/sync", summary="Sync desktop content records for H5")
def sync_content_records(
    body: ContentRecordSyncBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not body.records:
        return {"ok": True, "created": 0, "updated": 0, "items": []}
    if len(body.records) > 200:
        raise HTTPException(status_code=400, detail="单次最多同步 200 条内容")

    owner = online_user_for_mobile_user(db, current_user)
    def apply_items() -> tuple[int, int, list[UserContentRecord]]:
        created_count = 0
        updated_count = 0
        applied_rows: list[UserContentRecord] = []
        for sync_item in body.records:
            source, source_id, item_kind = _validate_sync_item(sync_item)
            row = (
                db.query(UserContentRecord)
                .filter(
                    UserContentRecord.user_id == owner.id,
                    UserContentRecord.source == source,
                    UserContentRecord.source_id == source_id,
                )
                .first()
            )
            if row is None:
                row = UserContentRecord(
                    user_id=owner.id,
                    source=source,
                    source_id=source_id,
                    kind=item_kind,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                    source_created_at=datetime.utcnow(),
                )
                db.add(row)
                created_count += 1
            else:
                updated_count += 1
            _apply_sync_item(row, sync_item, kind=item_kind)
            applied_rows.append(row)
        return created_count, updated_count, applied_rows

    created, updated, rows = apply_items()
    try:
        db.commit()
    except IntegrityError:
        # Login backfill and generation completion can report the same item together.
        db.rollback()
        created, updated, rows = apply_items()
        db.commit()
    for row in rows:
        db.refresh(row)
    return {"ok": True, "created": created, "updated": updated, "items": [_synced_payload(row) for row in rows]}


@router.get("/api/content-records", summary="List shared Online and IP daily content")
def list_content_records(
    kind: str = Query("article"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    normalized_kind = _clean_text(kind, 32).lower()
    if normalized_kind not in _CONTENT_KINDS:
        raise HTTPException(status_code=400, detail="内容类型无效")
    owner = online_user_for_mobile_user(db, current_user)
    synced_query = db.query(UserContentRecord).filter(
        UserContentRecord.user_id == owner.id,
        UserContentRecord.kind == normalized_kind,
    )
    synced_total = synced_query.with_entities(func.count(UserContentRecord.id)).scalar() or 0
    fetch_size = offset + limit
    synced_rows = (
        synced_query.order_by(UserContentRecord.source_created_at.desc(), UserContentRecord.id.desc())
        .limit(fetch_size)
        .all()
    )
    items = [_synced_payload(row) for row in synced_rows]

    ip_total = 0
    if normalized_kind == "article":
        ip_query = db.query(IPContentDraftRecord).filter(IPContentDraftRecord.user_id == owner.id)
        ip_total = ip_query.with_entities(func.count(IPContentDraftRecord.id)).scalar() or 0
        ip_rows = (
            ip_query.order_by(IPContentDraftRecord.created_at.desc(), IPContentDraftRecord.id.desc())
            .limit(fetch_size)
            .all()
        )
        items.extend(_ip_draft_payload(row) for row in ip_rows)

    items.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    total = int(synced_total) + int(ip_total)
    return {
        "items": items[offset:offset + limit],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_prev": offset > 0,
            "has_next": offset + limit < total,
        },
    }
