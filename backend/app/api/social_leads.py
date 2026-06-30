from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import CreativeGenerationJob, TikHubSourceItem, User
from .auth import get_current_user
from .ip_content_studio import (
    _clean_long_text,
    _clean_text,
    _execute_query_with_retry,
    _first,
    _jsonable,
    _lookup,
    _stable_hash,
    _utcnow,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_SUPPORTED_PLATFORMS = {"reddit", "x"}
_FEATURE_BY_PLATFORM = {"reddit": "reddit_leads", "x": "x_leads"}
_TERMINAL_STATUS = {"completed", "failed", "canceled", "stale"}
_AUTORUN_IDLE_SECONDS = 8.0


class SocialLeadsStartBody(BaseModel):
    platform: str = Field("", max_length=24)
    title: str = Field("", max_length=160)
    keywords: list[str] = Field(default_factory=list)
    accounts: list[str] = Field(default_factory=list)
    post_ids: list[str] = Field(default_factory=list)
    communities: list[str] = Field(default_factory=list)
    country: str = Field("", max_length=80)
    search_type: str = Field("", max_length=40)
    sort: str = Field("", max_length=40)
    time_range: str = Field("", max_length=40)
    max_items: int = Field(30, ge=1, le=100)
    include_comments: bool = True
    include_account_posts: bool = True
    auto_run: bool = True


class SocialLeadsStepBody(BaseModel):
    step_key: str = ""


def _platform(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"twitter", "x_leads", "twitter_x"}:
        raw = "x"
    if raw in {"reddit", "x"}:
        return raw
    raise HTTPException(status_code=400, detail="platform must be reddit or x")


def _feature_type(platform: str) -> str:
    return _FEATURE_BY_PLATFORM[_platform(platform)]


def _job_payload(row: CreativeGenerationJob, *, db: Optional[Session] = None, include_sources: bool = False) -> dict[str, Any]:
    meta = dict(row.meta or {})
    payload = {
        "job_id": row.job_id,
        "platform": (row.request_payload or {}).get("platform") or meta.get("platform") or "",
        "status": row.status,
        "stage": row.stage or "",
        "progress": row.progress or 0,
        "title": row.title or "",
        "request_payload": row.request_payload or {},
        "result_payload": row.result_payload or {},
        "error": row.error or "",
        "meta": meta,
        "current_step": str(meta.get("current_step") or ""),
        "needs_resume": _needs_autorun_resume(row),
        "steps": meta.get("steps") if isinstance(meta.get("steps"), list) else [],
        "outputs": meta.get("outputs") if isinstance(meta.get("outputs"), list) else [],
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "completed_at": row.completed_at.isoformat() if row.completed_at else "",
    }
    if include_sources and db is not None:
        platform = _platform(payload["platform"] or (row.request_payload or {}).get("platform"))
        rows = _rows_for_job(db, row.user_id, platform, row.job_id)
        payload["source_summary"] = _source_summary(rows)
        payload["source_items"] = [_source_item_payload(item) for item in rows[:500]]
    return payload


def _step(label: str, key: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": "pending",
        "detail": "",
        "result": {},
        "started_at": "",
        "finished_at": "",
        "attempts": 0,
        "error": "",
    }


def _initial_steps(req: dict[str, Any]) -> list[dict[str, Any]]:
    platform = _platform(req.get("platform"))
    steps: list[dict[str, Any]] = []
    if platform == "reddit":
        if req.get("keywords"):
            steps.append(_step("关键词搜索", "keyword_search"))
        if req.get("communities"):
            steps.append(_step("社区帖子采集", "community_feed"))
        if req.get("accounts"):
            steps.append(_step("账号公开资料", "account_profiles"))
            if req.get("include_account_posts"):
                steps.append(_step("账号发帖/评论", "account_activity"))
        if req.get("post_ids") and req.get("include_comments"):
            steps.append(_step("帖子评论采集", "post_comments"))
    else:
        if req.get("country"):
            steps.append(_step("趋势采集", "trending"))
        if req.get("keywords"):
            steps.append(_step("关键词搜索", "keyword_search"))
        if req.get("accounts"):
            steps.append(_step("账号公开资料", "account_profiles"))
            if req.get("include_account_posts"):
                steps.append(_step("账号发帖", "account_activity"))
        if req.get("post_ids") and req.get("include_comments"):
            steps.append(_step("推文评论采集", "post_comments"))
    steps.append(_step("线索归并", "merge_leads"))
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
    meta["current_step"] = key if status == "running" else ""
    _set_meta(row, meta)


def _append_output(row: CreativeGenerationJob, *, step_key: str, title: str, kind: str, data: Any) -> None:
    meta = _meta(row)
    outputs = meta.get("outputs") if isinstance(meta.get("outputs"), list) else []
    output_id = f"{step_key}_{_stable_hash({'title': title, 'data': data}, 16)}"
    if output_id not in {str(x.get("id") or "") for x in outputs if isinstance(x, dict)}:
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


def _clean_list(values: Any, *, limit: int = 20, max_len: int = 160) -> list[str]:
    out: list[str] = []
    raw = values if isinstance(values, list) else []
    for item in raw:
        text = _clean_text(item, max_len).strip().strip("/")
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _extract_reddit_username(value: Any) -> str:
    text = _clean_text(value, 255).strip().strip("/")
    if not text:
        return ""
    match = re.search(r"reddit\.com/(?:user|u)/([^/?#]+)", text, re.I)
    if match:
        return _clean_text(match.group(1), 120)
    return _clean_text(re.sub(r"^u/", "", text, flags=re.I).lstrip("@"), 120)


def _looks_like_reddit_subreddit(value: Any) -> bool:
    text = _clean_text(value, 255).strip().strip("/")
    if not text:
        return False
    return bool(re.search(r"(?:^|/)r/[^/?#]+", text, re.I) or re.search(r"reddit\.com/r/[^/?#]+", text, re.I))


def _extract_subreddit(value: Any) -> str:
    text = _clean_text(value, 255).strip().strip("/")
    if not text:
        return ""
    match = re.search(r"reddit\.com/r/([^/?#]+)", text, re.I)
    if match:
        return _clean_text(match.group(1), 120)
    return _clean_text(re.sub(r"^r/", "", text, flags=re.I), 120)


def _split_reddit_accounts_and_communities(accounts: list[Any], communities: list[Any]) -> tuple[list[str], list[str]]:
    account_out: list[str] = []
    community_out: list[str] = []
    for item in communities:
        name = _extract_subreddit(item)
        if name and name not in community_out:
            community_out.append(name)
    for item in accounts:
        if _looks_like_reddit_subreddit(item):
            name = _extract_subreddit(item)
            if name and name not in community_out:
                community_out.append(name)
            continue
        username = _extract_reddit_username(item)
        if username and username not in account_out:
            account_out.append(username)
    return account_out, community_out


def _extract_x_screen_name(value: Any) -> str:
    text = _clean_text(value, 255).strip().strip("/")
    if not text:
        return ""
    match = re.search(r"(?:x|twitter)\.com/([^/?#]+)", text, re.I)
    if match:
        text = match.group(1)
    return _clean_text(text.lstrip("@"), 120)


def _normalize_reddit_post_id(value: Any) -> str:
    text = _clean_text(value, 255).strip()
    if not text:
        return ""
    match = re.search(r"comments/([A-Za-z0-9_]+)", text, re.I)
    if match:
        text = match.group(1)
    if text.startswith("t3_"):
        return text
    return "t3_" + text


def _normalize_x_tweet_id(value: Any) -> str:
    text = _clean_text(value, 255).strip()
    if not text:
        return ""
    match = re.search(r"/status(?:es)?/(\d+)", text, re.I)
    if match:
        return match.group(1)
    match = re.search(r"\d{5,}", text)
    return match.group(0) if match else text


def _collect_raw_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in (
        "items",
        "results",
        "data",
        "children",
        "posts",
        "comments",
        "users",
        "communities",
        "tweets",
        "entries",
        "timeline",
        "instructions",
        "trends",
        "list",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _collect_raw_items(value)
            if nested:
                return nested
    for value in payload.values():
        if isinstance(value, (dict, list)):
            nested = _collect_raw_items(value)
            if nested:
                return nested
    return []


def _raw_entries(result: dict[str, Any]) -> list[Any]:
    raw = result.get("raw_response")
    items = _collect_raw_items(raw)
    if items:
        return items
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, dict):
            nested = _collect_raw_items(data)
            return nested or [data]
        return [raw]
    return []


def _first_raw(result: dict[str, Any]) -> dict[str, Any]:
    for item in _raw_entries(result):
        if isinstance(item, dict):
            return item
    return {}


def _row_meta(row: TikHubSourceItem) -> dict[str, Any]:
    raw = row.raw if isinstance(row.raw, dict) else {}
    meta = raw.get("__lobster_ip_content_meta")
    return meta if isinstance(meta, dict) else {}


def _raw_body(row: TikHubSourceItem) -> dict[str, Any]:
    body = row.raw if isinstance(row.raw, dict) else {}
    raw = body.get("raw") if isinstance(body.get("raw"), dict) else body
    return raw if isinstance(raw, dict) else {}


def _rows_for_job(db: Session, user_id: int, platform: str, job_id: str) -> list[TikHubSourceItem]:
    rows = (
        db.query(TikHubSourceItem)
        .filter(TikHubSourceItem.user_id == user_id, TikHubSourceItem.platform == platform)
        .order_by(TikHubSourceItem.created_at.desc(), TikHubSourceItem.id.desc())
        .limit(800)
        .all()
    )
    out: list[TikHubSourceItem] = []
    for row in rows:
        if str(_row_meta(row).get("social_leads_job_id") or "") == job_id:
            out.append(row)
    return out


def _source_summary(rows: list[TikHubSourceItem]) -> dict[str, Any]:
    by_step: dict[str, dict[str, Any]] = {}
    by_type: dict[str, int] = {}
    for row in rows:
        meta = _row_meta(row)
        step_key = str(meta.get("step_key") or row.source_type or "unknown")
        step = by_step.setdefault(step_key, {"step_key": step_key, "count": 0, "types": {}, "reasons": []})
        step["count"] += 1
        step["types"][row.source_type] = int(step["types"].get(row.source_type) or 0) + 1
        reason = _clean_text(meta.get("source_reason") or row.source_type, 160)
        if reason and reason not in step["reasons"]:
            step["reasons"].append(reason)
        by_type[row.source_type] = by_type.get(row.source_type, 0) + 1
    return {"total": len(rows), "by_type": by_type, "by_step": list(by_step.values())}


def _source_item_payload(row: TikHubSourceItem) -> dict[str, Any]:
    meta = _row_meta(row)
    raw = _raw_body(row)
    title = row.title or _first(raw, ["title", "name", "display_name", "subreddit_name_prefixed", "full_text", "text", "body"]) or ""
    description = row.description or _first(raw, ["public_description", "description", "selftext", "body", "full_text", "text"]) or ""
    handle = row.author_key or _first(raw, ["author", "username", "screen_name", "legacy.screen_name"]) or ""
    display_name = row.author_name or _first(raw, ["display_name", "name", "legacy.name"]) or handle
    url = row.public_url or _first(raw, ["permalink", "url", "tweet_url", "twitter_url"]) or ""
    if isinstance(url, str) and url.startswith("/"):
        url = "https://www.reddit.com" + url
    metrics = row.metrics if isinstance(row.metrics, dict) else {}
    raw_preview: dict[str, Any] = {}
    for key in ("author", "username", "name", "display_name", "title", "subreddit", "subreddit_name_prefixed", "public_description", "description", "selftext", "body", "full_text", "text", "permalink", "url", "score", "ups", "num_comments", "total_karma", "subscribers", "followers_count", "created_utc", "created_at"):
        value = _lookup(raw, key)
        if value not in (None, "", [], {}):
            raw_preview[key] = _jsonable(value)
    return {
        "id": row.id,
        "platform": row.platform,
        "source_type": row.source_type,
        "step_key": str(meta.get("step_key") or ""),
        "source_reason": str(meta.get("source_reason") or row.source_type),
        "item_key": row.item_key or "",
        "handle": _clean_text(handle, 255),
        "display_name": _clean_text(display_name, 255),
        "title": _clean_long_text(title, 1000),
        "description": _clean_long_text(description, 2000),
        "url": _clean_long_text(url, 1000),
        "metrics": _jsonable(metrics),
        "raw_preview": raw_preview,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


def _candidate_from_raw(raw: Any, platform: str, source_type: str, source_reason: str) -> Optional[dict[str, Any]]:
    body = raw if isinstance(raw, dict) else {"value": raw}
    if not body or all(v in (None, "", [], {}) for v in body.values()):
        return None
    user = _lookup(body, "user") if isinstance(_lookup(body, "user"), dict) else {}
    legacy = _lookup(body, "legacy") if isinstance(_lookup(body, "legacy"), dict) else {}
    core_user = _lookup(body, "core.user_results.result.legacy")
    if not isinstance(core_user, dict):
        core_user = {}
    if platform == "reddit":
        username = (
            _lookup(body, "author")
            or _lookup(body, "username")
            or _lookup(user, "username")
            or (_lookup(body, "name") if source_type in {"user_profile", "user_comment", "post_comment"} else "")
            or _lookup(body, "display_name")
        )
        name = username or _lookup(body, "display_name") or _lookup(body, "title") or _lookup(body, "subreddit_name_prefixed") or _lookup(body, "subreddit")
        url = _lookup(body, "permalink") or _lookup(body, "url")
        if isinstance(url, str) and url.startswith("/"):
            url = "https://www.reddit.com" + url
        bio = _lookup(body, "public_description") or _lookup(body, "description") or _lookup(body, "body") or _lookup(body, "selftext") or _lookup(body, "title")
        metrics = {
            "score": _lookup(body, "score"),
            "ups": _lookup(body, "ups"),
            "comments": _lookup(body, "num_comments"),
            "karma": _lookup(body, "total_karma") or _lookup(body, "post_karma") or _lookup(body, "comment_karma"),
        }
    else:
        username = (
            _lookup(body, "screen_name")
            or _lookup(legacy, "screen_name")
            or _lookup(core_user, "screen_name")
            or _lookup(user, "screen_name")
            or _lookup(body, "rest_id")
        )
        name = _lookup(body, "name") or _lookup(legacy, "name") or _lookup(core_user, "name") or username
        url = f"https://x.com/{username}" if username and not str(username).isdigit() else ""
        bio = _lookup(body, "full_text") or _lookup(legacy, "full_text") or _lookup(body, "description") or _lookup(legacy, "description")
        metrics = {
            "followers": _lookup(legacy, "followers_count") or _lookup(core_user, "followers_count"),
            "friends": _lookup(legacy, "friends_count") or _lookup(core_user, "friends_count"),
            "likes": _lookup(legacy, "favorite_count") or _lookup(body, "favorite_count"),
            "replies": _lookup(legacy, "reply_count") or _lookup(body, "reply_count"),
            "retweets": _lookup(legacy, "retweet_count") or _lookup(body, "retweet_count"),
        }
    key = _clean_text(username or name, 191)
    if not key:
        return None
    return {
        "candidate_key": key,
        "name": _clean_text(name or key, 255),
        "handle": _clean_text(username, 255),
        "platform": platform,
        "source_type": source_type,
        "source_reason": source_reason,
        "bio": _clean_long_text(bio, 1200),
        "url": _clean_long_text(url, 1000),
        "metrics": {k: v for k, v in metrics.items() if v not in (None, "", [], {})},
        "evidence": [{"source_type": source_type, "title": _clean_text(_lookup(body, "title") or _lookup(body, "full_text") or "", 255), "description": _clean_long_text(bio, 600)}],
        "raw": body,
    }


def _candidate_from_row(row: TikHubSourceItem) -> Optional[dict[str, Any]]:
    body = row.raw if isinstance(row.raw, dict) else {}
    meta = _row_meta(row)
    raw_body = _raw_body(row)
    candidate = _candidate_from_raw(raw_body, row.platform, row.source_type, str(meta.get("source_reason") or row.source_type))
    if not candidate:
        key = row.author_key or row.item_key
        name = row.author_name or row.title or key
        if not key and not name:
            return None
        if not (row.author_key or row.author_name or row.title or row.description or row.public_url):
            return None
        candidate = {
            "candidate_key": _clean_text(key, 191),
            "name": _clean_text(name, 255),
            "handle": _clean_text(row.author_key, 255),
            "platform": row.platform,
            "source_type": row.source_type,
            "source_reason": str(meta.get("source_reason") or row.source_type),
            "bio": _clean_long_text(row.description or row.title or "", 1200),
            "url": row.public_url or "",
            "metrics": row.metrics or {},
            "evidence": [],
            "raw": raw_body,
        }
    candidate.setdefault("evidence", []).append(
        {
            "source_item_id": row.id,
            "source_type": row.source_type,
            "title": row.title or "",
            "description": row.description or "",
            "url": row.public_url or "",
            "created_at": row.created_at.isoformat() if row.created_at else "",
        }
    )
    if not candidate.get("url") and row.public_url:
        candidate["url"] = row.public_url
    return candidate


def _merge_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in candidates:
        key = str(item.get("candidate_key") or item.get("handle") or item.get("name") or _stable_hash(item, 16))
        if key not in merged:
            merged[key] = {**item, "evidence": list(item.get("evidence") or [])}
            continue
        cur = merged[key]
        for field in ("name", "handle", "bio", "url"):
            if not cur.get(field) and item.get(field):
                cur[field] = item[field]
        cur_metrics = cur.setdefault("metrics", {})
        for k, v in (item.get("metrics") or {}).items():
            if v not in (None, "", [], {}) and not cur_metrics.get(k):
                cur_metrics[k] = v
        existing = {str(x.get("source_item_id") or x.get("title") or "") for x in cur.get("evidence") or [] if isinstance(x, dict)}
        for ev in item.get("evidence") or []:
            marker = str(ev.get("source_item_id") or ev.get("title") or "")
            if marker and marker not in existing:
                cur.setdefault("evidence", []).append(ev)
                existing.add(marker)
    out = list(merged.values())
    out.sort(key=lambda x: (len(x.get("evidence") or []), sum(1 for v in (x.get("metrics") or {}).values() if v)), reverse=True)
    for idx, item in enumerate(out, start=1):
        item["rank"] = idx
        item["score"] = max(10, min(99, 35 + len(item.get("evidence") or []) * 10 + sum(1 for v in (item.get("metrics") or {}).values() if v) * 4))
    return out


async def _run_query(
    *,
    db: Session,
    current_user: User,
    job: CreativeGenerationJob,
    step_key: str,
    query_type: str,
    params: dict[str, Any],
    source_reason: str,
    save_items: bool = True,
) -> dict[str, Any]:
    return await _execute_query_with_retry(
        db=db,
        current_user=current_user,
        query_type=query_type,
        params=params,
        body={},
        save_items=save_items,
        meta={
            "source": "social_leads",
            "source_reason": source_reason,
            "social_leads_job_id": job.job_id,
            "step_key": step_key,
        },
        attempts=3,
        include_raw_response=True,
    )


def _update_progress(row: CreativeGenerationJob) -> None:
    steps = (_meta(row).get("steps") if isinstance(_meta(row).get("steps"), list) else []) or []
    done = sum(1 for item in steps if item.get("status") in {"completed", "skipped"})
    row.progress = min(99, int(done * 100 / max(1, len(steps))))
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


def _has_unfinished_step(row: CreativeGenerationJob) -> bool:
    return _next_pending_step(row) is not None


def _has_running_step(row: CreativeGenerationJob) -> bool:
    steps = (_meta(row).get("steps") if isinstance(_meta(row).get("steps"), list) else []) or []
    return any(str(item.get("status") or "") == "running" for item in steps)


def _job_idle_seconds(row: CreativeGenerationJob) -> float:
    updated_at = row.updated_at or row.created_at
    if not updated_at:
        return 0.0
    try:
        return max(0.0, (_utcnow() - updated_at).total_seconds())
    except Exception:
        return 0.0


def _needs_autorun_resume(row: CreativeGenerationJob, *, idle_seconds: float = _AUTORUN_IDLE_SECONDS) -> bool:
    if row.status in _TERMINAL_STATUS:
        return False
    if not _has_unfinished_step(row):
        return False
    meta = _meta(row)
    if str(meta.get("current_step") or "").strip():
        return False
    if _has_running_step(row):
        return False
    if row.status == "queued":
        return _job_idle_seconds(row) >= idle_seconds
    if row.status == "running":
        return _job_idle_seconds(row) >= idle_seconds
    return False


def _user_from_job(db: Session, row: CreativeGenerationJob) -> User:
    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return user


def _extract_post_ids_from_rows(rows: list[TikHubSourceItem], platform: str, limit: int) -> list[str]:
    out: list[str] = []
    for row in rows:
        if row.source_type not in {"search_result", "subreddit_post", "user_post", "trend"}:
            continue
        raw = row.raw if isinstance(row.raw, dict) else {}
        if platform == "reddit":
            value = _first(raw, ["id", "post_id", "name", "permalink", "url"])
            post_id = _normalize_reddit_post_id(value)
        else:
            value = _first(raw, ["tweet_id", "id_str", "rest_id", "legacy.id_str", "url", "tweet_url"])
            post_id = _normalize_x_tweet_id(value)
        if post_id and post_id not in out:
            out.append(post_id)
        if len(out) >= limit:
            break
    return out


async def _execute_step(db: Session, row: CreativeGenerationJob, current_user: User, step_key: str) -> CreativeGenerationJob:
    req = row.request_payload or {}
    platform = _platform(req.get("platform"))
    _mark_step(row, step_key, "running", detail="执行中")
    row.status = "running"
    row.stage = "running"
    db.commit()
    db.refresh(row)
    outputs: list[dict[str, Any]] = []
    try:
        if step_key == "trending" and platform == "x":
            result = await _run_query(
                db=db,
                current_user=current_user,
                job=row,
                step_key=step_key,
                query_type="x_trending",
                params={"country": req.get("country") or "UnitedStates"},
                source_reason=f"X趋势 {req.get('country') or 'UnitedStates'}",
            )
            outputs.append({"query_type": "x_trending", "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": (result.get("query") or {}).get("query_id")})

        elif step_key == "keyword_search":
            for keyword in req.get("keywords") or []:
                if platform == "reddit":
                    result = await _run_query(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type="reddit_search",
                        params={
                            "query": keyword,
                            "search_type": req.get("search_type") or "post",
                            "sort": req.get("sort") or "RELEVANCE",
                            "time_range": req.get("time_range") or "month",
                            "safe_search": "strict",
                            "allow_nsfw": "0",
                            "need_format": False,
                        },
                        source_reason=f"Reddit关键词 {keyword}",
                    )
                else:
                    result = await _run_query(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type="x_search",
                        params={"keyword": keyword, "search_type": req.get("search_type") or "Top"},
                        source_reason=f"X关键词 {keyword}",
                    )
                outputs.append({"keyword": keyword, "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": (result.get("query") or {}).get("query_id")})

        elif step_key == "community_feed" and platform == "reddit":
            for community in req.get("communities") or []:
                result = await _run_query(
                    db=db,
                    current_user=current_user,
                    job=row,
                    step_key=step_key,
                    query_type="reddit_subreddit_feed",
                    params={"subreddit_name": community, "sort": req.get("sort") or "HOT", "need_format": False},
                    source_reason=f"Reddit社区 r/{community}",
                )
                outputs.append({"community": community, "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": (result.get("query") or {}).get("query_id")})

        elif step_key == "account_profiles":
            for account in req.get("accounts") or []:
                if platform == "reddit":
                    result = await _run_query(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type="reddit_user_profile",
                        params={"username": account, "need_format": False},
                        source_reason=f"Reddit账号 u/{account}",
                    )
                else:
                    result = await _run_query(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type="x_user_profile",
                        params={"screen_name": account},
                        source_reason=f"X账号 @{account}",
                    )
                candidate = _candidate_from_raw(_first_raw(result), platform, "user_profile", f"账号 {account}")
                outputs.append({"account": account, "ok": result.get("ok"), "candidate": candidate, "query_id": (result.get("query") or {}).get("query_id")})

        elif step_key == "account_activity":
            for account in req.get("accounts") or []:
                if platform == "reddit":
                    for query_type, params in (
                        ("reddit_user_posts", {"username": account, "sort": "NEW", "need_format": False}),
                        ("reddit_user_comments", {"username": account, "sort": "NEW", "page_size": min(25, int(req.get("max_items") or 30)), "need_format": False}),
                    ):
                        result = await _run_query(
                            db=db,
                            current_user=current_user,
                            job=row,
                            step_key=step_key,
                            query_type=query_type,
                            params=params,
                            source_reason=f"Reddit账号动态 u/{account}",
                        )
                        outputs.append({"account": account, "query_type": query_type, "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": (result.get("query") or {}).get("query_id")})
                else:
                    result = await _run_query(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type="x_user_posts",
                        params={"screen_name": account},
                        source_reason=f"X账号发帖 @{account}",
                    )
                    outputs.append({"account": account, "query_type": "x_user_posts", "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": (result.get("query") or {}).get("query_id")})

        elif step_key == "post_comments":
            explicit_ids = req.get("post_ids") or []
            post_ids = list(explicit_ids)
            if len(post_ids) < int(req.get("max_items") or 30):
                rows = _rows_for_job(db, row.user_id, platform, row.job_id)
                for post_id in _extract_post_ids_from_rows(rows, platform, int(req.get("max_items") or 30)):
                    if post_id not in post_ids:
                        post_ids.append(post_id)
            for post_id in post_ids[: int(req.get("max_items") or 30)]:
                if platform == "reddit":
                    result = await _run_query(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type="reddit_post_comments",
                        params={"post_id": _normalize_reddit_post_id(post_id), "sort_type": "CONFIDENCE", "need_format": False},
                        source_reason=f"Reddit帖子评论 {post_id}",
                    )
                else:
                    result = await _run_query(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type="x_post_comments",
                        params={"tweet_id": _normalize_x_tweet_id(post_id)},
                        source_reason=f"X推文评论 {post_id}",
                    )
                outputs.append({"post_id": post_id, "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": (result.get("query") or {}).get("query_id")})

        elif step_key == "merge_leads":
            rows = _rows_for_job(db, row.user_id, platform, row.job_id)
            candidates = []
            for source_row in rows:
                candidate = _candidate_from_row(source_row)
                if candidate:
                    candidates.append(candidate)
            merged = _merge_candidates(candidates)[: int(req.get("max_items") or 30)]
            payload = {
                "platform": platform,
                "total_source_rows": len(rows),
                "candidate_count": len(merged),
                "source_counts": _source_counts(rows),
                "candidates": merged,
            }
            row.result_payload = {**(row.result_payload or {}), "lead_summary": payload, "candidates": merged}
            _append_output(row, step_key=step_key, title="线索归并", kind="leads", data=payload)
            _mark_step(row, step_key, "completed", detail=f"归并 {len(merged)} 条线索", result={"candidate_count": len(merged), "source_rows": len(rows)})
            row.status = "completed"
            row.stage = "completed"
            row.progress = 100
            row.completed_at = _utcnow()
            db.commit()
            db.refresh(row)
            return row
        else:
            raise HTTPException(status_code=400, detail=f"unsupported step: {step_key}")
    except Exception as exc:
        row.status = "failed"
        row.error = str(getattr(exc, "detail", None) or exc)[:4000]
        _mark_step(row, step_key, "failed", detail="执行失败，可重试", error=row.error)
        db.commit()
        raise

    _append_output(row, step_key=step_key, title=_step_title(row, step_key), kind="queries", data={"queries": outputs})
    _mark_step(row, step_key, "completed", detail=f"完成 {len(outputs)} 次采集", result={"queries": outputs})
    if row.status not in _TERMINAL_STATUS:
        _update_progress(row)
    db.commit()
    db.refresh(row)
    return row


def _source_counts(rows: list[TikHubSourceItem]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[row.source_type] = out.get(row.source_type, 0) + 1
    return out


def _step_title(row: CreativeGenerationJob, key: str) -> str:
    for item in (_meta(row).get("steps") if isinstance(_meta(row).get("steps"), list) else []) or []:
        if item.get("key") == key:
            return str(item.get("label") or key)
    return key


async def _auto_run_job(job_id: str) -> None:
    await asyncio.sleep(0.2)
    with SessionLocal() as db:
        row = db.query(CreativeGenerationJob).filter(CreativeGenerationJob.job_id == job_id).first()
        if row is None:
            return
        user = _user_from_job(db, row)
        logger.info("[social_leads] auto_run start job_id=%s user_id=%s status=%s", row.job_id, row.user_id, row.status)
        while True:
            db.refresh(row)
            if row.status in _TERMINAL_STATUS:
                logger.info("[social_leads] auto_run stop terminal job_id=%s status=%s", row.job_id, row.status)
                return
            step = _next_pending_step(row)
            if step is None:
                row.status = "completed"
                row.progress = 100
                row.completed_at = _utcnow()
                db.commit()
                logger.info("[social_leads] auto_run completed job_id=%s no_pending_step", row.job_id)
                return
            try:
                row = await _execute_step(db, row, user, str(step.get("key") or ""))
            except Exception:
                logger.exception(
                    "[social_leads] auto_run stopped by error job_id=%s step=%s",
                    row.job_id,
                    step.get("key"),
                )
                return


@router.post("/api/social-leads/jobs", summary="启动 Reddit/X 只读线索采集任务")
async def start_social_leads_job(
    body: SocialLeadsStartBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    platform = _platform(body.platform)
    if platform == "reddit":
        accounts, communities = _split_reddit_accounts_and_communities(body.accounts, body.communities)
        post_ids = [_normalize_reddit_post_id(x) for x in body.post_ids]
    else:
        accounts = [_extract_x_screen_name(x) for x in body.accounts]
        post_ids = [_normalize_x_tweet_id(x) for x in body.post_ids]
        communities = []
    req = {
        "platform": platform,
        "keywords": _clean_list(body.keywords, limit=12),
        "accounts": [x for x in accounts if x][:12],
        "post_ids": [x for x in post_ids if x][:30],
        "communities": [x for x in communities if x][:12],
        "country": _clean_text(body.country, 80),
        "search_type": _clean_text(body.search_type, 40),
        "sort": _clean_text(body.sort, 40),
        "time_range": _clean_text(body.time_range, 40),
        "max_items": int(body.max_items or 30),
        "include_comments": bool(body.include_comments),
        "include_account_posts": bool(body.include_account_posts),
    }
    has_collection_input = bool(req["keywords"] or req["accounts"] or req["post_ids"] or req["communities"] or (platform == "x" and req["country"]))
    if not has_collection_input:
        raise HTTPException(status_code=400, detail="请至少输入一个采集条件")
    job_id = ("rd_" if platform == "reddit" else "x_") + uuid.uuid4().hex[:24]
    title = _clean_text(body.title, 160) or ("Reddit线索采集" if platform == "reddit" else "X线索采集")
    row = CreativeGenerationJob(
        job_id=job_id,
        user_id=current_user.id,
        feature_type=_feature_type(platform),
        provider="tikhub",
        status="queued",
        stage="queued",
        progress=0,
        title=title,
        prompt=json.dumps({"keywords": req["keywords"], "accounts": req["accounts"], "post_ids": req["post_ids"], "communities": req["communities"]}, ensure_ascii=False),
        request_payload=req,
        result_payload={},
        meta={"platform": platform, "steps": _initial_steps(req), "outputs": [], "current_step": ""},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if body.auto_run:
        asyncio.create_task(_auto_run_job(row.job_id))
    return {"ok": True, "job": _job_payload(row, db=db, include_sources=True)}


@router.get("/api/social-leads/jobs", summary="Reddit/X 线索采集任务列表")
def list_social_leads_jobs(
    platform: str = Query(""),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    feature_types = [_feature_type(platform)] if platform.strip() else list(_FEATURE_BY_PLATFORM.values())
    q = db.query(CreativeGenerationJob).filter(
        CreativeGenerationJob.user_id == current_user.id,
        CreativeGenerationJob.feature_type.in_(feature_types),
        CreativeGenerationJob.deleted_at.is_(None),
    )
    total = q.count()
    rows = q.order_by(CreativeGenerationJob.created_at.desc(), CreativeGenerationJob.id.desc()).offset(offset).limit(limit).all()
    return {"ok": True, "total": total, "items": [_job_payload(row, db=db, include_sources=True) for row in rows]}


@router.get("/api/social-leads/jobs/{job_id}", summary="Reddit/X 线索采集任务详情")
def get_social_leads_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(CreativeGenerationJob)
        .filter(
            CreativeGenerationJob.user_id == current_user.id,
            CreativeGenerationJob.feature_type.in_(list(_FEATURE_BY_PLATFORM.values())),
            CreativeGenerationJob.job_id == job_id.strip().lower(),
            CreativeGenerationJob.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "job": _job_payload(row, db=db, include_sources=True)}


@router.post("/api/social-leads/jobs/{job_id}/run-next", summary="继续执行 Reddit/X 线索采集任务")
async def run_next_social_leads_step(
    job_id: str,
    body: SocialLeadsStepBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(CreativeGenerationJob)
        .filter(
            CreativeGenerationJob.user_id == current_user.id,
            CreativeGenerationJob.feature_type.in_(list(_FEATURE_BY_PLATFORM.values())),
            CreativeGenerationJob.job_id == job_id.strip().lower(),
            CreativeGenerationJob.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if row.status in {"completed", "canceled", "stale"} and not body.step_key:
        return {"ok": True, "job": _job_payload(row, db=db, include_sources=True)}
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
        return {"ok": True, "job": _job_payload(row, db=db, include_sources=True)}
    row = await _execute_step(db, row, current_user, str(step.get("key") or ""))
    return {"ok": True, "job": _job_payload(row, db=db, include_sources=True)}


@router.post("/api/social-leads/jobs/{job_id}/resume", summary="自动续跑 Reddit/X 线索采集任务")
async def resume_social_leads_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(CreativeGenerationJob)
        .filter(
            CreativeGenerationJob.user_id == current_user.id,
            CreativeGenerationJob.feature_type.in_(list(_FEATURE_BY_PLATFORM.values())),
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
    return {"ok": True, "job": _job_payload(row, db=db, include_sources=True)}
