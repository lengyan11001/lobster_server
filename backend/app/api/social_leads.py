from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
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
_REDDIT_FEED_SORTS = {"HOT", "NEW", "TOP", "RISING", "CONTROVERSIAL"}
_REDDIT_RECENT_HOURS = 24
_REDDIT_MAX_COMMENT_PAGES_PER_POST = 100
_REDDIT_MAX_PROFILE_FETCH = 20
_LOW_INTENT_REDDIT_USERS = {"automoderator", "deleted", "[deleted]", "reddit", "admin"}
_HIGH_INTENT_PATTERNS = [
    (re.compile(r"\b(how|what|where|which|anyone|help|need|looking for|recommend|suggest|tool|app|service|solution)\b", re.I), 18, "有明确提问/求推荐表达"),
    (re.compile(r"\b(can'?t|cannot|problem|issue|stuck|struggling|failed|wrong|error|bug|hard|difficult)\b", re.I), 16, "表达痛点或问题"),
    (re.compile(r"\b(buy|pay|price|pricing|cost|subscribe|subscription|hire|agency|client|business|startup|lead|customer|marketing|sales)\b", re.I), 18, "出现商业/购买/获客相关词"),
    (re.compile(r"\b(ai|automation|workflow|generate|model|gemini|chatgpt|claude|bot|image|video|content)\b", re.I), 10, "与 AI/自动化/内容生产相关"),
]


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
        candidate_rows = _rows_with_candidate_profile_supplements(db, row.user_id, platform, payload["result_payload"], rows)
        payload["result_payload"] = _result_payload_with_current_candidates(payload["result_payload"], candidate_rows, platform)
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
            steps.append(_step("24小时社区帖子采集", "community_feed"))
        if req.get("accounts"):
            steps.append(_step("账号公开资料", "account_profiles"))
            if req.get("include_account_posts"):
                steps.append(_step("账号发帖/评论", "account_activity"))
        if (req.get("post_ids") or req.get("communities") or req.get("keywords")) and req.get("include_comments"):
            steps.append(_step("帖子内容和评论采集", "post_comments"))
        steps.append(_step("精准用户分析", "score_leads"))
        steps.append(_step("精准用户资料补全", "lead_profiles"))
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


def _reddit_feed_sort(value: Any) -> str:
    raw = _clean_text(value, 40).strip().upper()
    return raw if raw in _REDDIT_FEED_SORTS else "HOT"


def _reddit_recent_feed_sort(value: Any) -> str:
    raw = _clean_text(value, 40).strip().upper()
    if raw in {"HOT", "TOP", "RISING", "CONTROVERSIAL"}:
        return raw
    return "NEW"


def _summarize_query_outputs(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    query_outputs = [item for item in outputs if item.get("ok") is True or item.get("ok") is False]
    total = len(query_outputs)
    ok_count = sum(1 for item in query_outputs if item.get("ok"))
    failed_count = sum(1 for item in query_outputs if item.get("ok") is False)
    raw_item_count = sum(int(item.get("count") or item.get("raw_item_count") or 0) for item in query_outputs)
    errors: list[str] = []
    for item in query_outputs:
        err = _clean_text(item.get("error") or item.get("error_message"), 260)
        if err and err not in errors:
            errors.append(err)
    return {
        "total": total,
        "ok_count": ok_count,
        "failed_count": failed_count,
        "raw_item_count": raw_item_count,
        "errors": errors[:5],
    }


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


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (int, float)):
        try:
            if value > 10_000_000_000:
                value = value / 1000
            return datetime.utcfromtimestamp(float(value))
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_datetime(int(text))
    cleaned = text.replace("Z", "+00:00")
    if re.match(r".*[+-]\d{4}$", cleaned):
        cleaned = cleaned[:-5] + cleaned[-5:-2] + ":" + cleaned[-2:]
    for candidate in (cleaned, cleaned.replace(" ", "T", 1)):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except Exception:
            pass
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except Exception:
            pass
    return None


def _row_publish_datetime(row: TikHubSourceItem) -> Optional[datetime]:
    raw = _raw_body(row)
    return _parse_datetime(row.publish_time) or _parse_datetime(
        _first(raw, ["created_at", "created_utc", "createdAt", "created", "timestamp"])
    )


def _is_recent_row(row: TikHubSourceItem, *, hours: int = _REDDIT_RECENT_HOURS) -> bool:
    dt = _row_publish_datetime(row)
    if dt is None:
        return True
    return dt >= _utcnow() - timedelta(hours=hours)


def _recent_post_rows_for_job(db: Session, user_id: int, platform: str, job_id: str, limit: int) -> list[TikHubSourceItem]:
    rows = _rows_for_job(db, user_id, platform, job_id)
    out: list[TikHubSourceItem] = []
    for row in rows:
        if row.source_type not in {"search_result", "subreddit_post", "user_post", "trend", "post_detail"}:
            continue
        if platform == "reddit" and not _is_recent_row(row):
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _analysis_rows_for_job(db: Session, user_id: int, platform: str, job_id: str) -> list[TikHubSourceItem]:
    rows = _rows_for_job(db, user_id, platform, job_id)
    if platform != "reddit":
        return rows
    out: list[TikHubSourceItem] = []
    for row in rows:
        if row.source_type in {"search_result", "subreddit_post", "user_post", "trend", "post_detail"}:
            if _is_recent_row(row):
                out.append(row)
            continue
        out.append(row)
    return out


def _keep_recent_reddit_posts_for_job(db: Session, row: CreativeGenerationJob) -> dict[str, int]:
    rows = _rows_for_job(db, row.user_id, "reddit", row.job_id)
    kept = 0
    excluded = 0
    for source_row in rows:
        if source_row.source_type not in {"search_result", "subreddit_post", "user_post", "trend", "post_detail"}:
            continue
        if _is_recent_row(source_row):
            kept += 1
            continue
        raw = dict(source_row.raw or {}) if isinstance(source_row.raw, dict) else {}
        meta = raw.get("__lobster_ip_content_meta") if isinstance(raw.get("__lobster_ip_content_meta"), dict) else {}
        if str(meta.get("social_leads_job_id") or "") == row.job_id:
            meta = {
                **meta,
                "social_leads_job_id": "",
                "excluded_social_leads_job_id": row.job_id,
                "exclude_reason": f"older_than_{_REDDIT_RECENT_HOURS}h",
            }
            raw["__lobster_ip_content_meta"] = _jsonable(meta)
            source_row.raw = _jsonable(raw)
            source_row.updated_at = _utcnow()
            excluded += 1
    if excluded:
        db.flush()
    return {"recent_post_count": kept, "excluded_old_post_count": excluded}


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


def _reddit_more_comment_cursors(payload: Any) -> list[str]:
    cursors: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            more = value.get("more")
            if isinstance(more, dict):
                cursor = _clean_text(more.get("cursor"), 500)
                if cursor and cursor not in cursors:
                    cursors.append(cursor)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    walk(child)

    walk(payload)
    return cursors


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
        meta = _row_meta(row)
        job_ids = meta.get("social_leads_job_ids") if isinstance(meta.get("social_leads_job_ids"), list) else []
        if str(meta.get("social_leads_job_id") or "") == job_id or job_id in {str(x) for x in job_ids}:
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


def _candidate_handles_from_result_payload(result_payload: Any, platform: str) -> list[str]:
    if not isinstance(result_payload, dict):
        return []
    candidates = result_payload.get("candidates")
    if not isinstance(candidates, list):
        return []
    out: list[str] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        raw = item.get("handle") or item.get("candidate_key") or item.get("name") or item.get("url")
        handle = _extract_reddit_username(raw) if platform == "reddit" else _extract_x_screen_name(raw)
        if handle and handle.lower() not in _LOW_INTENT_REDDIT_USERS and handle not in out:
            out.append(handle)
        if len(out) >= 100:
            break
    return out


def _rows_with_candidate_profile_supplements(
    db: Session,
    user_id: int,
    platform: str,
    result_payload: Any,
    rows: list[TikHubSourceItem],
) -> list[TikHubSourceItem]:
    handles = _candidate_handles_from_result_payload(result_payload, platform)
    if not handles:
        return rows
    existing = {(row.source_type, (row.author_key or row.item_key or "").lower()) for row in rows}
    missing = [handle for handle in handles if ("user_profile", handle.lower()) not in existing]
    if not missing:
        return rows
    query = (
        db.query(TikHubSourceItem)
        .filter(
            TikHubSourceItem.user_id == user_id,
            TikHubSourceItem.platform == platform,
            TikHubSourceItem.source_type == "user_profile",
        )
        .order_by(TikHubSourceItem.updated_at.desc(), TikHubSourceItem.id.desc())
        .limit(1000)
    )
    wanted = {handle.lower() for handle in missing}
    supplements: list[TikHubSourceItem] = []
    seen = set(existing)
    for row in query.all():
        handle = (row.author_key or row.item_key or "").lower()
        if handle not in wanted:
            continue
        key = (row.source_type, handle)
        if key in seen:
            continue
        supplements.append(row)
        seen.add(key)
        if len(supplements) >= len(wanted):
            break
    return rows + supplements


def _result_payload_with_current_candidates(result_payload: Any, rows: list[TikHubSourceItem], platform: str) -> dict[str, Any]:
    payload = dict(result_payload or {}) if isinstance(result_payload, dict) else {}
    if not rows:
        return payload
    current_candidates = _high_intent_candidates(rows, platform=platform, limit=100)
    if not current_candidates:
        return payload
    payload["candidates"] = current_candidates
    if isinstance(payload.get("intent_analysis"), dict):
        payload["intent_analysis"] = {
            **payload["intent_analysis"],
            "candidates": current_candidates,
            "candidate_count": len(current_candidates),
            "high_intent_count": sum(1 for item in current_candidates if item.get("intent_level") == "high"),
            "medium_intent_count": sum(1 for item in current_candidates if item.get("intent_level") == "medium"),
        }
    if isinstance(payload.get("lead_summary"), dict):
        payload["lead_summary"] = {
            **payload["lead_summary"],
            "candidates": current_candidates,
            "candidate_count": len(current_candidates),
            "high_intent_count": sum(1 for item in current_candidates if item.get("intent_level") == "high"),
            "medium_intent_count": sum(1 for item in current_candidates if item.get("intent_level") == "medium"),
        }
    return payload


def _source_item_payload(row: TikHubSourceItem) -> dict[str, Any]:
    meta = _row_meta(row)
    raw = _raw_body(row)
    title = row.title or _first(raw, ["title", "profile_title", "name", "display_name", "subreddit_name_prefixed", "full_text", "text", "body"]) or ""
    description = row.description or _first(raw, ["public_description", "description", "selftext", "body", "full_text", "text"]) or ""
    handle = row.author_key or _first(raw, ["author", "username", "screen_name", "legacy.screen_name"]) or ""
    display_name = row.author_name or _first(raw, ["display_name", "name", "legacy.name"]) or handle
    url = row.public_url or _first(raw, ["profile_url", "permalink", "url", "tweet_url", "twitter_url"]) or ""
    if isinstance(url, str) and url.startswith("/"):
        url = "https://www.reddit.com" + url
    metrics = row.metrics if isinstance(row.metrics, dict) else {}
    raw_preview: dict[str, Any] = {}
    for key in ("author", "username", "name", "display_name", "title", "profile_title", "subreddit", "subreddit_name_prefixed", "public_description", "description", "selftext", "body", "full_text", "text", "permalink", "url", "profile_url", "score", "ups", "num_comments", "total_karma", "post_karma", "comment_karma", "post_count", "comment_count", "subscribers_count", "account_type", "is_verified", "is_accepting_chats", "is_accepting_followers", "is_accepting_pms", "is_user_banned", "is_nsfw", "social_links", "subscribers", "followers_count", "created_utc", "created_at"):
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


def _intent_score_from_text(text: str, source_type: str, metrics: dict[str, Any]) -> tuple[int, list[str]]:
    haystack = _clean_long_text(text, 3000)
    score = 20
    reasons: list[str] = []
    if source_type == "post_comment":
        score += 12
        reasons.append("来自评论互动")
    elif source_type in {"subreddit_post", "post_detail", "search_result"}:
        score += 8
        reasons.append("来自主动发帖")
    for pattern, points, reason in _HIGH_INTENT_PATTERNS:
        if pattern.search(haystack):
            score += points
            if reason not in reasons:
                reasons.append(reason)
    try:
        score += min(10, max(0, int(metrics.get("score") or metrics.get("ups") or 0)))
    except Exception:
        pass
    if len(haystack) >= 80:
        score += 6
        reasons.append("内容足够具体")
    return max(0, min(100, score)), reasons[:6]


def _candidate_intent(candidate: dict[str, Any]) -> dict[str, Any]:
    evidence = [item for item in (candidate.get("evidence") or []) if isinstance(item, dict)]
    text_parts: list[str] = []
    reasons: list[str] = []
    best_score = 0
    for item in evidence:
        text = "\n".join(str(item.get(k) or "") for k in ("title", "description", "body", "text"))
        text_parts.append(text)
        score, item_reasons = _intent_score_from_text(text, str(item.get("source_type") or ""), candidate.get("metrics") or {})
        best_score = max(best_score, score)
        for reason in item_reasons:
            if reason not in reasons:
                reasons.append(reason)
    profile_bonus = 0
    source_types = {str(item.get("source_type") or "") for item in evidence}
    if "user_profile" in source_types:
        profile_bonus += 6
        reasons.append("已补充公开资料")
    if len(source_types) >= 2:
        profile_bonus += 6
        reasons.append("多个来源重复出现")
    score = min(100, best_score + profile_bonus + min(12, len(evidence) * 4))
    level = "low"
    if score >= 72:
        level = "high"
    elif score >= 52:
        level = "medium"
    return {
        "intent_score": score,
        "intent_level": level,
        "intent_reasons": reasons[:8] or ["可见公开互动"],
        "intent_excerpt": _clean_long_text("\n".join(x for x in text_parts if x).strip(), 800),
    }


def _metric_label(key: str) -> str:
    labels = {
        "score": "互动分",
        "ups": "赞同数",
        "comments": "评论数",
        "karma": "总 Karma",
        "post_karma": "发帖 Karma",
        "comment_karma": "评论 Karma",
        "post_count": "发帖数",
        "comment_count": "评论数",
        "subscribers_count": "订阅者",
        "account_type": "账号类型",
        "is_verified": "已验证",
        "is_accepting_chats": "可聊天",
        "is_accepting_pms": "可私信",
        "is_nsfw": "NSFW",
    }
    return labels.get(key, key)


def _format_metric_value(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _profile_evidence_text(body: dict[str, Any], metrics: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "karma",
        "post_karma",
        "comment_karma",
        "post_count",
        "comment_count",
        "subscribers_count",
        "account_type",
        "is_verified",
        "is_accepting_chats",
        "is_accepting_pms",
        "is_nsfw",
    ):
        value = metrics.get(key)
        if value in (None, "", [], {}):
            continue
        parts.append(f"{_metric_label(key)}：{_format_metric_value(value)}")
    social_links = _lookup(body, "social_links")
    if isinstance(social_links, list):
        link_texts: list[str] = []
        for link in social_links[:3]:
            if not isinstance(link, dict):
                continue
            title = _clean_text(link.get("title") or link.get("type") or "链接", 80)
            url = _clean_long_text(link.get("url"), 300)
            if url:
                link_texts.append(f"{title} {url}")
        if link_texts:
            parts.append("社交链接：" + "；".join(link_texts))
    return "；".join(parts)


def _candidate_evidence_from_raw(
    body: dict[str, Any],
    *,
    source_type: str,
    source_reason: str,
    bio: Any,
    url: Any,
    metrics: dict[str, Any],
) -> Optional[dict[str, Any]]:
    title = _clean_text(
        _lookup(body, "title")
        or _lookup(body, "profile_title")
        or _lookup(body, "post_title")
        or _lookup(body, "full_text")
        or _lookup(body, "display_name")
        or _lookup(body, "name")
        or _lookup(body, "username")
        or source_reason,
        255,
    )
    body_text = _clean_long_text(_lookup(body, "body") or _lookup(body, "text") or _lookup(body, "selftext") or "", 1000)
    description = _clean_long_text(bio, 600)
    if source_type == "user_profile":
        profile_text = _profile_evidence_text(body, metrics)
        if profile_text:
            description = _clean_long_text(profile_text, 1000)
    evidence = {
        "source_type": source_type,
        "source_reason": source_reason,
        "title": title,
        "description": description,
        "body": body_text,
        "url": _clean_long_text(url, 1000),
    }
    if not any(evidence.get(key) for key in ("title", "description", "body", "url")):
        return None
    return evidence


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
            or _lookup(body, "authorInfo.name")
            or _lookup(body, "redditorInfoByName.name")
            or _lookup(body, "data.redditorInfoByName.name")
            or _lookup(user, "username")
            or (_lookup(body, "name") if source_type in {"user_profile", "user_comment", "post_comment"} else "")
            or _lookup(body, "display_name")
        )
        name = username or _lookup(body, "display_name") or _lookup(body, "title") or _lookup(body, "subreddit_name_prefixed") or _lookup(body, "subreddit")
        url = _lookup(body, "profile_url") or _lookup(body, "permalink") or _lookup(body, "url")
        if isinstance(url, str) and url.startswith("/"):
            url = "https://www.reddit.com" + url
        bio = _lookup(body, "public_description") or _lookup(body, "description") or _lookup(body, "body") or _lookup(body, "text") or _lookup(body, "selftext") or _lookup(body, "title")
        metrics = {
            "score": _lookup(body, "score"),
            "ups": _lookup(body, "ups"),
            "comments": _lookup(body, "num_comments"),
            "karma": _lookup(body, "total_karma") or _lookup(body, "post_karma") or _lookup(body, "comment_karma"),
            "post_karma": _lookup(body, "post_karma"),
            "comment_karma": _lookup(body, "comment_karma"),
            "post_count": _lookup(body, "post_count"),
            "comment_count": _lookup(body, "comment_count"),
            "subscribers_count": _lookup(body, "subscribers_count"),
            "account_type": _lookup(body, "account_type"),
            "is_verified": _lookup(body, "is_verified"),
            "is_accepting_chats": _lookup(body, "is_accepting_chats"),
            "is_accepting_pms": _lookup(body, "is_accepting_pms"),
            "is_nsfw": _lookup(body, "is_nsfw"),
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
    clean_metrics = {k: v for k, v in metrics.items() if v not in (None, "", [], {})}
    evidence = _candidate_evidence_from_raw(
        body,
        source_type=source_type,
        source_reason=source_reason,
        bio=bio,
        url=url,
        metrics=clean_metrics,
    )
    return {
        "candidate_key": key,
        "name": _clean_text(name or key, 255),
        "handle": _clean_text(username, 255),
        "platform": platform,
        "source_type": source_type,
        "source_reason": source_reason,
        "bio": _clean_long_text(bio, 1200),
        "url": _clean_long_text(url, 1000),
        "metrics": clean_metrics,
        "evidence": [evidence] if evidence else [],
        "raw": body,
    }


def _candidate_from_row(row: TikHubSourceItem) -> Optional[dict[str, Any]]:
    body = row.raw if isinstance(row.raw, dict) else {}
    meta = _row_meta(row)
    raw_body = _raw_body(row)
    if row.platform == "reddit" and row.source_type in {"subreddit_post", "post_detail", "post_comment", "user_post", "user_comment"}:
        handle = _clean_text(row.author_key or _lookup(raw_body, "author") or _lookup(raw_body, "username"), 191)
        if not handle or handle.lower() in _LOW_INTENT_REDDIT_USERS:
            return None
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
    row_evidence = {
        "source_item_id": row.id,
        "source_type": row.source_type,
        "source_reason": str(meta.get("source_reason") or row.source_type),
        "title": row.title or row.author_name or row.author_key or "",
        "description": (
            _profile_evidence_text(raw_body, row.metrics or {})
            if row.source_type == "user_profile"
            else row.description or ""
        ),
        "body": "" if row.source_type == "user_profile" else row.description or "",
        "url": row.public_url or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }
    existing_evidence = [item for item in candidate.setdefault("evidence", []) if isinstance(item, dict)]
    if _evidence_marker(row_evidence) not in {_evidence_marker(item) for item in existing_evidence}:
        candidate["evidence"].append(row_evidence)
    if not candidate.get("url") and row.public_url:
        candidate["url"] = row.public_url
    return candidate


def _evidence_marker(item: dict[str, Any]) -> str:
    composite = "|".join(
        [
            str(item.get("source_type") or ""),
            str(item.get("source_reason") or ""),
            str(item.get("url") or ""),
            str(item.get("title") or ""),
        ]
    )
    return composite if composite.strip("|") else str(item.get("source_item_id") or "")


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
        existing = {_evidence_marker(x) for x in cur.get("evidence") or [] if isinstance(x, dict)}
        for ev in item.get("evidence") or []:
            marker = _evidence_marker(ev) if isinstance(ev, dict) else ""
            if marker and marker not in existing:
                cur.setdefault("evidence", []).append(ev)
                existing.add(marker)
    out = list(merged.values())
    out.sort(key=lambda x: (len(x.get("evidence") or []), sum(1 for v in (x.get("metrics") or {}).values() if v)), reverse=True)
    for idx, item in enumerate(out, start=1):
        item["rank"] = idx
        intent = _candidate_intent(item)
        item.update(intent)
        item["score"] = max(10, min(99, item.get("intent_score") or 0))
    return out


def _high_intent_candidates(rows: list[TikHubSourceItem], *, platform: str, limit: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for source_row in rows:
        candidate = _candidate_from_row(source_row)
        if not candidate:
            continue
        handle = str(candidate.get("handle") or candidate.get("candidate_key") or "").strip()
        if platform == "reddit" and handle.lower() in _LOW_INTENT_REDDIT_USERS:
            continue
        candidates.append(candidate)
    merged = _merge_candidates(candidates)
    filtered = [
        item
        for item in merged
        if item.get("intent_level") in {"high", "medium"} or int(item.get("intent_score") or 0) >= 52
    ]
    return (filtered or merged)[:limit]


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


def _mark_autorun_resume_requested(row: CreativeGenerationJob) -> None:
    meta = _meta(row)
    meta["autorun_resume_requested_at"] = _utcnow().isoformat()
    _set_meta(row, meta)


def _schedule_autorun_if_needed(row: CreativeGenerationJob, db: Session) -> bool:
    if not _needs_autorun_resume(row):
        return False
    _mark_autorun_resume_requested(row)
    db.commit()
    logger.info("[social_leads] scheduled idle job resume job_id=%s status=%s", row.job_id, row.status)
    asyncio.create_task(_auto_run_job(row.job_id))
    return True


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
            query = result.get("query") if isinstance(result.get("query"), dict) else {}
            outputs.append({"query_type": "x_trending", "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": query.get("query_id"), "error": query.get("error_message") or result.get("error_message") or ""})

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
                query = result.get("query") if isinstance(result.get("query"), dict) else {}
                outputs.append({"keyword": keyword, "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": query.get("query_id"), "error": query.get("error_message") or result.get("error_message") or ""})

        elif step_key == "community_feed" and platform == "reddit":
            for community in req.get("communities") or []:
                community_name = _extract_subreddit(community)
                if not community_name:
                    continue
                result = await _run_query(
                    db=db,
                    current_user=current_user,
                    job=row,
                    step_key=step_key,
                    query_type="reddit_subreddit_feed",
                    params={"subreddit_name": community_name, "sort": _reddit_recent_feed_sort(req.get("sort")), "need_format": False},
                    source_reason=f"Reddit社区 r/{community_name}",
                )
                query = result.get("query") if isinstance(result.get("query"), dict) else {}
                outputs.append({"community": community_name, "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": query.get("query_id"), "error": query.get("error_message") or result.get("error_message") or ""})
            if platform == "reddit":
                recent_summary = _keep_recent_reddit_posts_for_job(db, row)
                outputs.append({"query_type": "recent_24h_filter", "ok": None, "count": 0, **recent_summary})

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
                query = result.get("query") if isinstance(result.get("query"), dict) else {}
                outputs.append({"account": account, "ok": result.get("ok"), "candidate": candidate, "query_id": query.get("query_id"), "error": query.get("error_message") or result.get("error_message") or ""})

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
                        query = result.get("query") if isinstance(result.get("query"), dict) else {}
                        outputs.append({"account": account, "query_type": query_type, "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": query.get("query_id"), "error": query.get("error_message") or result.get("error_message") or ""})
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
                    query = result.get("query") if isinstance(result.get("query"), dict) else {}
                    outputs.append({"account": account, "query_type": "x_user_posts", "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": query.get("query_id"), "error": query.get("error_message") or result.get("error_message") or ""})

        elif step_key == "post_comments":
            explicit_ids = req.get("post_ids") or []
            post_ids = list(explicit_ids)
            if len(post_ids) < int(req.get("max_items") or 30):
                rows = _recent_post_rows_for_job(db, row.user_id, platform, row.job_id, int(req.get("max_items") or 30))
                for post_id in _extract_post_ids_from_rows(rows, platform, int(req.get("max_items") or 30)):
                    if post_id not in post_ids:
                        post_ids.append(post_id)
            for post_id in post_ids[: int(req.get("max_items") or 30)]:
                if platform == "reddit":
                    detail_result = await _run_query(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type="reddit_post_details",
                        params={"post_id": _normalize_reddit_post_id(post_id), "need_format": False},
                        source_reason=f"Reddit帖子内容 {post_id}",
                    )
                    detail_query = detail_result.get("query") if isinstance(detail_result.get("query"), dict) else {}
                    outputs.append({"post_id": post_id, "query_type": "reddit_post_details", "ok": detail_result.get("ok"), "count": detail_result.get("raw_item_count"), "query_id": detail_query.get("query_id"), "error": detail_query.get("error_message") or detail_result.get("error_message") or ""})
                    seen_cursors: set[str] = set()
                    pending_cursors = [""]
                    page_count = 0
                    saved_comments = 0
                    while pending_cursors and page_count < _REDDIT_MAX_COMMENT_PAGES_PER_POST:
                        cursor = pending_cursors.pop(0)
                        if cursor and cursor in seen_cursors:
                            continue
                        if cursor:
                            seen_cursors.add(cursor)
                        params = {"post_id": _normalize_reddit_post_id(post_id), "sort_type": "CONFIDENCE", "need_format": False}
                        if cursor:
                            params["after"] = cursor
                        result = await _run_query(
                            db=db,
                            current_user=current_user,
                            job=row,
                            step_key=step_key,
                            query_type="reddit_post_comments",
                            params=params,
                            source_reason=f"Reddit帖子评论 {post_id}",
                        )
                        page_count += 1
                        saved_comments += int(result.get("raw_item_count") or 0)
                        for next_cursor in _reddit_more_comment_cursors(result.get("raw_response")):
                            if next_cursor not in seen_cursors and next_cursor not in pending_cursors:
                                pending_cursors.append(next_cursor)
                        query = result.get("query") if isinstance(result.get("query"), dict) else {}
                        outputs.append({"post_id": post_id, "query_type": "reddit_post_comments", "page": page_count, "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": query.get("query_id"), "error": query.get("error_message") or result.get("error_message") or ""})
                    continue
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
                query = result.get("query") if isinstance(result.get("query"), dict) else {}
                outputs.append({"post_id": post_id, "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": query.get("query_id"), "error": query.get("error_message") or result.get("error_message") or ""})

        elif step_key == "score_leads":
            rows = _analysis_rows_for_job(db, row.user_id, platform, row.job_id)
            candidates = _high_intent_candidates(rows, platform=platform, limit=int(req.get("max_items") or 30))
            analysis = {
                "platform": platform,
                "source_rows": len(rows),
                "candidate_count": len(candidates),
                "high_intent_count": sum(1 for item in candidates if item.get("intent_level") == "high"),
                "medium_intent_count": sum(1 for item in candidates if item.get("intent_level") == "medium"),
                "candidates": candidates,
            }
            row.result_payload = {**(row.result_payload or {}), "intent_analysis": analysis, "candidates": candidates}
            _append_output(row, step_key=step_key, title="精准用户分析", kind="intent_analysis", data=analysis)
            _mark_step(row, step_key, "completed", detail=f"筛出 {len(candidates)} 个可能精准用户", result={"candidate_count": len(candidates), "source_rows": len(rows)})
            _update_progress(row)
            db.commit()
            db.refresh(row)
            return row

        elif step_key == "lead_profiles":
            existing = (row.result_payload or {}).get("candidates") if isinstance(row.result_payload, dict) else []
            candidates = existing if isinstance(existing, list) else []
            fetched = 0
            skipped = 0
            for item in candidates[:_REDDIT_MAX_PROFILE_FETCH]:
                handle = _clean_text(item.get("handle") or item.get("candidate_key"), 120)
                if not handle or handle.lower() in _LOW_INTENT_REDDIT_USERS:
                    skipped += 1
                    continue
                if platform == "reddit":
                    result = await _run_query(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type="reddit_user_profile",
                        params={"username": handle, "need_format": False},
                        source_reason=f"Reddit精准用户资料 u/{handle}",
                    )
                else:
                    result = await _run_query(
                        db=db,
                        current_user=current_user,
                        job=row,
                        step_key=step_key,
                        query_type="x_user_profile",
                        params={"screen_name": handle},
                        source_reason=f"X精准用户资料 @{handle}",
                    )
                fetched += 1
                query = result.get("query") if isinstance(result.get("query"), dict) else {}
                outputs.append({"account": handle, "ok": result.get("ok"), "count": result.get("raw_item_count"), "query_id": query.get("query_id"), "error": query.get("error_message") or result.get("error_message") or ""})
            if not candidates:
                _append_output(row, step_key=step_key, title="精准用户资料补全", kind="queries", data={"queries": [], "summary": {"total": 0, "ok_count": 0, "failed_count": 0, "raw_item_count": 0, "errors": []}})
                _mark_step(row, step_key, "skipped", detail="没有可补全的精准用户", result={"candidate_count": 0})
                _update_progress(row)
                db.commit()
                db.refresh(row)
                return row
            if fetched == 0 and skipped:
                _mark_step(row, step_key, "skipped", detail=f"跳过 {skipped} 个无效账号", result={"skipped": skipped})
                _update_progress(row)
                db.commit()
                db.refresh(row)
                return row

        elif step_key == "merge_leads":
            rows = _analysis_rows_for_job(db, row.user_id, platform, row.job_id)
            merged = _high_intent_candidates(rows, platform=platform, limit=int(req.get("max_items") or 30))
            payload = {
                "platform": platform,
                "total_source_rows": len(rows),
                "candidate_count": len(merged),
                "high_intent_count": sum(1 for item in merged if item.get("intent_level") == "high"),
                "medium_intent_count": sum(1 for item in merged if item.get("intent_level") == "medium"),
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

    summary = _summarize_query_outputs(outputs)
    _append_output(row, step_key=step_key, title=_step_title(row, step_key), kind="queries", data={"queries": outputs, "summary": summary})
    if summary["total"] and summary["ok_count"] == 0:
        detail = f"采集失败 {summary['failed_count']} 次"
        if summary["errors"]:
            detail += "：" + summary["errors"][0]
        row.status = "failed"
        row.error = detail[:4000]
        _mark_step(row, step_key, "failed", detail=detail, result={"queries": outputs, "summary": summary}, error=row.error)
        db.commit()
        db.refresh(row)
        return row
    detail = f"完成 {summary['ok_count']} 次采集，保存 {summary['raw_item_count']} 条数据"
    if summary["failed_count"]:
        detail += f"，失败 {summary['failed_count']} 次"
    _mark_step(row, step_key, "completed", detail=detail, result={"queries": outputs, "summary": summary})
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
        "include_comments": bool(body.include_comments) or bool(platform == "reddit" and (communities or post_ids or body.keywords)),
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
async def list_social_leads_jobs(
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
    for row in rows:
        if _schedule_autorun_if_needed(row, db):
            db.refresh(row)
    return {"ok": True, "total": total, "items": [_job_payload(row, db=db, include_sources=True) for row in rows]}


@router.get("/api/social-leads/jobs/{job_id}", summary="Reddit/X 线索采集任务详情")
async def get_social_leads_job(
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
    if _schedule_autorun_if_needed(row, db):
        db.refresh(row)
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
