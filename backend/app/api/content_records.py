from __future__ import annotations

import re
from datetime import datetime, timezone
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import get_current_user
from .mobile_identity import online_user_for_mobile_user
from ..db import get_db
from ..models import IPContentDraftRecord, ScheduledTaskRun, User, UserContentRecord


router = APIRouter()

_CONTENT_KINDS = {"article", "wechat_article", "ppt"}
_SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*(https?://[^\s)]+)", re.IGNORECASE)
_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*\bsrc\s*=\s*(['\"])(https?://.*?)\1", re.IGNORECASE)
_IP_TASK_LABELS = {
    "industry_hot_oral": "行业热门口播",
    "professional_ip_oral": "专业 IP 口播",
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


class ContentRecordPublishBody(BaseModel):
    """Request a publish draft for a record already visible in the shared content library."""

    source: str
    source_id: str
    platform: str = "wechat_moments"
    platform_name: str = ""
    account_id: str = ""
    account_nickname: str = ""
    installation_id: str = ""
    title: str = ""
    description: str = ""
    content: str = ""
    tags: str = ""
    media_type: str = "image_text"
    visibility: str = "public"
    image_urls: list[str] = Field(default_factory=list)
    image_asset_ids: list[str] = Field(default_factory=list)
    publish_draft: dict[str, Any] = Field(default_factory=dict)


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


def _content_image_urls(*, cover_url: Any = "", content: Any = "", meta: Any = None) -> list[str]:
    urls: list[str] = []

    def add(value: Any) -> None:
        url = _clean_text(value, 4096)
        if url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)

    def add_meta_images(value: Any, *, image_context: bool = False) -> None:
        if isinstance(value, str):
            if image_context:
                add(value)
            return
        if isinstance(value, list):
            for item in value:
                add_meta_images(item, image_context=image_context)
            return
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            normalized_key = str(key or "").strip().lower()
            nested_image_context = image_context or any(
                token in normalized_key for token in ("image", "cover", "thumbnail", "poster", "preview")
            )
            if normalized_key in {"url", "src", "source_url", "public_url"} and image_context:
                add(item)
            else:
                add_meta_images(item, image_context=nested_image_context)

    add(cover_url)
    raw_content = str(content or "")
    for match in _MARKDOWN_IMAGE_RE.finditer(raw_content):
        add(match.group(1))
    for match in _HTML_IMAGE_RE.finditer(raw_content):
        add(match.group(2))
    add_meta_images(meta)
    return urls[:30]


def _content_image_asset_ids(*, direct: Any = "", meta: Any = None) -> list[str]:
    """Read image asset IDs from current and legacy content-record payloads."""

    asset_ids: list[str] = []

    def add(value: Any) -> None:
        asset_id = _clean_text(value, 128)
        if asset_id and asset_id not in asset_ids:
            asset_ids.append(asset_id)

    def walk(
        value: Any,
        *,
        image_context: bool = False,
        asset_context: bool = False,
        depth: int = 0,
    ) -> None:
        if depth > 5 or value is None:
            return
        if isinstance(value, list):
            for entry in value:
                walk(
                    entry,
                    image_context=image_context,
                    asset_context=asset_context,
                    depth=depth + 1,
                )
            return
        if not isinstance(value, dict):
            if asset_context:
                add(value)
            return
        for key, entry in value.items():
            normalized = str(key or "").strip().lower()
            if normalized == "image_asset_id":
                if isinstance(entry, list):
                    walk(entry, asset_context=True, depth=depth + 1)
                else:
                    add(entry)
                continue
            hinted = image_context or any(
                token in normalized for token in ("image", "cover", "thumbnail", "poster", "preview")
            )
            if normalized == "asset_id" and image_context:
                add(entry)
                continue
            next_asset_context = asset_context or normalized == "image_asset_ids"
            if isinstance(entry, (list, dict, str, int)):
                walk(
                    entry,
                    image_context=hinted,
                    asset_context=next_asset_context,
                    depth=depth + 1,
                )

    add(direct)
    walk(meta)
    return asset_ids[:30]


def _ip_image_items(row: IPContentDraftRecord, meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize legacy and image-update payloads into one small image list."""

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(value: Any, *, fallback_index: int = 0) -> None:
        if isinstance(value, str):
            url = _clean_text(value, 4096)
            if not url.startswith(("http://", "https://")):
                return
            value = {"image_url": url}
        if not isinstance(value, dict):
            return
        url = _clean_text(
            value.get("image_url")
            or value.get("url")
            or value.get("source_url")
            or value.get("public_url")
            or value.get("preview_url"),
            4096,
        )
        asset_id = _clean_text(value.get("image_asset_id") or value.get("asset_id"), 128)
        if not url.startswith(("http://", "https://")) and not asset_id:
            return
        existing = next(
            (
                item
                for item in items
                if (url and item.get("image_url") == url)
                or (asset_id and item.get("image_asset_id") == asset_id)
            ),
            None,
        )
        if existing is not None:
            if url and not existing.get("image_url"):
                existing["image_url"] = url
            if asset_id and not existing.get("image_asset_id"):
                existing["image_asset_id"] = asset_id
            return
        key = url or f"asset:{asset_id}"
        if key in seen:
            return
        seen.add(key)
        item: dict[str, Any] = {
            "image_url": url,
            "image_asset_id": asset_id,
            "image_prompt": _clean_text(value.get("image_prompt") or value.get("prompt"), 2000),
            "generated_prompt": _clean_text(value.get("generated_prompt"), 4000),
            "index": int(value.get("index") or fallback_index or len(items) + 1),
        }
        for key_name in ("status", "error", "image_status", "image_error"):
            clean = _clean_text(value.get(key_name), 500)
            if clean:
                item[key_name] = clean
        items.append(item)

    def walk(value: Any, *, image_context: bool = False, depth: int = 0) -> None:
        if depth > 5 or value is None:
            return
        if isinstance(value, list):
            for index, entry in enumerate(value, 1):
                if isinstance(entry, dict) or isinstance(entry, str):
                    add(entry, fallback_index=index)
                    if isinstance(entry, dict):
                        walk(entry, image_context=image_context, depth=depth + 1)
                else:
                    walk(entry, image_context=image_context, depth=depth + 1)
            return
        if isinstance(value, dict):
            if image_context:
                add(value)
            for key, entry in value.items():
                normalized = str(key or "").strip().lower()
                hinted = image_context or any(token in normalized for token in ("image", "cover", "thumbnail", "poster", "preview"))
                if hinted and isinstance(entry, (list, dict, str)):
                    walk(entry, image_context=hinted, depth=depth + 1)

    add({"image_url": row.image_url or "", "image_asset_id": row.image_asset_id or ""}, fallback_index=1)
    image_update = meta.get("image_update") if isinstance(meta.get("image_update"), dict) else {}
    for value in (
        meta.get("images"),
        image_update.get("images"),
        meta.get("image_results"),
        image_update.get("image_results"),
    ):
        walk(value, image_context=True)

    # A few older writers only persisted image_url/image_asset_id inside image_update.
    add(image_update, fallback_index=len(items) + 1)
    if not items:
        for index, url in enumerate(
            _content_image_urls(cover_url=row.image_url, content=row.content, meta=meta),
            1,
        ):
            add({"image_url": url, "index": index}, fallback_index=index)
    return items[:30]


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


def _synced_payload(row: UserContentRecord, *, compact: bool = False) -> dict[str, Any]:
    content_id = f"content:{row.source}:{row.source_id}"
    meta = row.meta if isinstance(row.meta, dict) else {}
    image_urls = _content_image_urls(cover_url=row.cover_url, content=row.content, meta=meta)
    image_asset_ids = _content_image_asset_ids(direct=meta.get("image_asset_id"), meta=meta)
    cover_url = row.cover_url or (image_urls[0] if image_urls else "")
    compact_source = str(row.summary or "").strip() or row.content
    compact_preview = _compact_summary(compact_source)
    return {
        "id": row.id,
        "record_id": content_id,
        "asset_id": content_id,
        "source": row.source,
        "source_id": row.source_id,
        "kind": row.kind,
        "title": row.title or "",
        "summary": compact_preview if compact else (row.summary or ""),
        "content": "" if compact else (row.content or ""),
        "cover_url": cover_url,
        "image_urls": image_urls,
        "image_asset_id": image_asset_ids[0] if image_asset_ids else "",
        "image_asset_ids": image_asset_ids,
        "file_url": row.file_url or "",
        "source_url": row.file_url or cover_url,
        "filename": row.filename or "",
        "media_type": "document",
        "status": row.status or "completed",
        "tags": f"content-record,{row.kind},{row.source}",
        "prompt": compact_preview if compact else (row.summary or ""),
        "meta": {} if compact else meta,
        "created_at": row.source_created_at.isoformat() if row.source_created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "_content_record": True,
        "_compact": compact,
    }


def _ip_draft_payload(row: IPContentDraftRecord, *, compact: bool = False) -> dict[str, Any]:
    meta = dict(row.meta) if isinstance(row.meta, dict) else {}
    image_update = meta.get("image_update") if isinstance(meta.get("image_update"), dict) else {}
    images = _ip_image_items(row, meta)
    content = row.content or ""
    image_urls = [item["image_url"] for item in images if item.get("image_url")]
    image_asset_ids = list(dict.fromkeys(
        item["image_asset_id"] for item in images if item.get("image_asset_id")
    ))
    cover_url = image_urls[0] if image_urls else ""
    title = row.title or _IP_TASK_LABELS.get(row.task, "IP日更内容")
    content_id = f"ip-daily:{row.record_id}"
    image_status = _clean_text(
        meta.get("image_status") or image_update.get("image_status") or image_update.get("status"),
        64,
    )
    image_progress = _clean_text(meta.get("image_progress") or image_update.get("image_progress"), 64)
    image_error = _clean_text(
        meta.get("image_error") or image_update.get("image_error") or image_update.get("error"),
        1000,
    )
    try:
        image_failed_index = int(meta.get("image_failed_index") or image_update.get("image_failed_index") or 0)
    except (TypeError, ValueError):
        image_failed_index = 0
    image_batch_id = _clean_text(
        meta.get("image_batch_id") or image_update.get("image_batch_id"),
        96,
    )
    if not image_status:
        if image_error:
            image_status = "failed"
        elif image_batch_id and image_progress and image_progress not in {"3/3", "completed"}:
            image_status = "processing"
        elif images:
            image_status = "completed"
    if not image_progress and image_batch_id:
        image_progress = f"{len(images)}/3"
    light_meta = {
        "task": row.task,
        "platform": row.platform,
        "image_asset_id": row.image_asset_id or (image_asset_ids[0] if image_asset_ids else ""),
        "image_status": image_status,
        "image_progress": image_progress,
        "image_error": image_error,
        "image_failed_index": image_failed_index,
        "image_batch_id": image_batch_id,
    }
    meta.update(light_meta)
    return {
        "id": row.id,
        "record_id": content_id,
        "asset_id": content_id,
        "source": "ip_daily",
        "source_id": row.record_id,
        "kind": "article",
        "task": row.task,
        "platform": row.platform,
        "title": title,
        "summary": _compact_summary(content),
        "content": _compact_summary(content, limit=1200) if compact else content,
        "body": _compact_summary(content, limit=1200) if compact else content,
        "cover_url": cover_url,
        "image_urls": image_urls,
        "image_asset_ids": image_asset_ids,
        "images": images,
        "image_status": image_status,
        "image_progress": image_progress,
        "image_error": image_error,
        "image_failed_index": image_failed_index,
        "image_batch_id": image_batch_id,
        "file_url": "",
        "source_url": cover_url,
        "filename": "",
        "media_type": "document",
        "status": "completed",
        "tags": f"content-record,article,ip-daily,{row.task}",
        "prompt": row.image_prompt or "",
        "meta": light_meta if compact else meta,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "_content_record": True,
        "_compact": compact,
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
                row_meta = row.meta if isinstance(row.meta, dict) else {}
                if row.status == "deleted" and row_meta.get("deleted_from_h5") is True:
                    applied_rows.append(row)
                    continue
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
    compact: bool = False,
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
        UserContentRecord.status != "deleted",
    )
    synced_total = synced_query.with_entities(func.count(UserContentRecord.id)).scalar() or 0
    fetch_size = offset + limit
    synced_rows = (
        synced_query.order_by(UserContentRecord.source_created_at.desc(), UserContentRecord.id.desc())
        .limit(fetch_size)
        .all()
    )
    items = [_synced_payload(row, compact=compact) for row in synced_rows]

    ip_total = 0
    if normalized_kind == "article":
        ip_query = db.query(IPContentDraftRecord).filter(IPContentDraftRecord.user_id == owner.id)
        ip_total = ip_query.with_entities(func.count(IPContentDraftRecord.id)).scalar() or 0
        ip_rows = (
            ip_query.order_by(IPContentDraftRecord.created_at.desc(), IPContentDraftRecord.id.desc())
            .limit(fetch_size)
            .all()
        )
        items.extend(_ip_draft_payload(row, compact=compact) for row in ip_rows)

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


def _content_record_by_source(
    db: Session,
    owner: User,
    *,
    source: str,
    source_id: str,
) -> tuple[str, UserContentRecord | IPContentDraftRecord]:
    clean_source = _clean_text(source, 64).lower()
    clean_source_id = _clean_text(source_id, 128)
    if not clean_source_id:
        raise HTTPException(status_code=400, detail="内容记录 ID 不能为空")
    if clean_source == "ip_daily":
        row = db.query(IPContentDraftRecord).filter(
            IPContentDraftRecord.user_id == owner.id,
            IPContentDraftRecord.record_id == clean_source_id,
        ).first()
        record_type = "ip_daily"
    else:
        row = db.query(UserContentRecord).filter(
            UserContentRecord.user_id == owner.id,
            UserContentRecord.source == clean_source,
            UserContentRecord.source_id == clean_source_id,
            UserContentRecord.status != "deleted",
        ).first()
        record_type = "synced"
    if row is None:
        raise HTTPException(status_code=404, detail="内容记录不存在")
    return record_type, row


@router.get("/api/content-records/detail", summary="Get one shared content record")
def get_content_record_detail(
    source: str = Query(...),
    source_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    record_type, row = _content_record_by_source(db, owner, source=source, source_id=source_id)
    item = _ip_draft_payload(row) if record_type == "ip_daily" else _synced_payload(row)
    return {"ok": True, "item": item}


def _unique_publish_values(values: Any, *, limit: int, max_length: int) -> list[str]:
    rows = values if isinstance(values, list) else []
    out: list[str] = []
    for value in rows:
        text = _clean_text(value, max_length)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _publish_value_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    if value is None:
        return []
    return [value]


def _pending_content_publish_run(
    db: Session,
    *,
    user_id: int,
    request_key: str,
) -> Optional[ScheduledTaskRun]:
    rows = (
        db.query(ScheduledTaskRun)
        .filter(
            ScheduledTaskRun.user_id == user_id,
            ScheduledTaskRun.task_kind == "content_publish",
            ScheduledTaskRun.status == "completed",
        )
        .order_by(ScheduledTaskRun.created_at.desc())
        .limit(50)
        .all()
    )
    for row in rows:
        result = row.result_payload if isinstance(row.result_payload, dict) else {}
        draft = result.get("publish_draft") if isinstance(result.get("publish_draft"), dict) else {}
        if draft.get("content_publish_key") != request_key:
            continue
        if str(draft.get("status") or "").strip().lower() in {"pending", "processing"}:
            return row
    return None


@router.post("/api/content-records/publish-request", summary="Publish a shared content record")
def request_content_record_publish(
    body: ContentRecordPublishBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    record_type, row = _content_record_by_source(
        db,
        owner,
        source=body.source,
        source_id=body.source_id,
    )
    item = _ip_draft_payload(row) if record_type == "ip_daily" else _synced_payload(row)
    incoming = dict(body.publish_draft) if isinstance(body.publish_draft, dict) else {}

    platform = _clean_text(body.platform or incoming.get("platform"), 32).lower() or "wechat_moments"
    if platform in {"wechat", "moments"}:
        platform = "wechat_moments"
    if platform != "wechat_moments":
        raise HTTPException(status_code=400, detail="This content-record endpoint only supports WeChat Moments")

    image_refs: list[dict[str, str]] = []

    def add_image_ref(raw_url: Any = "", raw_asset_id: Any = "") -> None:
        url = _clean_text(raw_url, 4096)
        asset_id = _clean_text(raw_asset_id, 128)
        if url and not url.startswith(("http://", "https://")):
            url = ""
        if not url and not asset_id:
            return
        existing = next(
            (
                ref
                for ref in image_refs
                if (url and ref.get("image_url") == url) or (asset_id and ref.get("image_asset_id") == asset_id)
            ),
            None,
        )
        if existing is not None:
            if url and not existing.get("image_url"):
                existing["image_url"] = url
            if asset_id and not existing.get("image_asset_id"):
                existing["image_asset_id"] = asset_id
            return
        image_refs.append({"image_url": url, "image_asset_id": asset_id})

    def add_parallel_refs(urls: Any, asset_ids: Any) -> None:
        url_values = _publish_value_list(urls)
        asset_values = _publish_value_list(asset_ids)
        for index in range(max(len(url_values), len(asset_values))):
            add_image_ref(
                url_values[index] if index < len(url_values) else "",
                asset_values[index] if index < len(asset_values) else "",
            )

    add_parallel_refs(body.image_urls, body.image_asset_ids)
    add_parallel_refs(incoming.get("image_urls"), incoming.get("image_asset_ids"))
    for raw in _publish_value_list(incoming.get("attachments")):
        if isinstance(raw, dict):
            add_image_ref(raw.get("source_url") or raw.get("url"), raw.get("asset_id") or raw.get("image_asset_id"))
    for raw in _publish_value_list(incoming.get("images")):
        if isinstance(raw, dict):
            add_image_ref(raw.get("image_url") or raw.get("url") or raw.get("source_url"), raw.get("image_asset_id") or raw.get("asset_id"))
    for raw in _publish_value_list(item.get("images")):
        if isinstance(raw, dict):
            add_image_ref(raw.get("image_url") or raw.get("url") or raw.get("source_url"), raw.get("image_asset_id") or raw.get("asset_id"))
    add_parallel_refs(item.get("image_urls"), item.get("image_asset_ids"))
    image_refs = image_refs[:9]
    image_urls = [ref["image_url"] for ref in image_refs if ref.get("image_url")]
    image_asset_ids = [ref["image_asset_id"] for ref in image_refs if ref.get("image_asset_id")]
    if not image_urls and not image_asset_ids:
        raise HTTPException(status_code=400, detail="朋友圈文案生成图片后才能发布")
    description = _clean_text(
        body.description
        or body.content
        or incoming.get("description")
        or incoming.get("content")
        or item.get("content")
        or item.get("summary"),
        300000,
    )
    title = _clean_text(body.title or incoming.get("title") or item.get("title"), 500)
    if not description and not title and not image_urls and not image_asset_ids:
        raise HTTPException(status_code=400, detail="The content record has no publishable copy or image")

    account_id = _clean_text(body.account_id or incoming.get("account_id"), 160)
    account_nickname = _clean_text(body.account_nickname or incoming.get("account_nickname"), 160)
    if not account_id and not account_nickname:
        raise HTTPException(status_code=400, detail="A publish account is required")
    installation_id = _clean_text(body.installation_id or incoming.get("installation_id"), 128)
    request_key = ":".join(
        [
            _clean_text(body.source, 64).lower(),
            _clean_text(body.source_id, 128),
            installation_id,
            account_id or account_nickname,
        ]
    )
    existing = _pending_content_publish_run(db, user_id=owner.id, request_key=request_key)
    if existing is not None:
        existing_result = existing.result_payload if isinstance(existing.result_payload, dict) else {}
        return {
            "ok": True,
            "status": "pending",
            "reused": True,
            "run_id": existing.id,
            "publish_draft": existing_result.get("publish_draft") or {},
        }

    attachments: list[dict[str, Any]] = []
    for index, ref in enumerate(image_refs):
        url = ref.get("image_url") or ""
        asset_id = ref.get("image_asset_id") or ""
        attachments.append(
            {
                "asset_id": asset_id,
                "source_url": url,
                "url": url,
                "media_type": "image",
                "kind": "image",
                "filename": f"moments-{index + 1}.jpg",
            }
        )

    now = datetime.utcnow()
    draft = {
        **incoming,
        "content_publish_key": request_key,
        "content_record_source": _clean_text(body.source, 64).lower(),
        "content_record_source_id": _clean_text(body.source_id, 128),
        "platform": "wechat_moments",
        "platform_name": _clean_text(body.platform_name or incoming.get("platform_name"), 160) or "WeChat Moments",
        "account_id": account_id,
        "account_nickname": account_nickname,
        "installation_id": installation_id,
        "asset_id": image_asset_ids[0] if image_asset_ids else "",
        "source_url": image_urls[0] if image_urls else "",
        "url": image_urls[0] if image_urls else "",
        "image_urls": image_urls,
        "image_asset_ids": image_asset_ids,
        "attachments": attachments,
        "title": title,
        "description": description,
        "content": description,
        "tags": _clean_text(body.tags or incoming.get("tags"), 4000),
        "media_type": "image_text",
        "visibility": _clean_text(body.visibility or incoming.get("visibility"), 32) or "public",
        "status": "pending",
        "requested_at": now.isoformat(),
    }
    run_id = uuid.uuid4().hex
    run = ScheduledTaskRun(
        id=run_id,
        task_id=None,
        user_id=owner.id,
        created_by_user_id=owner.id,
        created_by_role="user",
        installation_id=installation_id or None,
        title=(f"Moments publish: {title}" if title else "Moments publish")[:160],
        task_kind="content_publish",
        content=description,
        payload={
            "action": "publish_content",
            "source": "content_record",
            "content_record_source": draft["content_record_source"],
            "content_record_source_id": draft["content_record_source_id"],
        },
        status="completed",
        progress={"status": "completed"},
        result_text="Content record is ready to publish",
        result_payload={
            "content_record": {
                "source": draft["content_record_source"],
                "source_id": draft["content_record_source_id"],
                "title": title,
            },
            "publish_draft": draft,
        },
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=now,
    )
    db.add(run)
    db.commit()
    try:
        from .scheduled_tasks import _clear_pending_empty_for_target

        _clear_pending_empty_for_target("publish", owner.id, installation_id or None)
    except Exception:
        pass
    return {
        "ok": True,
        "status": "pending",
        "reused": False,
        "run_id": run_id,
        "publish_draft": draft,
    }


@router.delete("/api/content-records", summary="Delete one shared content record")
def delete_content_record(
    source: str = Query(...),
    source_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    record_type, row = _content_record_by_source(db, owner, source=source, source_id=source_id)
    if record_type == "ip_daily":
        db.delete(row)
    else:
        meta = dict(row.meta or {})
        meta["deleted_from_h5"] = True
        meta["deleted_at"] = datetime.utcnow().isoformat()
        row.meta = meta
        row.status = "deleted"
        row.updated_at = datetime.utcnow()
        db.add(row)
    db.commit()
    return {"ok": True, "source": source, "source_id": source_id}
