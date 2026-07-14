from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CreativeGenerationJob, GlobalLeadCrmContact, GlobalLeadJob, User
from .auth import get_current_user
from .linkedin_mining import (
    create_linkedin_mining_job_from_payload,
    linkedin_mining_job_payload,
)
from .social_leads import (
    create_social_leads_job_from_payload,
    social_leads_job_payload,
)

router = APIRouter()


SOURCE_CATALOG: list[dict[str, str]] = [
    {"id": "google", "name": "Google", "kind": "search", "status": "search_link"},
    {"id": "bing", "name": "Bing", "kind": "search", "status": "search_link"},
    {"id": "yandex", "name": "Yandex", "kind": "search", "status": "search_link"},
    {"id": "yahoo", "name": "Yahoo", "kind": "search", "status": "search_link"},
    {"id": "linkedin", "name": "LinkedIn", "kind": "people", "status": "connected"},
    {"id": "x", "name": "X", "kind": "social", "status": "connected"},
    {"id": "tiktok", "name": "TikTok", "kind": "social", "status": "connected"},
    {"id": "reddit", "name": "Reddit", "kind": "community", "status": "conditional"},
    {"id": "facebook", "name": "Facebook", "kind": "social", "status": "needs_connector"},
    {"id": "whatsapp", "name": "WhatsApp", "kind": "messaging", "status": "needs_connector"},
    {"id": "crunchbase", "name": "Crunchbase", "kind": "company", "status": "needs_connector"},
    {"id": "zoominfo", "name": "ZoomInfo", "kind": "people", "status": "needs_connector"},
    {"id": "apollo", "name": "Apollo", "kind": "people", "status": "needs_connector"},
    {"id": "tradeatlas", "name": "TradeAtlas", "kind": "trade", "status": "needs_connector"},
    {"id": "10times", "name": "10times", "kind": "event", "status": "needs_connector"},
    {"id": "glassdoor", "name": "Glassdoor", "kind": "company", "status": "needs_connector"},
]
SOURCE_BY_ID = {row["id"]: row for row in SOURCE_CATALOG}
CONNECTED_SOURCES = {"linkedin", "x", "tiktok"}
TERMINAL = {"completed", "failed", "canceled", "stale", "deleted"}


class GlobalLeadJobIn(BaseModel):
    title: str = Field("", max_length=180)
    company_name: str = Field("", max_length=255)
    domain: str = Field("", max_length=255)
    region: str = Field("", max_length=120)
    target_profile: str = Field("", max_length=2000)
    keywords: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    reddit_communities: list[str] = Field(default_factory=list)
    max_items: int = Field(80, ge=10, le=300)
    auto_run: bool = True


class GlobalLeadStatusPatch(BaseModel):
    status: str = Field("", max_length=32)
    tags: list[str] = Field(default_factory=list)


def _now() -> datetime:
    return datetime.utcnow()


def _clean_text(value: Any, limit: int = 255) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _clean_long(value: Any, limit: int = 2000) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\r\n?", "\n", text)
    return text[:limit]


def _clean_list(values: Any, *, limit: int = 20) -> list[str]:
    if isinstance(values, str):
        raw = re.split(r"[\n,，;；]+", values)
    elif isinstance(values, list):
        raw = values
    else:
        raw = []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _clean_text(item, 160)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _normalize_domain(value: Any) -> str:
    text = _clean_text(value, 255).lower()
    text = re.sub(r"^https?://", "", text)
    text = text.split("/")[0].strip()
    text = text[4:] if text.startswith("www.") else text
    return text[:255]


def _source_ids(values: list[str]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    raw = values or ["google", "bing", "linkedin", "x", "tiktok", "facebook", "crunchbase", "zoominfo", "tradeatlas", "10times", "glassdoor"]
    for item in raw:
        sid = _clean_text(item, 40).lower().replace("_", "-")
        if sid == "twitter":
            sid = "x"
        if sid in SOURCE_BY_ID and sid not in seen:
            seen.add(sid)
            ids.append(sid)
    return ids


def _search_terms(payload: dict[str, Any]) -> list[str]:
    terms = []
    for value in [payload.get("company_name"), payload.get("domain"), payload.get("region")]:
        if _clean_text(value):
            terms.append(_clean_text(value))
    terms.extend(_clean_list(payload.get("keywords"), limit=8))
    return _clean_list(terms, limit=12)


def _source_search_url(source_id: str, terms: list[str]) -> str:
    query = "+".join([re.sub(r"\s+", "+", term.strip()) for term in terms if term.strip()])
    if source_id == "google":
        return f"https://www.google.com/search?q={query}"
    if source_id == "bing":
        return f"https://www.bing.com/search?q={query}"
    if source_id == "yandex":
        return f"https://yandex.com/search/?text={query}"
    if source_id == "yahoo":
        return f"https://search.yahoo.com/search?p={query}"
    if source_id == "facebook":
        return f"https://www.facebook.com/search/top?q={query}"
    if source_id == "crunchbase":
        return f"https://www.crunchbase.com/discover/organization.companies/field/organizations/identifier/{query}"
    if source_id == "zoominfo":
        return f"https://www.zoominfo.com/search#q={query}"
    if source_id == "apollo":
        return f"https://app.apollo.io/#/people?finderKeywords={query}"
    if source_id == "tradeatlas":
        return f"https://www.tradeatlas.com/search?q={query}"
    if source_id == "10times":
        return f"https://10times.com/search?q={query}"
    if source_id == "glassdoor":
        return f"https://www.glassdoor.com/Search/results.htm?keyword={query}"
    return ""


def _build_source_plan(source_ids: list[str], payload: dict[str, Any]) -> list[dict[str, Any]]:
    terms = _search_terms(payload)
    plan: list[dict[str, Any]] = []
    for sid in source_ids:
        info = SOURCE_BY_ID[sid]
        connected = sid in CONNECTED_SOURCES
        status = "queued" if connected else ("ready" if info["status"] == "search_link" else "needs_connector")
        if sid == "reddit" and _clean_list(payload.get("reddit_communities"), limit=12):
            status = "queued"
        elif sid == "reddit":
            status = "needs_input"
        plan.append(
            {
                "id": sid,
                "name": info["name"],
                "kind": info["kind"],
                "status": status,
                "search_url": _source_search_url(sid, terms),
                "message": "",
                "job_id": "",
                "lead_count": 0,
                "updated_at": _now().isoformat(),
            }
        )
    return plan


def _job_title(body: GlobalLeadJobIn) -> str:
    if _clean_text(body.title):
        return _clean_text(body.title, 180)
    parts = [_clean_text(body.company_name), _normalize_domain(body.domain), _clean_text(body.region)]
    title = " - ".join([x for x in parts if x])
    return title or "全球获客任务"


def _job_payload(row: GlobalLeadJob, *, crm_count: Optional[int] = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "user_id": row.user_id,
        "status": row.status,
        "stage": row.stage or "",
        "progress": row.progress or 0,
        "title": row.title or "",
        "company_name": row.company_name or "",
        "domain": row.domain or "",
        "region": row.region or "",
        "target_profile": row.target_profile or "",
        "request_payload": row.request_payload or {},
        "source_plan": row.source_plan or [],
        "child_jobs": row.child_jobs or [],
        "result_payload": row.result_payload or {},
        "crm_count": crm_count,
        "error": row.error or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "completed_at": row.completed_at.isoformat() if row.completed_at else "",
    }


def _contact_payload(row: GlobalLeadCrmContact) -> dict[str, Any]:
    return {
        "id": row.id,
        "job_id": row.job_id or "",
        "entity_type": row.entity_type,
        "name": row.name or "",
        "company": row.company or "",
        "role": row.role or "",
        "domain": row.domain or "",
        "region": row.region or "",
        "email": row.email or "",
        "phone": row.phone or "",
        "social_handle": row.social_handle or "",
        "profile_url": row.profile_url or "",
        "source_platform": row.source_platform or "",
        "source_url": row.source_url or "",
        "score": row.score or 0,
        "status": row.status,
        "tags": row.tags or [],
        "evidence": row.evidence or [],
        "raw": row.raw or {},
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _dedupe_key(user_id: int, item: dict[str, Any]) -> str:
    for key in ("profile_url", "email", "source_url"):
        value = _clean_text(item.get(key), 255).lower()
        if value:
            return f"{key}:{value}"[:255]
    domain = _normalize_domain(item.get("domain"))
    entity_type = _clean_text(item.get("entity_type"), 32)
    if domain and entity_type == "company":
        return f"company-domain:{domain}"[:255]
    handle = _clean_text(item.get("social_handle") or item.get("handle"), 255).lower()
    platform = _clean_text(item.get("source_platform"), 64).lower()
    if handle:
        return f"{platform}:handle:{handle}"[:255]
    base = "|".join(
        [
            str(user_id),
            platform,
            _clean_text(item.get("name"), 120).lower(),
            _clean_text(item.get("company"), 120).lower(),
            _clean_text(item.get("job_id"), 64).lower(),
        ]
    )
    return f"fallback:{base}"[:255]


def _upsert_contact(db: Session, user_id: int, item: dict[str, Any]) -> GlobalLeadCrmContact:
    key = _dedupe_key(user_id, item)
    row = (
        db.query(GlobalLeadCrmContact)
        .filter(GlobalLeadCrmContact.user_id == user_id, GlobalLeadCrmContact.dedupe_key == key)
        .first()
    )
    if row is None:
        row = GlobalLeadCrmContact(user_id=user_id, dedupe_key=key, created_at=_now())
        db.add(row)
    row.job_id = _clean_text(item.get("job_id"), 64) or row.job_id
    row.entity_type = _clean_text(item.get("entity_type"), 32) or "person"
    row.name = _clean_text(item.get("name"), 255) or row.name or ""
    row.company = _clean_text(item.get("company"), 255) or row.company
    row.role = _clean_text(item.get("role"), 255) or row.role
    row.domain = _normalize_domain(item.get("domain")) or row.domain
    row.region = _clean_text(item.get("region"), 120) or row.region
    row.email = _clean_text(item.get("email"), 255) or row.email
    row.phone = _clean_text(item.get("phone"), 80) or row.phone
    row.social_handle = _clean_text(item.get("social_handle") or item.get("handle"), 255) or row.social_handle
    row.profile_url = _clean_long(item.get("profile_url") or item.get("url"), 2000) or row.profile_url
    row.source_platform = _clean_text(item.get("source_platform"), 64) or row.source_platform or ""
    row.source_url = _clean_long(item.get("source_url") or item.get("profile_url") or item.get("url"), 2000) or row.source_url
    row.score = max(row.score or 0, int(item.get("score") or 0))
    row.tags = item.get("tags") if isinstance(item.get("tags"), list) else (row.tags or [])
    row.evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else (row.evidence or [])
    row.raw = item.get("raw") if isinstance(item.get("raw"), dict) else (row.raw or {})
    row.updated_at = _now()
    return row


def _seed_company_contact(db: Session, row: GlobalLeadJob) -> None:
    if not (row.company_name or row.domain):
        return
    _upsert_contact(
        db,
        row.user_id,
        {
            "job_id": row.job_id,
            "entity_type": "company",
            "name": row.company_name or row.domain or "",
            "company": row.company_name or "",
            "domain": row.domain or "",
            "region": row.region or "",
            "source_platform": "query",
            "source_url": f"https://{row.domain}" if row.domain else "",
            "score": 20,
            "tags": ["企业入口"],
            "evidence": [{"title": "用户提交的企业/域名/区域", "text": row.target_profile or ""}],
            "raw": row.request_payload or {},
        },
    )


def _normalize_child_lead(raw: dict[str, Any], *, row: GlobalLeadJob, platform: str, child_job_id: str) -> dict[str, Any]:
    evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else []
    url = raw.get("url") or raw.get("profile_url") or raw.get("source") or ""
    return {
        "job_id": row.job_id,
        "entity_type": "person",
        "name": raw.get("name") or raw.get("display_name") or raw.get("username") or raw.get("handle") or raw.get("company") or "",
        "company": raw.get("company") or raw.get("company_name") or row.company_name or "",
        "role": raw.get("role") or raw.get("title") or raw.get("headline") or "",
        "domain": row.domain or "",
        "region": row.region or "",
        "email": raw.get("email") or "",
        "phone": raw.get("phone") or "",
        "social_handle": raw.get("handle") or raw.get("username") or raw.get("screen_name") or "",
        "profile_url": url,
        "source_platform": platform,
        "source_url": url,
        "score": int(raw.get("score") or raw.get("relevance_score") or 0),
        "tags": [platform],
        "evidence": evidence,
        "raw": {"child_job_id": child_job_id, "lead": raw},
    }


def _extract_child_leads(payload: dict[str, Any], *, platform: str) -> list[dict[str, Any]]:
    result = payload.get("result_payload") if isinstance(payload.get("result_payload"), dict) else {}
    report = result.get("report") if isinstance(result.get("report"), dict) else {}
    lead_summary = result.get("lead_summary") if isinstance(result.get("lead_summary"), dict) else {}
    candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
    groups = [
        report.get("priority_leads"),
        lead_summary.get("top_leads"),
        lead_summary.get("candidates"),
        candidates,
    ]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            key = str(item.get("profile_url") or item.get("url") or item.get("handle") or item.get("name") or "")
            if not key:
                continue
            dedupe = f"{platform}:{key.lower()}"
            if dedupe in seen:
                continue
            seen.add(dedupe)
            out.append(item)
    return out


def _source_plan_by_id(plan: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id") or ""): item for item in plan if isinstance(item, dict)}


def _refresh_global_job(db: Session, row: GlobalLeadJob) -> GlobalLeadJob:
    child_jobs = [item for item in (row.child_jobs or []) if isinstance(item, dict)]
    plan = [item for item in (row.source_plan or []) if isinstance(item, dict)]
    plan_map = _source_plan_by_id(plan)
    synced = set((row.result_payload or {}).get("synced_child_jobs") or [])
    imported = int((row.result_payload or {}).get("imported_contacts") or 0)
    running = 0
    failed = 0
    completed = 0

    for child in child_jobs:
        platform = str(child.get("platform") or "").strip().lower()
        child_job_id = str(child.get("job_id") or "").strip()
        if not child_job_id:
            continue
        child_row = (
            db.query(CreativeGenerationJob)
            .filter(CreativeGenerationJob.user_id == row.user_id, CreativeGenerationJob.job_id == child_job_id)
            .first()
        )
        if child_row is None:
            continue
        child["status"] = child_row.status
        child["progress"] = child_row.progress or 0
        child["updated_at"] = child_row.updated_at.isoformat() if child_row.updated_at else ""
        item = plan_map.get(platform)
        if item:
            item["status"] = child_row.status
            item["job_id"] = child_job_id
            item["updated_at"] = child["updated_at"]
            item["message"] = child_row.error or ""
        if child_row.status in TERMINAL:
            if child_row.status == "failed":
                failed += 1
            else:
                completed += 1
            if child_job_id not in synced and child_row.status == "completed":
                payload = linkedin_mining_job_payload(child_row) if platform == "linkedin" else social_leads_job_payload(child_row, db=db, include_sources=True)
                leads = _extract_child_leads(payload, platform=platform)
                count = 0
                for lead in leads:
                    _upsert_contact(db, row.user_id, _normalize_child_lead(lead, row=row, platform=platform, child_job_id=child_job_id))
                    count += 1
                if item:
                    item["lead_count"] = int(item.get("lead_count") or 0) + count
                imported += count
                synced.add(child_job_id)
        else:
            running += 1

    row.child_jobs = child_jobs
    row.source_plan = list(plan_map.values()) if plan_map else plan
    row.result_payload = {
        **(row.result_payload or {}),
        "synced_child_jobs": sorted(synced),
        "imported_contacts": imported,
        "child_job_count": len(child_jobs),
        "completed_child_jobs": completed,
        "failed_child_jobs": failed,
    }
    if child_jobs:
        if running:
            row.status = "running"
            row.stage = "collecting"
            row.progress = max(row.progress or 0, min(95, int((completed + failed) / max(1, len(child_jobs)) * 90)))
        else:
            row.status = "completed" if failed < len(child_jobs) else "failed"
            row.stage = "completed" if row.status == "completed" else "failed"
            row.progress = 100
            row.completed_at = row.completed_at or _now()
    else:
        row.status = "completed"
        row.stage = "ready_for_connectors"
        row.progress = 100
        row.completed_at = row.completed_at or _now()
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row


def _create_child_jobs(db: Session, current_user: User, row: GlobalLeadJob, body: GlobalLeadJobIn) -> list[dict[str, Any]]:
    payload = row.request_payload or {}
    terms = _search_terms(payload)
    title = row.title or "全球获客任务"
    children: list[dict[str, Any]] = []
    selected = {str(item.get("id") or "") for item in (row.source_plan or []) if isinstance(item, dict)}
    if "linkedin" in selected:
        child = create_linkedin_mining_job_from_payload(
            db=db,
            current_user=current_user,
            payload={
                "title": f"{title} - LinkedIn",
                "keywords": terms[:12],
                "target_profile": row.target_profile or "寻找目标企业相关负责人、采购、市场、BD、创始人等潜在线索",
                "max_people": min(body.max_items, 100),
                "max_company_employees": 20,
                "max_interactions_per_post": 20,
                "auto_run": bool(body.auto_run),
            },
            auto_run=bool(body.auto_run),
        )
        meta = dict(child.meta or {})
        meta["global_leads_job_id"] = row.job_id
        child.meta = meta
        children.append({"platform": "linkedin", "job_id": child.job_id, "status": child.status, "progress": child.progress or 0})

    for platform in ("x", "tiktok"):
        if platform not in selected:
            continue
        child = create_social_leads_job_from_payload(
            db=db,
            current_user=current_user,
            payload={
                "platform": platform,
                "title": f"{title} - {platform.upper()}",
                "keywords": terms[:12],
                "source_keywords": terms[:12],
                "country": row.region or "",
                "max_items": min(body.max_items, 100),
                "include_comments": True,
                "include_account_posts": True,
                "auto_run": bool(body.auto_run),
            },
            auto_run=bool(body.auto_run),
        )
        meta = dict(child.meta or {})
        meta["global_leads_job_id"] = row.job_id
        child.meta = meta
        children.append({"platform": platform, "job_id": child.job_id, "status": child.status, "progress": child.progress or 0})

    if "reddit" in selected and _clean_list(body.reddit_communities, limit=12):
        child = create_social_leads_job_from_payload(
            db=db,
            current_user=current_user,
            payload={
                "platform": "reddit",
                "title": f"{title} - Reddit",
                "keywords": terms[:12],
                "communities": _clean_list(body.reddit_communities, limit=12),
                "country": row.region or "",
                "max_items": min(body.max_items, 100),
                "include_comments": True,
                "include_account_posts": True,
                "auto_run": bool(body.auto_run),
            },
            auto_run=bool(body.auto_run),
        )
        meta = dict(child.meta or {})
        meta["global_leads_job_id"] = row.job_id
        child.meta = meta
        children.append({"platform": "reddit", "job_id": child.job_id, "status": child.status, "progress": child.progress or 0})

    db.commit()
    return children


@router.get("/api/global-leads/source-catalog", summary="Global lead source catalog")
def global_lead_source_catalog(current_user: User = Depends(get_current_user)):
    return {"ok": True, "items": SOURCE_CATALOG}


@router.post("/api/global-leads/jobs", summary="Create global company lead job")
async def create_global_lead_job(
    body: GlobalLeadJobIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    company_name = _clean_text(body.company_name, 255)
    domain = _normalize_domain(body.domain)
    region = _clean_text(body.region, 120)
    keywords = _clean_list(body.keywords, limit=12)
    if not (company_name or domain or keywords):
        raise HTTPException(status_code=400, detail="请填写企业名称、域名或获客关键词")
    payload = {
        "company_name": company_name,
        "domain": domain,
        "region": region,
        "target_profile": _clean_long(body.target_profile, 2000),
        "keywords": keywords,
        "sources": _source_ids(body.sources),
        "reddit_communities": _clean_list(body.reddit_communities, limit=12),
        "max_items": int(body.max_items or 80),
    }
    job_id = "gl_" + uuid.uuid4().hex[:24]
    row = GlobalLeadJob(
        job_id=job_id,
        user_id=current_user.id,
        status="queued",
        stage="planning",
        progress=5,
        title=_job_title(body),
        company_name=company_name,
        domain=domain,
        region=region,
        target_profile=payload["target_profile"],
        request_payload=payload,
        source_plan=_build_source_plan(payload["sources"], payload),
        child_jobs=[],
        result_payload={},
        meta={"created_from": "online_global_leads"},
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _seed_company_contact(db, row)
    children = _create_child_jobs(db, current_user, row, body)
    row.child_jobs = children
    row.status = "running" if children else "completed"
    row.stage = "collecting" if children else "ready_for_connectors"
    row.progress = 15 if children else 100
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    row = _refresh_global_job(db, row)
    crm_count = db.query(GlobalLeadCrmContact).filter(
        GlobalLeadCrmContact.user_id == current_user.id,
        GlobalLeadCrmContact.job_id == row.job_id,
        GlobalLeadCrmContact.deleted_at.is_(None),
    ).count()
    return {"ok": True, "job": _job_payload(row, crm_count=crm_count)}


@router.get("/api/global-leads/jobs", summary="List global lead jobs")
def list_global_lead_jobs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str = Query(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(GlobalLeadJob).filter(
        GlobalLeadJob.user_id == current_user.id,
        GlobalLeadJob.deleted_at.is_(None),
    )
    text = _clean_text(q, 120)
    if text:
        like = f"%{text}%"
        query = query.filter(
            or_(
                GlobalLeadJob.title.ilike(like),
                GlobalLeadJob.company_name.ilike(like),
                GlobalLeadJob.domain.ilike(like),
                GlobalLeadJob.region.ilike(like),
            )
        )
    total = query.count()
    rows = query.order_by(GlobalLeadJob.created_at.desc(), GlobalLeadJob.id.desc()).offset(offset).limit(limit).all()
    payloads = []
    for row in rows:
        row = _refresh_global_job(db, row)
        crm_count = db.query(GlobalLeadCrmContact).filter(
            GlobalLeadCrmContact.user_id == current_user.id,
            GlobalLeadCrmContact.job_id == row.job_id,
            GlobalLeadCrmContact.deleted_at.is_(None),
        ).count()
        payloads.append(_job_payload(row, crm_count=crm_count))
    return {"ok": True, "total": total, "items": payloads}


@router.get("/api/global-leads/jobs/{job_id}", summary="Get global lead job")
def get_global_lead_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(GlobalLeadJob)
        .filter(
            GlobalLeadJob.user_id == current_user.id,
            GlobalLeadJob.job_id == job_id.strip().lower(),
            GlobalLeadJob.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    row = _refresh_global_job(db, row)
    contacts = (
        db.query(GlobalLeadCrmContact)
        .filter(
            GlobalLeadCrmContact.user_id == current_user.id,
            GlobalLeadCrmContact.job_id == row.job_id,
            GlobalLeadCrmContact.deleted_at.is_(None),
        )
        .order_by(GlobalLeadCrmContact.score.desc(), GlobalLeadCrmContact.created_at.desc())
        .limit(50)
        .all()
    )
    return {"ok": True, "job": _job_payload(row, crm_count=len(contacts)), "contacts": [_contact_payload(x) for x in contacts]}


@router.get("/api/global-leads/crm", summary="Global lead CRM contacts")
def list_global_lead_crm(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str = Query(""),
    source: str = Query(""),
    job_id: str = Query(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(GlobalLeadCrmContact).filter(
        GlobalLeadCrmContact.user_id == current_user.id,
        GlobalLeadCrmContact.deleted_at.is_(None),
    )
    text = _clean_text(q, 120)
    if text:
        like = f"%{text}%"
        query = query.filter(
            or_(
                GlobalLeadCrmContact.name.ilike(like),
                GlobalLeadCrmContact.company.ilike(like),
                GlobalLeadCrmContact.role.ilike(like),
                GlobalLeadCrmContact.domain.ilike(like),
                GlobalLeadCrmContact.social_handle.ilike(like),
            )
        )
    if _clean_text(source, 64):
        query = query.filter(GlobalLeadCrmContact.source_platform == _clean_text(source, 64).lower())
    if _clean_text(job_id, 64):
        query = query.filter(GlobalLeadCrmContact.job_id == _clean_text(job_id, 64).lower())
    total = query.count()
    rows = query.order_by(GlobalLeadCrmContact.created_at.desc(), GlobalLeadCrmContact.id.desc()).offset(offset).limit(limit).all()
    return {"ok": True, "total": total, "items": [_contact_payload(row) for row in rows]}


@router.patch("/api/global-leads/crm/{contact_id}", summary="Update CRM lead status")
def update_global_lead_crm_contact(
    contact_id: int,
    body: GlobalLeadStatusPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(GlobalLeadCrmContact)
        .filter(
            GlobalLeadCrmContact.user_id == current_user.id,
            GlobalLeadCrmContact.id == contact_id,
            GlobalLeadCrmContact.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="线索不存在")
    status = _clean_text(body.status, 32)
    if status:
        row.status = status
    if body.tags:
        row.tags = _clean_list(body.tags, limit=20)
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return {"ok": True, "contact": _contact_payload(row)}
