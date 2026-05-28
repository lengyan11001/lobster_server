from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Asset, CreativeGenerationJob, User
from .auth import get_current_user

router = APIRouter()

_ALLOWED_STATUS = {"queued", "running", "completed", "failed", "stale", "canceled"}
_TERMINAL_STATUS = {"completed", "failed", "stale", "canceled"}


class CreativeJobUpsertBody(BaseModel):
    job_id: str = Field(..., min_length=3, max_length=64)
    feature_type: str = Field(..., min_length=1, max_length=64)
    provider: Optional[str] = Field(None, max_length=64)
    provider_task_id: Optional[str] = Field(None, max_length=128)
    status: str = Field("running", max_length=32)
    stage: Optional[str] = Field(None, max_length=64)
    progress: Optional[int] = Field(None, ge=0, le=100)
    title: Optional[str] = Field(None, max_length=255)
    prompt: Optional[str] = None
    request_payload: Optional[dict[str, Any]] = None
    result_payload: Optional[dict[str, Any]] = None
    asset_ids: Optional[list[str]] = None
    saved_assets: Optional[list[Any]] = None
    error: Optional[str] = None
    meta: Optional[dict[str, Any]] = None


class CreativeJobPatchBody(BaseModel):
    provider_task_id: Optional[str] = Field(None, max_length=128)
    status: Optional[str] = Field(None, max_length=32)
    stage: Optional[str] = Field(None, max_length=64)
    progress: Optional[int] = Field(None, ge=0, le=100)
    title: Optional[str] = Field(None, max_length=255)
    prompt: Optional[str] = None
    request_payload: Optional[dict[str, Any]] = None
    result_payload: Optional[dict[str, Any]] = None
    asset_ids: Optional[list[str]] = None
    saved_assets: Optional[list[Any]] = None
    error: Optional[str] = None
    meta: Optional[dict[str, Any]] = None


def _clean_status(value: Optional[str], default: str = "running") -> str:
    status = (value or default).strip().lower() or default
    if status not in _ALLOWED_STATUS:
        raise HTTPException(status_code=400, detail=f"invalid status: {status}")
    return status


def _clean_feature(value: str) -> str:
    feature = (value or "").strip().lower()
    if not feature:
        raise HTTPException(status_code=400, detail="feature_type required")
    return feature[:64]


def _asset_ids_from_saved(saved_assets: Any) -> list[str]:
    result: list[str] = []
    rows = saved_assets if isinstance(saved_assets, list) else []
    for item in rows:
        aid = ""
        if isinstance(item, dict):
            aid = str(item.get("asset_id") or "").strip()
            if not aid and isinstance(item.get("asset"), dict):
                aid = str(item["asset"].get("asset_id") or "").strip()
            if not aid and isinstance(item.get("cloud_asset"), dict):
                aid = str(item["cloud_asset"].get("asset_id") or "").strip()
        if aid and aid not in result:
            result.append(aid)
    return result


def _normalize_asset_ids(asset_ids: Any, saved_assets: Any = None) -> list[str]:
    result: list[str] = []
    if isinstance(asset_ids, list):
        for item in asset_ids:
            aid = str(item or "").strip()
            if aid and aid not in result:
                result.append(aid)
    for aid in _asset_ids_from_saved(saved_assets):
        if aid not in result:
            result.append(aid)
    return result


def _asset_map(db: Session, user_id: int, asset_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not asset_ids:
        return {}
    rows = (
        db.query(Asset)
        .filter(Asset.user_id == user_id, Asset.asset_id.in_(asset_ids[:100]))
        .all()
    )
    return {
        row.asset_id: {
            "asset_id": row.asset_id,
            "media_type": row.media_type,
            "source_url": row.source_url or "",
            "prompt": row.prompt or "",
            "model": row.model or "",
            "tags": row.tags or "",
            "created_at": row.created_at.isoformat() if row.created_at else "",
        }
        for row in rows
    }


def _job_dict(row: CreativeGenerationJob, db: Session, include_assets: bool = True) -> dict[str, Any]:
    asset_ids = row.asset_ids if isinstance(row.asset_ids, list) else []
    data = {
        "id": row.id,
        "job_id": row.job_id,
        "feature_type": row.feature_type,
        "provider": row.provider or "",
        "provider_task_id": row.provider_task_id or "",
        "status": row.status,
        "stage": row.stage or "",
        "progress": row.progress,
        "title": row.title or "",
        "prompt": row.prompt or "",
        "request_payload": row.request_payload or {},
        "result_payload": row.result_payload or {},
        "asset_ids": asset_ids,
        "saved_assets": row.saved_assets or [],
        "error": row.error or "",
        "meta": row.meta or {},
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "completed_at": row.completed_at.isoformat() if row.completed_at else "",
    }
    if include_assets:
        data["assets"] = _asset_map(db, row.user_id, [str(x) for x in asset_ids])
    return data


def _apply_payload(row: CreativeGenerationJob, payload: CreativeJobPatchBody | CreativeJobUpsertBody) -> None:
    if getattr(payload, "provider_task_id", None) is not None:
        row.provider_task_id = (payload.provider_task_id or "").strip() or None
    if getattr(payload, "status", None) is not None:
        row.status = _clean_status(payload.status)
    if getattr(payload, "stage", None) is not None:
        row.stage = (payload.stage or "").strip()[:64] or None
    if getattr(payload, "progress", None) is not None:
        row.progress = payload.progress
    if getattr(payload, "title", None) is not None:
        row.title = (payload.title or "").strip()[:255] or None
    if getattr(payload, "prompt", None) is not None:
        row.prompt = payload.prompt or None
    if getattr(payload, "request_payload", None) is not None:
        row.request_payload = payload.request_payload or {}
    if getattr(payload, "result_payload", None) is not None:
        row.result_payload = payload.result_payload or {}
    if getattr(payload, "saved_assets", None) is not None:
        row.saved_assets = payload.saved_assets or []
    if getattr(payload, "asset_ids", None) is not None or getattr(payload, "saved_assets", None) is not None:
        row.asset_ids = _normalize_asset_ids(payload.asset_ids, payload.saved_assets)
    if getattr(payload, "error", None) is not None:
        row.error = (payload.error or "")[:4000] or None
    if getattr(payload, "meta", None) is not None:
        base = dict(row.meta or {})
        base.update(payload.meta or {})
        row.meta = base
    if row.status in _TERMINAL_STATUS and row.completed_at is None:
        row.completed_at = datetime.utcnow()


@router.post("/api/creative-jobs")
async def upsert_creative_job(
    body: CreativeJobUpsertBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job_id = (body.job_id or "").strip().lower()
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id required")
    row = (
        db.query(CreativeGenerationJob)
        .filter(CreativeGenerationJob.job_id == job_id, CreativeGenerationJob.user_id == current_user.id)
        .first()
    )
    if row is None:
        row = CreativeGenerationJob(
            job_id=job_id,
            user_id=current_user.id,
            feature_type=_clean_feature(body.feature_type),
            provider=(body.provider or "").strip()[:64] or None,
            status=_clean_status(body.status),
        )
        db.add(row)
    else:
        row.feature_type = _clean_feature(body.feature_type or row.feature_type)
        if body.provider is not None:
            row.provider = (body.provider or "").strip()[:64] or None
    _apply_payload(row, body)
    db.commit()
    db.refresh(row)
    return {"ok": True, "job": _job_dict(row, db)}


@router.get("/api/creative-jobs")
async def list_creative_jobs(
    feature_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(CreativeGenerationJob).filter(
        CreativeGenerationJob.user_id == current_user.id,
        CreativeGenerationJob.deleted_at.is_(None),
    )
    if feature_type:
        query = query.filter(CreativeGenerationJob.feature_type == _clean_feature(feature_type))
    if status:
        query = query.filter(CreativeGenerationJob.status == _clean_status(status))
    total = query.count()
    rows = (
        query.order_by(CreativeGenerationJob.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {"ok": True, "total": total, "items": [_job_dict(row, db) for row in rows]}


@router.get("/api/creative-jobs/{job_id}")
async def get_creative_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(CreativeGenerationJob)
        .filter(
            CreativeGenerationJob.job_id == job_id.strip().lower(),
            CreativeGenerationJob.user_id == current_user.id,
            CreativeGenerationJob.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"ok": True, "job": _job_dict(row, db)}


@router.patch("/api/creative-jobs/{job_id}")
async def patch_creative_job(
    job_id: str,
    body: CreativeJobPatchBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(CreativeGenerationJob)
        .filter(CreativeGenerationJob.job_id == job_id.strip().lower(), CreativeGenerationJob.user_id == current_user.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    _apply_payload(row, body)
    db.commit()
    db.refresh(row)
    return {"ok": True, "job": _job_dict(row, db)}


@router.delete("/api/creative-jobs/{job_id}")
async def delete_creative_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(CreativeGenerationJob)
        .filter(CreativeGenerationJob.job_id == job_id.strip().lower(), CreativeGenerationJob.user_id == current_user.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    row.deleted_at = datetime.utcnow()
    db.commit()
    return {"ok": True}
