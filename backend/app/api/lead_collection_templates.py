from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CreativeGenerationJob, LeadCollectionTemplate, User
from .auth import get_current_user
from .linkedin_mining import (
    LinkedInMiningStartBody,
    create_linkedin_mining_job_from_payload,
    linkedin_mining_job_payload,
    run_linkedin_mining_job_to_completion,
)
from .social_leads import (
    SocialLeadsStartBody,
    create_social_leads_job_from_payload,
    run_social_leads_job_to_completion,
    social_leads_job_payload,
)

router = APIRouter()

_PLATFORMS = {"reddit", "x", "tiktok", "linkedin"}
_SOCIAL_PLATFORMS = {"reddit", "x", "tiktok"}


class LeadCollectionTemplateIn(BaseModel):
    platform: str = Field("", max_length=32)
    name: str = Field("", max_length=160)
    title: str = Field("", max_length=160)
    request_payload: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class LeadCollectionTemplatePatch(BaseModel):
    name: Optional[str] = Field(None, max_length=160)
    title: Optional[str] = Field(None, max_length=160)
    request_payload: Optional[dict[str, Any]] = None
    meta: Optional[dict[str, Any]] = None


class LeadCollectionTemplateRunIn(BaseModel):
    template_ids: list[int] = Field(default_factory=list)
    title: str = Field("", max_length=160)


def _platform(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"twitter", "twitter_x", "x_leads"}:
        raw = "x"
    if raw in {"tiktok_leads", "tik_tok", "tt"}:
        raw = "tiktok"
    if raw in {"linkedin_mining", "linkedin_leads", "linkedin"}:
        raw = "linkedin"
    if raw not in _PLATFORMS:
        raise HTTPException(status_code=400, detail="不支持的平台")
    return raw


def _clean_name(value: Any, fallback: str) -> str:
    name = str(value or "").strip()
    if not name:
        name = fallback
    return name[:160]


def _template_payload(row: LeadCollectionTemplate) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "platform": row.platform,
        "name": row.name,
        "title": row.title or "",
        "request_payload": row.request_payload or {},
        "status": row.status,
        "meta": row.meta or {},
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _normalize_social_payload(platform: str, payload: dict[str, Any], title: str = "") -> dict[str, Any]:
    data = dict(payload or {})
    data["platform"] = platform
    if title:
        data["title"] = title
    body = SocialLeadsStartBody(**data)
    if not body.keywords:
        raise HTTPException(status_code=400, detail="关键词不能为空，请填写要筛选的精准用户方向")
    if platform == "reddit":
        if bool(body.accounts) == bool(body.communities):
            raise HTTPException(status_code=400, detail="Reddit 账号和社区必须二选一填写")
    elif platform == "tiktok":
        if bool(body.accounts) == bool(body.source_keywords or body.communities):
            raise HTTPException(status_code=400, detail="TikTok 账号和来源词必须二选一填写")
    elif platform == "x":
        if bool(body.accounts) == bool(body.source_keywords or body.communities):
            raise HTTPException(status_code=400, detail="X 账号和搜索词必须二选一填写")
    return {
        "platform": platform,
        "title": body.title,
        "keywords": body.keywords,
        "source_keywords": body.source_keywords or body.communities if platform in {"x", "tiktok"} else [],
        "accounts": body.accounts,
        "post_ids": body.post_ids,
        "communities": body.communities if platform == "reddit" else [],
        "country": body.country,
        "search_type": body.search_type,
        "sort": body.sort or "NEW",
        "time_range": body.time_range or "day",
        "max_items": body.max_items,
        "include_comments": True,
        "include_account_posts": True,
        "auto_run": True,
    }


def _normalize_linkedin_payload(payload: dict[str, Any], title: str = "") -> dict[str, Any]:
    data = dict(payload or {})
    if title:
        data["title"] = title
    body = LinkedInMiningStartBody(**data)
    if not (body.seed_profile_urls or body.seed_company_urls or body.keywords or body.hashtags):
        raise HTTPException(status_code=400, detail="请至少输入一个LinkedIn个人主页、公司主页、关键词或话题")
    return {
        "title": body.title,
        "seed_profile_urls": body.seed_profile_urls,
        "seed_company_urls": body.seed_company_urls,
        "keywords": body.keywords,
        "hashtags": body.hashtags,
        "target_profile": body.target_profile,
        "memory_docs": body.memory_docs,
        "max_people": body.max_people,
        "max_company_employees": body.max_company_employees,
        "max_interactions_per_post": body.max_interactions_per_post,
        "auto_run": True,
    }


def _normalize_template_request(platform: str, payload: dict[str, Any], title: str = "") -> dict[str, Any]:
    if platform in _SOCIAL_PLATFORMS:
        return _normalize_social_payload(platform, payload, title)
    return _normalize_linkedin_payload(payload, title)


def _candidate_count(job_payload: dict[str, Any]) -> int:
    result = job_payload.get("result_payload") if isinstance(job_payload, dict) else {}
    if not isinstance(result, dict):
        return 0
    candidates = result.get("candidates")
    if isinstance(candidates, list):
        return len(candidates)
    lead_summary = result.get("lead_summary")
    if isinstance(lead_summary, dict) and isinstance(lead_summary.get("candidates"), list):
        return len(lead_summary.get("candidates") or [])
    return int(result.get("candidate_count") or 0)


def _job_summary(row: CreativeGenerationJob, payload: dict[str, Any], template: LeadCollectionTemplate) -> dict[str, Any]:
    return {
        "template_id": template.id,
        "template_name": template.name,
        "platform": template.platform,
        "job_id": row.job_id,
        "title": row.title or template.title or template.name,
        "status": row.status,
        "progress": row.progress or 0,
        "candidate_count": _candidate_count(payload),
        "error": row.error or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "completed_at": row.completed_at.isoformat() if row.completed_at else "",
    }


def _templates_for_user(db: Session, user_id: int, template_ids: list[int]) -> list[LeadCollectionTemplate]:
    ids = []
    seen = set()
    for raw in template_ids:
        try:
            tid = int(raw)
        except Exception:
            continue
        if tid > 0 and tid not in seen:
            seen.add(tid)
            ids.append(tid)
    if not ids:
        raise HTTPException(status_code=400, detail="请选择至少一个采集模板")
    rows = (
        db.query(LeadCollectionTemplate)
        .filter(
            LeadCollectionTemplate.user_id == user_id,
            LeadCollectionTemplate.status == "active",
            LeadCollectionTemplate.id.in_(ids),
        )
        .all()
    )
    by_id = {int(row.id): row for row in rows}
    ordered = [by_id[tid] for tid in ids if tid in by_id]
    if not ordered:
        raise HTTPException(status_code=404, detail="未找到可用采集模板")
    return ordered


async def _run_one_template(
    *,
    db: Session,
    current_user: User,
    template: LeadCollectionTemplate,
    title_prefix: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    payload = dict(template.request_payload or {})
    payload["title"] = payload.get("title") or template.title or template.name
    if title_prefix:
        payload["title"] = f"{title_prefix}-{template.name}"[:160]
    if template.platform in _SOCIAL_PLATFORMS:
        row = create_social_leads_job_from_payload(db=db, current_user=current_user, payload=payload, auto_run=False)
        meta = dict(row.meta or {})
        meta.update({"lead_collection_template_id": template.id, "scheduled_run_id": run_id})
        row.meta = meta
        db.commit()
        try:
            row = await run_social_leads_job_to_completion(db=db, current_user=current_user, row=row)
        except Exception as exc:
            try:
                db.refresh(row)
            except Exception:
                pass
            job_payload = social_leads_job_payload(row, db=db, include_sources=False)
            summary = _job_summary(row, job_payload, template)
            summary["status"] = "failed"
            summary["error"] = str(getattr(exc, "detail", None) or exc)[:2000] or summary.get("error", "")
            return summary
        job_payload = social_leads_job_payload(row, db=db, include_sources=False)
    else:
        row = create_linkedin_mining_job_from_payload(db=db, current_user=current_user, payload=payload, auto_run=False)
        meta = dict(row.meta or {})
        meta.update({"lead_collection_template_id": template.id, "scheduled_run_id": run_id})
        row.meta = meta
        db.commit()
        try:
            row = await run_linkedin_mining_job_to_completion(db=db, current_user=current_user, row=row)
        except Exception as exc:
            try:
                db.refresh(row)
            except Exception:
                pass
            job_payload = linkedin_mining_job_payload(row)
            summary = _job_summary(row, job_payload, template)
            summary["status"] = "failed"
            summary["error"] = str(getattr(exc, "detail", None) or exc)[:2000] or summary.get("error", "")
            return summary
        job_payload = linkedin_mining_job_payload(row)
    return _job_summary(row, job_payload, template)


async def run_lead_collection_templates_scheduled(
    *,
    db: Session,
    current_user: User,
    template_ids: list[int],
    title: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    templates = _templates_for_user(db, current_user.id, template_ids)
    started_at = datetime.utcnow()
    jobs: list[dict[str, Any]] = []
    for template in templates:
        try:
            jobs.append(
                await _run_one_template(
                    db=db,
                    current_user=current_user,
                    template=template,
                    title_prefix=title,
                    run_id=run_id,
                )
            )
        except Exception as exc:
            db.rollback()
            jobs.append(
                {
                    "template_id": template.id,
                    "template_name": template.name,
                    "platform": template.platform,
                    "job_id": "",
                    "title": template.title or template.name,
                    "status": "failed",
                    "progress": 0,
                    "candidate_count": 0,
                    "error": str(getattr(exc, "detail", None) or exc)[:2000],
                }
            )
    by_platform: dict[str, dict[str, Any]] = {}
    for item in jobs:
        p = str(item.get("platform") or "")
        bucket = by_platform.setdefault(p, {"platform": p, "job_count": 0, "candidate_count": 0, "failed_count": 0})
        bucket["job_count"] += 1
        bucket["candidate_count"] += int(item.get("candidate_count") or 0)
        if item.get("status") == "failed" or item.get("error"):
            bucket["failed_count"] += 1
    return {
        "template_count": len(templates),
        "job_count": len(jobs),
        "failed_count": sum(1 for item in jobs if item.get("status") == "failed" or item.get("error")),
        "candidate_count": sum(int(item.get("candidate_count") or 0) for item in jobs),
        "jobs": jobs,
        "by_platform": list(by_platform.values()),
        "started_at": started_at.isoformat(),
        "finished_at": datetime.utcnow().isoformat(),
    }


@router.get("/api/lead-collection/templates", summary="线索采集模板列表")
def list_lead_collection_templates(
    platform: str = Query(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(LeadCollectionTemplate).filter(
        LeadCollectionTemplate.user_id == current_user.id,
        LeadCollectionTemplate.status == "active",
    )
    if platform.strip():
        q = q.filter(LeadCollectionTemplate.platform == _platform(platform))
    rows = q.order_by(LeadCollectionTemplate.updated_at.desc(), LeadCollectionTemplate.id.desc()).all()
    return {"ok": True, "items": [_template_payload(row) for row in rows]}


@router.post("/api/lead-collection/templates", summary="创建线索采集模板")
def create_lead_collection_template(
    body: LeadCollectionTemplateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    platform = _platform(body.platform or (body.request_payload or {}).get("platform"))
    payload = _normalize_template_request(platform, body.request_payload or {}, body.title)
    name = _clean_name(body.name, payload.get("title") or f"{platform}采集模板")
    title = str(body.title or payload.get("title") or name).strip()[:160]
    row = (
        db.query(LeadCollectionTemplate)
        .filter(
            LeadCollectionTemplate.user_id == current_user.id,
            LeadCollectionTemplate.platform == platform,
            LeadCollectionTemplate.name == name,
        )
        .first()
    )
    if row is None:
        row = LeadCollectionTemplate(user_id=current_user.id, platform=platform, name=name, created_at=datetime.utcnow())
        db.add(row)
    row.title = title
    row.request_payload = payload
    row.status = "active"
    row.meta = body.meta or {}
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"ok": True, "template": _template_payload(row)}


@router.patch("/api/lead-collection/templates/{template_id}", summary="更新线索采集模板")
def update_lead_collection_template(
    template_id: int,
    body: LeadCollectionTemplatePatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(LeadCollectionTemplate)
        .filter(LeadCollectionTemplate.user_id == current_user.id, LeadCollectionTemplate.id == template_id)
        .first()
    )
    if row is None or row.status != "active":
        raise HTTPException(status_code=404, detail="模板不存在")
    name = _clean_name(body.name if body.name is not None else row.name, row.name)
    duplicate = (
        db.query(LeadCollectionTemplate.id)
        .filter(
            LeadCollectionTemplate.user_id == current_user.id,
            LeadCollectionTemplate.platform == row.platform,
            LeadCollectionTemplate.name == name,
            LeadCollectionTemplate.id != row.id,
            LeadCollectionTemplate.status == "active",
        )
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="同平台下已存在同名模板")
    title = str(body.title if body.title is not None else (row.title or name)).strip()[:160]
    payload = row.request_payload or {}
    if body.request_payload is not None:
        payload = _normalize_template_request(row.platform, body.request_payload or {}, title)
    row.name = name
    row.title = title
    row.request_payload = payload
    if body.meta is not None:
        row.meta = body.meta
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"ok": True, "template": _template_payload(row)}


@router.delete("/api/lead-collection/templates/{template_id}", summary="删除线索采集模板")
def delete_lead_collection_template(
    template_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(LeadCollectionTemplate)
        .filter(LeadCollectionTemplate.user_id == current_user.id, LeadCollectionTemplate.id == template_id)
        .first()
    )
    if row is None or row.status != "active":
        raise HTTPException(status_code=404, detail="模板不存在")
    row.status = "deleted"
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "deleted": True, "template_id": template_id}


@router.post("/api/lead-collection/template-runs", summary="立即执行线索采集模板")
async def run_lead_collection_templates(
    body: LeadCollectionTemplateRunIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = await run_lead_collection_templates_scheduled(
        db=db,
        current_user=current_user,
        template_ids=body.template_ids,
        title=body.title,
    )
    return {"ok": True, "result": result}
