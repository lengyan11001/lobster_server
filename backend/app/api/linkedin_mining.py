from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db, SessionLocal
from ..models import CreativeGenerationJob, TikHubSourceItem, User
from .auth import create_access_token, get_current_user
from .ip_content_studio import (
    _clean_long_text,
    _clean_text,
    _execute_query_with_retry,
    _extract_json_object,
    _jsonable,
    _lookup,
    _memory_payload_from_docs,
    _post_llm_with_retry,
    _stable_hash,
    _utcnow,
)

router = APIRouter()

_FEATURE_TYPE = "linkedin_mining"
_TERMINAL_STATUS = {"completed", "failed", "canceled", "stale"}


class LinkedInMiningStartBody(BaseModel):
    title: str = Field("", max_length=160)
    seed_profile_urls: list[str] = Field(default_factory=list)
    seed_company_urls: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    target_profile: str = Field("", max_length=2000)
    memory_docs: list[dict[str, Any]] = Field(default_factory=list)
    max_people: int = Field(30, ge=5, le=80)
    max_company_employees: int = Field(20, ge=0, le=50)
    max_interactions_per_post: int = Field(20, ge=0, le=50)
    auto_run: bool = True


class LinkedInMiningStepBody(BaseModel):
    step_key: str = ""


def _job_payload(row: CreativeGenerationJob) -> dict[str, Any]:
    meta = dict(row.meta or {})
    steps = meta.get("steps") if isinstance(meta.get("steps"), list) else []
    outputs = meta.get("outputs") if isinstance(meta.get("outputs"), list) else []
    current_step = meta.get("current_step") or ""
    return {
        "job_id": row.job_id,
        "status": row.status,
        "stage": row.stage or "",
        "progress": row.progress or 0,
        "title": row.title or "",
        "prompt": row.prompt or "",
        "request_payload": row.request_payload or {},
        "result_payload": row.result_payload or {},
        "error": row.error or "",
        "meta": meta,
        "steps": steps,
        "outputs": outputs,
        "current_step": current_step,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "completed_at": row.completed_at.isoformat() if row.completed_at else "",
    }


def _step(label: str, key: str, status: str = "pending", detail: str = "", result: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "result": result or {},
        "started_at": "",
        "finished_at": "",
        "attempts": 0,
        "error": "",
    }


def _initial_steps(request_payload: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    if request_payload.get("seed_usernames"):
        steps.append(_step("种子用户画像", "seed_profiles"))
        steps.append(_step("种子用户动态", "seed_activity"))
        steps.append(_step("基于用户发现候选人", "discovery_users"))
    if request_payload.get("seed_companies"):
        steps.append(_step("公司画像", "company_profiles"))
        steps.append(_step("公司员工与相似公司", "company_people"))
    if request_payload.get("keywords"):
        steps.append(_step("关键词搜索用户", "keyword_search"))
    if request_payload.get("hashtags"):
        steps.append(_step("话题内容与互动人群", "hashtag_feed"))
    steps.append(_step("候选人归并评分", "score_candidates"))
    steps.append(_step("生成分析报告", "summary_report"))
    return steps


def _meta(row: CreativeGenerationJob) -> dict[str, Any]:
    return dict(row.meta or {})


def _set_meta(row: CreativeGenerationJob, meta: dict[str, Any]) -> None:
    row.meta = _jsonable(meta)
    row.updated_at = _utcnow()


def _mark_step(row: CreativeGenerationJob, key: str, status: str, *, detail: str = "", result: Optional[dict[str, Any]] = None, error: str = "") -> None:
    meta = _meta(row)
    steps = meta.get("steps") if isinstance(meta.get("steps"), list) else []
    now = _utcnow().isoformat()
    for item in steps:
        if item.get("key") != key:
            continue
        if status == "running" and not item.get("started_at"):
            item["started_at"] = now
        if status in {"completed", "failed", "skipped"}:
            item["finished_at"] = now
        item["status"] = status
        item["detail"] = detail or item.get("detail") or ""
        if result is not None:
            item["result"] = _jsonable(result)
        if error:
            item["error"] = error[:2000]
        item["attempts"] = int(item.get("attempts") or 0) + (1 if status == "running" else 0)
        break
    meta["steps"] = steps
    meta["current_step"] = key if status == "running" else meta.get("current_step", "")
    _set_meta(row, meta)


def _append_output(row: CreativeGenerationJob, *, step_key: str, title: str, kind: str, data: Any) -> None:
    meta = _meta(row)
    outputs = meta.get("outputs") if isinstance(meta.get("outputs"), list) else []
    output_id = f"{step_key}_{_stable_hash({'title': title, 'data': data}, 16)}"
    existing = {str(item.get("id") or "") for item in outputs if isinstance(item, dict)}
    if output_id not in existing:
        outputs.append(
            {
                "id": output_id,
                "step_key": step_key,
                "title": _clean_text(title, 160),
                "kind": _clean_text(kind, 64),
                "created_at": _utcnow().isoformat(),
                "data": _jsonable(data),
            }
        )
    meta["outputs"] = outputs[-120:]
    _set_meta(row, meta)


def _extract_linkedin_username(value: Any) -> str:
    text = _clean_long_text(value, 1000)
    if not text:
        return ""
    text = text.strip().rstrip("/")
    match = re.search(r"linkedin\.com/in/([^/?#]+)", text, re.I)
    if match:
        return _clean_text(match.group(1), 120)
    if "/" not in text and " " not in text:
        return _clean_text(text.lstrip("@"), 120)
    return ""


def _extract_linkedin_company(value: Any) -> str:
    text = _clean_long_text(value, 1000)
    if not text:
        return ""
    text = text.strip().rstrip("/")
    match = re.search(r"linkedin\.com/company/([^/?#]+)", text, re.I)
    if match:
        return _clean_text(match.group(1), 120)
    if "/" not in text and " " not in text:
        return _clean_text(text.lstrip("@"), 120)
    return ""


def _clean_list(values: Any, *, limit: int = 20, max_len: int = 160) -> list[str]:
    out: list[str] = []
    raw = values if isinstance(values, list) else []
    for item in raw:
        text = _clean_text(item, max_len)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _post_urn(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    value = (
        _lookup(raw, "post_urn")
        or _lookup(raw, "urn")
        or _lookup(raw, "entity_urn")
        or _lookup(raw, "activity_urn")
        or _lookup(raw, "update_urn")
        or _lookup(raw, "reshared_update_urn")
    )
    return _clean_text(value, 255)


def _first_lookup(raw: Any, paths: tuple[str, ...]) -> Any:
    if not isinstance(raw, dict):
        return None
    for path in paths:
        value = _lookup(raw, path)
        if value not in (None, "", [], {}):
            return value
    return None


def _extract_linkedin_urn(raw: Any) -> str:
    value = _first_lookup(
        raw,
        (
            "urn",
            "entityUrn",
            "entity_urn",
            "profile_urn",
            "objectUrn",
            "member_urn",
            "profile.urn",
            "mini_profile.urn",
            "data.urn",
            "data.entityUrn",
        ),
    )
    return _clean_text(value, 255)


def _extract_company_id(raw: Any) -> str:
    value = _first_lookup(
        raw,
        (
            "company_id",
            "companyId",
            "id",
            "company.id",
            "data.company_id",
            "data.companyId",
            "data.id",
            "urn",
            "entityUrn",
        ),
    )
    text = _clean_text(value, 255)
    if not text:
        return ""
    match = re.search(r"\d{3,}", text)
    return match.group(0) if match else text


def _post_id(raw: Any) -> str:
    value = _first_lookup(
        raw,
        (
            "post_id",
            "postId",
            "id",
            "activity_id",
            "activityId",
            "activity",
            "urn",
            "entity_urn",
            "entityUrn",
            "activity_urn",
            "update_urn",
        ),
    )
    text = _clean_text(value, 255)
    if not text:
        return ""
    match = re.search(r"urn:li:(?:activity|ugcPost|share):([^,\s]+)", text)
    return match.group(1) if match else text


def _first_raw_entry(result: dict[str, Any]) -> dict[str, Any]:
    for item in _raw_entries_from_query_result(result):
        if isinstance(item, dict):
            return item
    raw = result.get("raw_response")
    if isinstance(raw, dict):
        return raw
    return {}


def _ensure_meta_map(row: CreativeGenerationJob, key: str) -> dict[str, Any]:
    meta = _meta(row)
    value = meta.get(key)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _save_meta_map(row: CreativeGenerationJob, key: str, value: dict[str, Any]) -> None:
    meta = _meta(row)
    meta[key] = _jsonable(value)
    _set_meta(row, meta)


def _profile_key_from_raw(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    value = (
        _lookup(raw, "username")
        or _lookup(raw, "public_identifier")
        or _lookup(raw, "publicIdentifier")
        or _lookup(raw, "public_id")
        or _lookup(raw, "profile_id")
        or _lookup(raw, "mini_profile.public_identifier")
        or _lookup(raw, "profile.public_identifier")
        or _lookup(raw, "miniProfile.publicIdentifier")
        or _lookup(raw, "profile.publicIdentifier")
        or _lookup(raw, "entity_urn")
        or _lookup(raw, "entityUrn")
        or _lookup(raw, "member_urn")
        or _lookup(raw, "urn")
    )
    return _clean_text(value, 191)


def _profile_name_from_raw(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    first = _lookup(raw, "first_name") or _lookup(raw, "firstName") or _lookup(raw, "mini_profile.first_name")
    last = _lookup(raw, "last_name") or _lookup(raw, "lastName") or _lookup(raw, "mini_profile.last_name")
    name = _lookup(raw, "name") or _lookup(raw, "full_name") or _lookup(raw, "fullName") or " ".join([str(first or "").strip(), str(last or "").strip()]).strip()
    return _clean_text(name, 255)


def _string_list(value: Any, limit: int = 8) -> list[str]:
    raw = value if isinstance(value, list) else ([value] if value not in (None, "") else [])
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            text = _clean_long_text(_first_lookup(item, ("url", "value", "text", "phone", "number", "address")) or item, 1000)
        else:
            text = _clean_long_text(item, 1000)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _contact_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    websites = _string_list(_lookup(raw, "websites") or _lookup(raw, "website") or _lookup(raw, "website_url"))
    phone_numbers = _string_list(_lookup(raw, "phone_numbers") or _lookup(raw, "phone") or _lookup(raw, "phoneNumbers"))
    email = _clean_text(_lookup(raw, "email") or _lookup(raw, "email_address") or _lookup(raw, "emailAddress"), 255)
    wechat = _clean_text(_lookup(raw, "wechat") or _lookup(raw, "weixin"), 255)
    twitter = _string_list(_lookup(raw, "twitter") or _lookup(raw, "twitter_handles"))
    address = _clean_long_text(_lookup(raw, "address"), 1000)
    out = {
        "email": email,
        "websites": websites,
        "phone_numbers": phone_numbers,
        "wechat": wechat,
        "twitter": twitter,
        "address": address,
    }
    return {k: v for k, v in out.items() if v not in ("", [], {}, None)}


def _has_contact(item: dict[str, Any]) -> bool:
    contact = item.get("contact") if isinstance(item.get("contact"), dict) else {}
    return any(contact.get(key) for key in ("email", "websites", "phone_numbers", "wechat", "twitter", "address"))


def _candidate_next_action(item: dict[str, Any]) -> str:
    contact = item.get("contact") if isinstance(item.get("contact"), dict) else {}
    if contact.get("email"):
        return "优先邮件触达，结合其职位/发帖证据写一封短邮件。"
    if contact.get("websites"):
        return "先访问公开网站或个人主页，补充公司/业务背景后再触达。"
    if contact.get("phone_numbers") or contact.get("wechat"):
        return "可直接人工触达，先确认身份和业务相关性。"
    if item.get("url"):
        return "先打开 LinkedIn 主页核对背景，再通过站内互动或其他公开渠道补联系方式。"
    return "先保留为待补充线索，后续用公司/关键词继续扩展公开联系方式。"


def _lead_summary_payload(candidates: list[dict[str, Any]], rows: list[TikHubSourceItem]) -> dict[str, Any]:
    contact_count = sum(1 for item in candidates if isinstance(item, dict) and _has_contact(item))
    source_counts: dict[str, int] = {}
    for row in rows:
        source_counts[row.source_type] = source_counts.get(row.source_type, 0) + 1
    top_leads = []
    for item in candidates[:30]:
        if not isinstance(item, dict):
            continue
        top_leads.append(
            {
                "rank": item.get("rank"),
                "score": item.get("score"),
                "name": item.get("name") or item.get("candidate_key") or "",
                "headline": item.get("headline") or "",
                "company": item.get("company") or "",
                "url": item.get("url") or "",
                "contact": item.get("contact") or {},
                "source_reason": item.get("source_reason") or "",
                "evidence_count": len(item.get("evidence") or []),
                "next_action": _candidate_next_action(item),
            }
        )
    return {
        "summary": {
            "candidate_count": len(candidates),
            "with_public_contact": contact_count,
            "source_rows": len(rows),
            "source_counts": source_counts,
        },
        "top_leads": top_leads,
    }


def _brief_text(value: Any, limit: int = 500) -> str:
    if isinstance(value, dict):
        for key in ("text", "commentary", "description", "summary", "headline", "title", "content"):
            if value.get(key):
                return _clean_long_text(value.get(key), limit)
        return _clean_long_text(json.dumps(value, ensure_ascii=False), limit)
    return _clean_long_text(value, limit)


def _collect_raw_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "results", "data", "included", "elements", "list", "records", "posts", "comments", "reactions", "employees", "companies", "users"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _collect_raw_items(value)
            if nested:
                return nested
    return []


def _raw_entries_from_query_result(result: dict[str, Any]) -> list[Any]:
    raw = result.get("raw_response")
    items = _collect_raw_items(raw)
    if items:
        return items
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, dict):
            nested = _collect_raw_items(data)
            if nested:
                return nested
            return [data]
        return [raw]
    return []


def _query_rows_for_job(db: Session, user_id: int, job_id: str) -> list[TikHubSourceItem]:
    rows = (
        db.query(TikHubSourceItem)
        .filter(TikHubSourceItem.user_id == user_id, TikHubSourceItem.platform == "linkedin")
        .order_by(TikHubSourceItem.created_at.desc(), TikHubSourceItem.id.desc())
        .limit(500)
        .all()
    )
    out: list[TikHubSourceItem] = []
    for row in rows:
        raw = row.raw if isinstance(row.raw, dict) else {}
        meta = raw.get("__lobster_ip_content_meta") if isinstance(raw.get("__lobster_ip_content_meta"), dict) else {}
        if str(meta.get("linkedin_job_id") or "") == job_id:
            out.append(row)
    return out


def _normalize_candidate_from_row(row: TikHubSourceItem) -> Optional[dict[str, Any]]:
    raw = row.raw if isinstance(row.raw, dict) else {}
    meta = raw.get("__lobster_ip_content_meta") if isinstance(raw.get("__lobster_ip_content_meta"), dict) else {}
    body = raw.get("raw") if isinstance(raw.get("raw"), dict) else raw
    key = row.author_key or _profile_key_from_raw(body) or row.item_key
    name = row.author_name or _profile_name_from_raw(body) or row.title or key
    if not key and not name:
        return None
    headline = _lookup(body, "headline") or _lookup(body, "occupation") or row.description or ""
    company = _lookup(body, "company_name") or _lookup(body, "current_company.name") or _lookup(body, "company.name") or ""
    url = row.public_url or _lookup(body, "url") or _lookup(body, "profile_url") or ""
    contact = _contact_payload(body)
    return {
        "candidate_key": _clean_text(key, 191),
        "name": _clean_text(name, 255),
        "headline": _clean_long_text(headline, 600),
        "company": _clean_text(company, 255),
        "contact": contact,
        "source_type": row.source_type,
        "source_reason": meta.get("source_reason") or meta.get("source") or row.source_type,
        "url": _clean_long_text(url, 1000),
        "evidence": [
            {
                "source_item_id": row.id,
                "source_type": row.source_type,
                "title": row.title or "",
                "description": row.description or "",
                "created_at": row.created_at.isoformat() if row.created_at else "",
            }
        ],
        "raw": body,
    }


def _merge_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in candidates:
        key = item.get("candidate_key") or item.get("name") or _stable_hash(item, 16)
        if key not in merged:
            merged[key] = {**item, "evidence": list(item.get("evidence") or [])}
            continue
        cur = merged[key]
        for field in ("name", "headline", "company", "url"):
            if not cur.get(field) and item.get(field):
                cur[field] = item[field]
        if item.get("contact"):
            cur_contact = cur.setdefault("contact", {})
            for key, value in (item.get("contact") or {}).items():
                if not value:
                    continue
                if isinstance(value, list):
                    cur_contact.setdefault(key, [])
                    for entry in value:
                        if entry not in cur_contact[key]:
                            cur_contact[key].append(entry)
                elif not cur_contact.get(key):
                    cur_contact[key] = value
        existing_evidence = {str(x.get("source_item_id") or "") for x in cur.get("evidence") or [] if isinstance(x, dict)}
        for ev in item.get("evidence") or []:
            ev_id = str(ev.get("source_item_id") or "")
            if ev_id and ev_id not in existing_evidence:
                cur.setdefault("evidence", []).append(ev)
                existing_evidence.add(ev_id)
    out = list(merged.values())
    out.sort(key=lambda x: len(x.get("evidence") or []), reverse=True)
    return out


def _candidate_from_raw(raw: Any, *, source_type: str, source_reason: str) -> Optional[dict[str, Any]]:
    body = raw if isinstance(raw, dict) else {"value": raw}
    key = _profile_key_from_raw(body)
    name = _profile_name_from_raw(body)
    if not key and not name:
        return None
    headline = _lookup(body, "headline") or _lookup(body, "occupation") or _lookup(body, "summary") or ""
    company = _lookup(body, "company_name") or _lookup(body, "current_company.name") or _lookup(body, "company.name") or ""
    url = _lookup(body, "url") or _lookup(body, "profile_url") or _lookup(body, "public_profile_url") or ""
    contact = _contact_payload(body)
    return {
        "candidate_key": _clean_text(key or name, 191),
        "name": _clean_text(name or key, 255),
        "headline": _clean_long_text(headline, 600),
        "company": _clean_text(company, 255),
        "contact": contact,
        "source_type": source_type,
        "source_reason": source_reason,
        "url": _clean_long_text(url, 1000),
        "evidence": [{"source_type": source_type, "title": _clean_text(name or key, 255), "description": _brief_text(body, 600)}],
        "raw": body,
    }


def _append_candidate_pool(row: CreativeGenerationJob, raw_items: list[Any], *, source_type: str, source_reason: str, limit: int = 80) -> int:
    candidates: list[dict[str, Any]] = []
    for raw in raw_items[:limit]:
        item = _candidate_from_raw(raw, source_type=source_type, source_reason=source_reason)
        if item:
            candidates.append(item)
    if not candidates:
        return 0
    meta = _meta(row)
    pool = meta.get("candidate_pool") if isinstance(meta.get("candidate_pool"), list) else []
    existing = {str(item.get("candidate_key") or item.get("name") or "") for item in pool if isinstance(item, dict)}
    added = 0
    for item in candidates:
        key = str(item.get("candidate_key") or item.get("name") or "")
        if key and key in existing:
            for old in pool:
                if str(old.get("candidate_key") or old.get("name") or "") == key:
                    old.setdefault("evidence", []).extend(item.get("evidence") or [])
                    break
            continue
        pool.append(item)
        if key:
            existing.add(key)
        added += 1
    meta["candidate_pool"] = pool[-300:]
    _set_meta(row, meta)
    return added


async def _run_query_step(
    *,
    db: Session,
    current_user: User,
    job: CreativeGenerationJob,
    step_key: str,
    query_type: str,
    params: dict[str, Any],
    meta: dict[str, Any],
    save_items: bool = True,
    attempts: int = 5,
) -> dict[str, Any]:
    result = await _execute_query_with_retry(
        db=db,
        current_user=current_user,
        query_type=query_type,
        params=params,
        body={},
        save_items=save_items,
        meta={**meta, "linkedin_job_id": job.job_id, "step_key": step_key},
        attempts=attempts,
        include_raw_response=True,
    )
    if query_type in {
        "linkedin_user_profile",
        "linkedin_user_posts",
        "linkedin_user_comments",
        "linkedin_user_reactions",
        "linkedin_user_recent_activity",
        "linkedin_discovery_user",
        "linkedin_company_employees",
        "linkedin_company_jobs",
        "linkedin_search_users",
        "linkedin_search_posts",
        "linkedin_hashtag_feed",
        "linkedin_post_comments",
        "linkedin_post_reactions",
    }:
        _append_candidate_pool(
            job,
            _raw_entries_from_query_result(result),
            source_type=query_type,
            source_reason=str(meta.get("source_reason") or meta.get("source") or query_type),
        )
    return result


def _update_progress(row: CreativeGenerationJob) -> None:
    meta = _meta(row)
    steps = meta.get("steps") if isinstance(meta.get("steps"), list) else []
    done = sum(1 for item in steps if item.get("status") in {"completed", "skipped"})
    total = max(1, len(steps))
    row.progress = min(99, int(done * 100 / total))
    if row.status not in _TERMINAL_STATUS:
        row.status = "running"
    row.updated_at = _utcnow()


def _next_pending_step(row: CreativeGenerationJob, requested: str = "") -> Optional[dict[str, Any]]:
    steps = (_meta(row).get("steps") if isinstance(_meta(row).get("steps"), list) else []) or []
    if requested:
        for item in steps:
            if item.get("key") == requested:
                return item
        raise HTTPException(status_code=404, detail="step not found")
    for item in steps:
        if item.get("status") not in {"completed", "skipped"}:
            return item
    return None


def _user_from_job(db: Session, row: CreativeGenerationJob) -> User:
    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return user


async def _execute_step(db: Session, row: CreativeGenerationJob, current_user: User, step_key: str) -> CreativeGenerationJob:
    req = row.request_payload or {}
    _mark_step(row, step_key, "running", detail="执行中")
    row.status = "running"
    db.commit()
    db.refresh(row)

    try:
        if step_key == "seed_profiles":
            outputs = []
            user_refs = _ensure_meta_map(row, "linkedin_user_refs")
            for username in req.get("seed_usernames") or []:
                profile = await _run_query_step(
                    db=db,
                    current_user=current_user,
                    job=row,
                    step_key=step_key,
                    query_type="linkedin_user_profile",
                    params={"username": username},
                    meta={"source": "seed_profile", "source_reason": f"种子用户 {username}"},
                )
                urn = _extract_linkedin_urn(_first_raw_entry(profile))
                if urn:
                    user_refs[username] = {"urn": urn}
                    _save_meta_map(row, "linkedin_user_refs", user_refs)
                outputs.append({"username": username, "query_type": "linkedin_user_profile", "ok": profile.get("ok"), "urn": urn, "query_id": (profile.get("query") or {}).get("query_id")})
                for query_type in ("linkedin_user_contact_info", "linkedin_user_follow_count"):
                    result = await _run_query_step(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type=query_type,
                        params={"username": username},
                        meta={"source": "seed_profile", "source_reason": f"种子用户 {username}"},
                    )
                    item = {"username": username, "query_type": query_type, "ok": result.get("ok"), "query_id": (result.get("query") or {}).get("query_id")}
                    if query_type == "linkedin_user_contact_info":
                        item["contact"] = _contact_payload(_first_raw_entry(result))
                    outputs.append(item)
                if urn:
                    for query_type in ("linkedin_user_experiences", "linkedin_user_skills", "linkedin_user_about"):
                        result = await _run_query_step(
                            db=db,
                            current_user=current_user,
                            job=row,
                            step_key=step_key,
                            query_type=query_type,
                            params={"urn": urn, "page": 1},
                            meta={"source": "seed_profile", "source_reason": f"种子用户 {username}"},
                        )
                        outputs.append({"username": username, "query_type": query_type, "ok": result.get("ok"), "query_id": (result.get("query") or {}).get("query_id")})
                else:
                    outputs.append({"username": username, "query_type": "urn_dependent_profile_queries", "ok": False, "skipped": True, "reason": "missing urn"})
            _append_output(row, step_key=step_key, title="种子用户画像", kind="profile", data=outputs)
            _mark_step(row, step_key, "completed", detail=f"已完成 {len(outputs)} 次画像查询", result={"queries": outputs})

        elif step_key == "seed_activity":
            outputs = []
            post_ids: list[str] = []
            user_refs = _ensure_meta_map(row, "linkedin_user_refs")
            for username in req.get("seed_usernames") or []:
                ref = user_refs.get(username) if isinstance(user_refs.get(username), dict) else {}
                urn = _clean_text(ref.get("urn") if isinstance(ref, dict) else "", 255)
                if not urn:
                    profile = await _run_query_step(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type="linkedin_user_profile",
                        params={"username": username},
                        meta={"source": "seed_activity", "source_reason": f"种子用户 {username}"},
                    )
                    urn = _extract_linkedin_urn(_first_raw_entry(profile))
                    if urn:
                        user_refs[username] = {"urn": urn}
                        _save_meta_map(row, "linkedin_user_refs", user_refs)
                if not urn:
                    outputs.append({"username": username, "ok": False, "skipped": True, "reason": "missing urn"})
                    continue
                for query_type in ("linkedin_user_posts", "linkedin_user_comments", "linkedin_user_reactions"):
                    result = await _run_query_step(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type=query_type,
                        params={"urn": urn, "page": 1},
                        meta={"source": "seed_activity", "source_reason": f"种子用户动态 {username}"},
                    )
                    raw = result.get("raw_response")
                    for item in _collect_raw_items(raw)[:6]:
                        post_id = _post_id(item)
                        if post_id and post_id not in post_ids:
                            post_ids.append(post_id)
                    outputs.append({"username": username, "query_type": query_type, "ok": result.get("ok"), "query_id": (result.get("query") or {}).get("query_id")})
            meta = _meta(row)
            meta["post_ids"] = post_ids[:12]
            _set_meta(row, meta)
            _append_output(row, step_key=step_key, title="种子用户动态", kind="activity", data={"queries": outputs, "post_ids": post_ids[:12]})
            _mark_step(row, step_key, "completed", detail=f"已同步动态，发现 {len(post_ids[:12])} 个可追踪帖子", result={"queries": outputs, "post_ids": post_ids[:12]})

        elif step_key == "discovery_users":
            outputs = [{"skipped": True, "reason": "TikHub old LinkedIn web API has no stable user discovery endpoint; use keyword/company/post interaction sources instead."}]
            _append_output(row, step_key=step_key, title="基于用户发现候选人", kind="candidates", data=outputs)
            _mark_step(row, step_key, "skipped", detail="老 LinkedIn web 接口无稳定推荐用户接口，已跳过", result={"queries": outputs})

        elif step_key == "company_profiles":
            outputs = []
            company_refs = _ensure_meta_map(row, "linkedin_company_refs")
            for company in req.get("seed_companies") or []:
                profile = await _run_query_step(
                    db=db,
                    current_user=current_user,
                    job=row,
                    step_key=step_key,
                    query_type="linkedin_company_profile",
                    params={"company": company},
                    meta={"source": "company_profile", "source_reason": f"种子公司 {company}"},
                )
                company_id = _extract_company_id(_first_raw_entry(profile))
                if company_id:
                    company_refs[company] = {"company_id": company_id}
                    _save_meta_map(row, "linkedin_company_refs", company_refs)
                outputs.append({"company": company, "query_type": "linkedin_company_profile", "ok": profile.get("ok"), "company_id": company_id, "query_id": (profile.get("query") or {}).get("query_id")})
                if not company_id:
                    outputs.append({"company": company, "query_type": "company_id_dependent_queries", "ok": False, "skipped": True, "reason": "missing company_id"})
                    continue
                for query_type in ("linkedin_company_posts", "linkedin_company_jobs"):
                    result = await _run_query_step(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type=query_type,
                        params={"company_id": company_id, "page": 1},
                        meta={"source": "company_profile", "source_reason": f"种子公司 {company}"},
                    )
                    outputs.append({"company": company, "query_type": query_type, "ok": result.get("ok"), "query_id": (result.get("query") or {}).get("query_id")})
            _append_output(row, step_key=step_key, title="公司画像", kind="company", data=outputs)
            _mark_step(row, step_key, "completed", detail=f"已完成 {len(outputs)} 次公司查询", result={"queries": outputs})

        elif step_key == "company_people":
            outputs = []
            company_refs = _ensure_meta_map(row, "linkedin_company_refs")
            for company in req.get("seed_companies") or []:
                ref = company_refs.get(company) if isinstance(company_refs.get(company), dict) else {}
                company_id = _clean_text(ref.get("company_id") if isinstance(ref, dict) else "", 255)
                if not company_id:
                    profile = await _run_query_step(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type="linkedin_company_profile",
                        params={"company": company},
                        meta={"source": "company_people", "source_reason": f"种子公司 {company}"},
                    )
                    company_id = _extract_company_id(_first_raw_entry(profile))
                    if company_id:
                        company_refs[company] = {"company_id": company_id}
                        _save_meta_map(row, "linkedin_company_refs", company_refs)
                if not company_id:
                    outputs.append({"company": company, "ok": False, "skipped": True, "reason": "missing company_id"})
                    continue
                result = await _run_query_step(
                    db=db,
                    current_user=current_user,
                    job=row,
                    step_key=step_key,
                    query_type="linkedin_company_employees",
                    params={"company_id": company_id, "page": 1},
                    meta={"source": "company_people", "source_reason": f"公司扩展 {company}"},
                )
                outputs.append({"company": company, "query_type": "linkedin_company_employees", "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": (result.get("query") or {}).get("query_id")})
            _append_output(row, step_key=step_key, title="公司员工与相似公司", kind="candidates", data=outputs)
            _mark_step(row, step_key, "completed", detail=f"已完成 {len(outputs)} 次公司扩展", result={"queries": outputs})

        elif step_key == "keyword_search":
            outputs = []
            for keyword in req.get("keywords") or []:
                result = await _run_query_step(
                    db=db,
                    current_user=current_user,
                    job=row,
                    step_key=step_key,
                    query_type="linkedin_search_users",
                    params={"name": keyword, "page": 1},
                    meta={"source": "keyword_search", "source_reason": f"关键词搜索 {keyword}"},
                )
                outputs.append({"keyword": keyword, "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": (result.get("query") or {}).get("query_id")})
            _append_output(row, step_key=step_key, title="关键词搜索用户", kind="candidates", data=outputs)
            _mark_step(row, step_key, "completed", detail=f"已同步 {len(outputs)} 个关键词", result={"queries": outputs})

        elif step_key == "hashtag_feed":
            outputs = []
            post_ids: list[str] = []
            for hashtag in req.get("hashtags") or []:
                tag = hashtag.lstrip("#")
                result = await _run_query_step(
                    db=db,
                    current_user=current_user,
                    job=row,
                    step_key=step_key,
                    query_type="linkedin_hashtag_feed",
                    params={"keyword": tag, "page": 1},
                    meta={"source": "hashtag_feed", "source_reason": f"话题 #{tag}"},
                )
                raw = result.get("raw_response")
                for item in _collect_raw_items(raw)[:8]:
                    post_id = _post_id(item)
                    if post_id and post_id not in post_ids:
                        post_ids.append(post_id)
                outputs.append({"hashtag": tag, "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": (result.get("query") or {}).get("query_id")})
            interaction_outputs = []
            limit = int(req.get("max_interactions_per_post") or 0)
            if limit > 0:
                for post_id in post_ids[:8]:
                    for query_type in ("linkedin_post_comments", "linkedin_post_reactions"):
                        result = await _run_query_step(
                            db=db,
                            current_user=current_user,
                            job=row,
                            step_key=step_key,
                            query_type=query_type,
                            params={"post_id": post_id, "page": 1},
                            meta={"source": "post_interaction", "source_reason": f"话题帖子互动 {post_id}"},
                        )
                        interaction_outputs.append({"post_id": post_id, "query_type": query_type, "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": (result.get("query") or {}).get("query_id")})
            data = {"hashtag_queries": outputs, "post_ids": post_ids[:8], "interaction_queries": interaction_outputs}
            _append_output(row, step_key=step_key, title="话题内容与互动人群", kind="interactions", data=data)
            _mark_step(row, step_key, "completed", detail=f"已同步 {len(outputs)} 个话题，追踪 {len(interaction_outputs)} 次互动", result=data)

        elif step_key == "score_candidates":
            rows = _query_rows_for_job(db, row.user_id, row.job_id)
            candidates = []
            pool = _meta(row).get("candidate_pool") if isinstance(_meta(row).get("candidate_pool"), list) else []
            for item in pool:
                if isinstance(item, dict):
                    candidates.append(item)
            for source_row in rows:
                if source_row.source_type in {"user_search", "company_employee", "company_job", "discovery_user", "post_comment", "post_reaction", "user_profile", "user_recent_activity", "user_reaction", "search_post", "hashtag_feed"}:
                    c = _normalize_candidate_from_row(source_row)
                    if c:
                        candidates.append(c)
            merged = _merge_candidates(candidates)[: int(req.get("max_people") or 30)]
            for idx, item in enumerate(merged, start=1):
                item["score"] = max(20, min(98, 45 + len(item.get("evidence") or []) * 12 + (8 if item.get("headline") else 0) + (8 if item.get("company") else 0)))
                item["rank"] = idx
            lead_summary = _lead_summary_payload(merged, rows)
            payload = {"total_source_rows": len(rows), "candidates": merged, "lead_summary": lead_summary}
            row.result_payload = {**(row.result_payload or {}), "candidates": merged, "lead_summary": lead_summary}
            _append_output(row, step_key=step_key, title="候选人归并评分", kind="candidates", data=payload)
            _mark_step(row, step_key, "completed", detail=f"已归并 {len(merged)} 个候选人", result={"candidate_count": len(merged)})

        elif step_key == "summary_report":
            report = await _generate_summary_report(row, current_user, db)
            row.result_payload = {**(row.result_payload or {}), "report": report}
            row.status = "completed"
            row.stage = "completed"
            row.progress = 100
            row.completed_at = _utcnow()
            _append_output(row, step_key=step_key, title="最终分析报告", kind="report", data=report)
            _mark_step(row, step_key, "completed", detail="已生成最终报告", result={"sections": list(report.keys())})

        else:
            raise HTTPException(status_code=400, detail=f"unsupported step: {step_key}")
    except Exception as exc:
        row.status = "failed"
        row.error = str(getattr(exc, "detail", None) or exc)[:4000]
        _mark_step(row, step_key, "failed", detail="执行失败，可重试", error=row.error)
        db.commit()
        raise

    if row.status not in _TERMINAL_STATUS:
        _update_progress(row)
        row.stage = "running"
    db.commit()
    db.refresh(row)
    return row


async def _generate_summary_report(row: CreativeGenerationJob, current_user: User, db: Session) -> dict[str, Any]:
    req = row.request_payload or {}
    candidates = (row.result_payload or {}).get("candidates") if isinstance(row.result_payload, dict) else []
    lead_summary = (row.result_payload or {}).get("lead_summary") if isinstance(row.result_payload, dict) else {}
    rows = _query_rows_for_job(db, row.user_id, row.job_id)
    source_briefs = []
    for source_row in rows[:80]:
        source_briefs.append(
            {
                "source_type": source_row.source_type,
                "title": source_row.title or "",
                "description": source_row.description or "",
                "author": source_row.author_name or "",
                "metrics": source_row.metrics or {},
            }
        )
    memories = _memory_payload_from_docs(req.get("memory_docs") or [], content_limit=1600)
    system_prompt = (
        "你是LinkedIn B2B公开线索挖掘分析师。根据TikHub已抓取的数据，输出给业务人员直接使用的中文报告。"
        "报告目标不是解释接口数据，而是帮助用户决定先跟进谁、为什么跟进、从哪个公开联系方式或公开主页开始。"
        "必须优先整理：可跟进线索列表、公开联系方式状态、推荐触达动作、判断依据和数据限制。"
        "下一步建议必须产品化、可执行，不能只写泛泛建议；要拆成名单分层、补资料任务、触达资产和观察任务。"
        "只基于输入数据做判断；不能声称已完成点赞、关注、私信、加好友或获取非公开联系方式。"
        "返回严格JSON，格式："
        "{\"executive_summary\":\"\",\"lead_overview\":{\"candidate_count\":0,\"with_public_contact\":0,\"recommendation\":\"\"},"
        "\"contact_list\":[{\"name\":\"\",\"role\":\"\",\"company\":\"\",\"contact\":\"\",\"source\":\"\",\"next_action\":\"\"}],"
        "\"priority_leads\":[{\"name\":\"\",\"company\":\"\",\"score\":0,\"why\":\"\",\"contact_status\":\"\",\"opening_line\":\"\",\"next_step\":\"\"}],"
        "\"candidate_segments\":[{\"name\":\"\",\"reason\":\"\",\"people\":[\"\"]}],"
        "\"action_workbench\":{\"list_a\":[{\"name\":\"\",\"reason\":\"\",\"next_action\":\"\"}],"
        "\"list_b\":[{\"name\":\"\",\"reason\":\"\",\"next_action\":\"\"}],"
        "\"watch_list\":[{\"name\":\"\",\"reason\":\"\",\"next_action\":\"\"}],"
        "\"supplement_tasks\":[{\"target\":\"\",\"missing\":\"\",\"how_to_fill\":\"\"}],"
        "\"outreach_assets\":[{\"name\":\"\",\"channel\":\"\",\"copy\":\"\"}]},"
        "\"relationship_map\":[{\"from\":\"\",\"to\":\"\",\"relation\":\"\",\"evidence\":\"\"}],"
        "\"next_actions\":[\"\"],\"limitations\":[\"\"]}"
    )
    user_prompt = json.dumps(
        {
            "user_inputs": {
                "title": row.title or "",
                "seed_usernames": req.get("seed_usernames") or [],
                "seed_companies": req.get("seed_companies") or [],
                "keywords": req.get("keywords") or [],
                "hashtags": req.get("hashtags") or [],
                "target_profile": req.get("target_profile") or "",
            },
            "memory_docs": memories,
            "source_briefs": source_briefs[:80],
            "candidates": candidates[:50] if isinstance(candidates, list) else [],
            "lead_summary": lead_summary,
            "requirements": "给出用户能直接使用的线索列表、联系方式状态、跟进优先级、关系路径、建联开场白和下一步执行建议。",
        },
        ensure_ascii=False,
    )
    token = create_access_token({"sub": str(current_user.id)})
    headers = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {token}"}
    payload = {
        "model": (os.environ.get("LINKEDIN_MINING_MODEL") or "deepseek-chat").strip() or "deepseek-chat",
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        "stream": False,
        "temperature": 0.35,
    }
    data = await _post_llm_with_retry(payload=payload, headers=headers, attempts=3)
    try:
        text = str(data["choices"][0]["message"]["content"] or "")
    except Exception:
        text = json.dumps(data, ensure_ascii=False)
    report = _extract_json_object(text)
    if not report:
        report = {
            "executive_summary": _clean_long_text(text, 4000),
            "target_profile": req.get("target_profile") or "",
            "candidate_segments": [],
            "priority_leads": [],
            "action_workbench": {
                "list_a": [],
                "list_b": [],
                "watch_list": [],
                "supplement_tasks": [],
                "outreach_assets": [],
            },
            "relationship_map": [],
            "next_actions": [],
            "limitations": ["LLM未返回结构化JSON，已保留原始摘要。"],
        }
    return _jsonable(report)


async def _auto_run_job(job_id: str, bearer_token: str = "") -> None:
    await asyncio.sleep(0.2)
    with SessionLocal() as db:
        row = (
            db.query(CreativeGenerationJob)
            .filter(CreativeGenerationJob.job_id == job_id, CreativeGenerationJob.feature_type == _FEATURE_TYPE)
            .first()
        )
        if row is None:
            return
        user = _user_from_job(db, row)
        while True:
            db.refresh(row)
            if row.status in _TERMINAL_STATUS:
                return
            step = _next_pending_step(row)
            if step is None:
                row.status = "completed"
                row.progress = 100
                row.completed_at = _utcnow()
                db.commit()
                return
            try:
                await _execute_step(db, row, user, str(step.get("key") or ""))
            except Exception:
                return


@router.post("/api/linkedin-mining/jobs", summary="启动LinkedIn线索挖掘任务")
async def start_linkedin_mining_job(
    body: LinkedInMiningStartBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    seed_usernames = []
    for value in body.seed_profile_urls:
        username = _extract_linkedin_username(value)
        if username and username not in seed_usernames:
            seed_usernames.append(username)
    seed_companies = []
    for value in body.seed_company_urls:
        company = _extract_linkedin_company(value)
        if company and company not in seed_companies:
            seed_companies.append(company)
    keywords = _clean_list(body.keywords, limit=12)
    hashtags = [x.lstrip("#") for x in _clean_list(body.hashtags, limit=12) if x.lstrip("#")]
    if not seed_usernames and not seed_companies and not keywords and not hashtags:
        raise HTTPException(status_code=400, detail="请至少输入一个LinkedIn个人主页、公司主页、关键词或话题")

    job_id = "li_" + uuid.uuid4().hex[:24]
    title = _clean_text(body.title, 160) or "LinkedIn线索挖掘"
    request_payload = {
        "seed_usernames": seed_usernames[:10],
        "seed_companies": seed_companies[:10],
        "keywords": keywords,
        "hashtags": hashtags,
        "target_profile": _clean_long_text(body.target_profile, 2000),
        "memory_docs": body.memory_docs[:12],
        "max_people": int(body.max_people or 30),
        "max_company_employees": int(body.max_company_employees or 20),
        "max_interactions_per_post": int(body.max_interactions_per_post or 20),
    }
    row = CreativeGenerationJob(
        job_id=job_id,
        user_id=current_user.id,
        feature_type=_FEATURE_TYPE,
        provider="tikhub",
        status="queued",
        stage="queued",
        progress=0,
        title=title,
        prompt=_clean_long_text(body.target_profile, 4000) or None,
        request_payload=request_payload,
        result_payload={},
        meta={"steps": _initial_steps(request_payload), "outputs": [], "current_step": ""},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if body.auto_run:
        token = request.headers.get("Authorization") or request.headers.get("authorization") or ""
        asyncio.create_task(_auto_run_job(job_id, token))
    return {"ok": True, "job": _job_payload(row)}


@router.get("/api/linkedin-mining/jobs", summary="LinkedIn线索挖掘任务列表")
def list_linkedin_mining_jobs(
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(CreativeGenerationJob).filter(
        CreativeGenerationJob.user_id == current_user.id,
        CreativeGenerationJob.feature_type == _FEATURE_TYPE,
        CreativeGenerationJob.deleted_at.is_(None),
    )
    total = q.count()
    rows = q.order_by(CreativeGenerationJob.created_at.desc(), CreativeGenerationJob.id.desc()).offset(offset).limit(limit).all()
    return {"ok": True, "total": total, "items": [_job_payload(row) for row in rows]}


@router.get("/api/linkedin-mining/jobs/{job_id}", summary="LinkedIn线索挖掘任务详情")
def get_linkedin_mining_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(CreativeGenerationJob)
        .filter(
            CreativeGenerationJob.user_id == current_user.id,
            CreativeGenerationJob.feature_type == _FEATURE_TYPE,
            CreativeGenerationJob.job_id == job_id.strip().lower(),
            CreativeGenerationJob.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "job": _job_payload(row)}


@router.post("/api/linkedin-mining/jobs/{job_id}/run-next", summary="继续执行LinkedIn线索挖掘任务")
async def run_next_linkedin_mining_step(
    job_id: str,
    body: LinkedInMiningStepBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(CreativeGenerationJob)
        .filter(
            CreativeGenerationJob.user_id == current_user.id,
            CreativeGenerationJob.feature_type == _FEATURE_TYPE,
            CreativeGenerationJob.job_id == job_id.strip().lower(),
            CreativeGenerationJob.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if row.status in {"completed", "canceled", "stale"} and not body.step_key:
        return {"ok": True, "job": _job_payload(row)}
    if row.status == "failed" and not body.step_key:
        row.status = "running"
        row.error = None
    step = _next_pending_step(row, _clean_text(body.step_key, 80))
    if step is None:
        row.status = "completed"
        row.progress = 100
        row.completed_at = _utcnow()
        db.commit()
        db.refresh(row)
        return {"ok": True, "job": _job_payload(row)}
    row = await _execute_step(db, row, current_user, str(step.get("key") or ""))
    return {"ok": True, "job": _job_payload(row)}


@router.post("/api/linkedin-mining/jobs/{job_id}/resume", summary="自动续跑LinkedIn线索挖掘任务")
async def resume_linkedin_mining_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(CreativeGenerationJob)
        .filter(
            CreativeGenerationJob.user_id == current_user.id,
            CreativeGenerationJob.feature_type == _FEATURE_TYPE,
            CreativeGenerationJob.job_id == job_id.strip().lower(),
            CreativeGenerationJob.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if row.status == "failed":
        row.status = "running"
        row.error = None
        db.commit()
    asyncio.create_task(_auto_run_job(row.job_id))
    return {"ok": True, "job": _job_payload(row)}


@router.get("/api/linkedin-mining/jobs/{job_id}/outputs/{output_id}", summary="LinkedIn线索挖掘输出详情")
def get_linkedin_mining_output(
    job_id: str,
    output_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(CreativeGenerationJob)
        .filter(
            CreativeGenerationJob.user_id == current_user.id,
            CreativeGenerationJob.feature_type == _FEATURE_TYPE,
            CreativeGenerationJob.job_id == job_id.strip().lower(),
            CreativeGenerationJob.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    outputs = (_meta(row).get("outputs") if isinstance(_meta(row).get("outputs"), list) else []) or []
    for item in outputs:
        if str(item.get("id") or "") == output_id:
            return {"ok": True, "output": item}
    raise HTTPException(status_code=404, detail="输出不存在")
