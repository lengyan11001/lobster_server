from __future__ import annotations

import asyncio
import uuid
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Asset,
    H5ChatDevicePresence,
    H5ChatEvent,
    H5ChatMessage,
    PublishAccount,
    ScheduledTask,
    ScheduledTaskRun,
    User,
    UserInstallation,
)
from .publish import SUPPORTED_PLATFORMS
from .admin import AdminContext, _agent_sub_user_ids, _verify_admin_token
from .auth import get_current_user, get_current_user_id_from_token
from .ip_content_studio import run_ip_content_daily_scheduled
from .installation_slots import ensure_installation_slot
from .mobile_identity import online_user_for_mobile_user
from ..services.runtime_cache import cache_delete, cache_flag_recent, cache_mark_flag

router = APIRouter()

_TASK_KINDS = {"openclaw_message", "chat_message", "capability", "ip_content_daily", "douyin_leads"}
_SCHEDULE_TYPES = {"once", "interval", "daily_times"}
_FINAL_STATUSES = {"completed", "failed", "cancelled"}
_MAX_TARGET_DEVICES = 20
_VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v", ".avi")
_RUNNING_STATUSES = {"running", "processing", "pending", "queued", "waiting"}
_GOAL_VIDEO_SOURCE_AI_IMAGE = "ai_image"
_GOAL_VIDEO_SOURCE_ASSET_RANDOM = "asset_random"
_DISABLED_SCHEDULED_CAPABILITIES = {"create.video.pipeline", "create.ppt.pipeline"}
_PENDING_INSTALLATION_TOUCH_MIN_SECONDS = 60
_RUN_PENDING_EMPTY_CACHE_SECONDS = 20.0
_PUBLISH_PENDING_EMPTY_CACHE_SECONDS = 20.0
_pending_empty_cache: Dict[str, float] = {}


def _creative_candidate_group(meta: Optional[dict]) -> str:
    if not isinstance(meta, dict):
        return ""
    current = str(meta.get("creative_candidate_group") or "").strip()
    if current:
        return current[:40]
    raw = meta.get("creative_candidate_groups")
    if isinstance(raw, str):
        raw_items = re.split(r"[,\s，、；;]+", raw)
    elif isinstance(raw, list):
        raw_items = raw
    else:
        raw_items = []
    for item in raw_items:
        name = str(item or "").strip()
        if name:
            return name[:40]
    return ""


class ScheduledTaskCreate(BaseModel):
    user_id: Optional[int] = None
    title: str = Field("", max_length=160)
    task_kind: str = "openclaw_message"
    content: str = Field("", max_length=12000)
    payload: Dict[str, Any] = Field(default_factory=dict)
    schedule_type: str = "once"
    interval_seconds: Optional[int] = None
    start_at: Optional[str] = None
    daily_times: Any = Field(default_factory=list)
    timezone_offset_minutes: Optional[int] = None
    installation_ids: List[str] = Field(default_factory=list)


class ScheduledTaskPatch(BaseModel):
    status: Optional[str] = None


class ScheduledTaskEventIn(BaseModel):
    type: str = Field("progress", min_length=1, max_length=32)
    payload: Dict[str, Any] = Field(default_factory=dict)


class ScheduledTaskCompleteIn(BaseModel):
    result_text: Optional[str] = None
    result_payload: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class ScheduledPublishCompleteIn(BaseModel):
    publish_result: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="seconds") + "Z"


def _parse_client_datetime(value: Optional[str], timezone_offset_minutes: Optional[int]) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        text = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(status_code=400, detail="开始时间格式不正确")
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    offset = int(timezone_offset_minutes if timezone_offset_minutes is not None else 480)
    return dt - timedelta(minutes=offset)


_DAILY_TIME_RE = re.compile(r"^([01]?\d|2[0-3])[:：]([0-5]\d)$")
_DAILY_HOUR_RE = re.compile(r"^([01]?\d|2[0-3])(?:点|时)?$")


def _normalize_daily_times(values: Any) -> List[str]:
    if isinstance(values, str):
        raw_values = re.split(r"[,\s，、]+", values)
    elif isinstance(values, list):
        raw_values = values
    else:
        raw_values = []
    seen: set[str] = set()
    out: List[str] = []
    for raw in raw_values:
        text = str(raw or "").strip()
        if not text:
            continue
        m = _DAILY_TIME_RE.match(text)
        if m:
            item = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
        else:
            m = _DAILY_HOUR_RE.match(text)
            if not m:
                raise HTTPException(status_code=400, detail="固定时间格式应为 9,12,18 或 09:00,12:00,18:00")
            item = f"{int(m.group(1)):02d}:00"
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    out.sort()
    if not out:
        raise HTTPException(status_code=400, detail="请填写每天固定执行时间")
    if len(out) > 24:
        raise HTTPException(status_code=400, detail="每天固定执行时间最多 24 个")
    return out


def _compute_next_daily_time(
    *,
    now_utc: datetime,
    daily_times: List[str],
    timezone_offset_minutes: int,
    not_before_utc: Optional[datetime] = None,
    inclusive: bool = False,
) -> datetime:
    floor = max(now_utc, not_before_utc) if not_before_utc else now_utc
    local_floor = floor + timedelta(minutes=timezone_offset_minutes)
    base_date = local_floor.date()
    for day_offset in range(0, 370):
        cur_date = base_date + timedelta(days=day_offset)
        for item in daily_times:
            hour, minute = [int(x) for x in item.split(":", 1)]
            local_dt = datetime(cur_date.year, cur_date.month, cur_date.day, hour, minute)
            candidate = local_dt - timedelta(minutes=timezone_offset_minutes)
            if candidate > floor or (inclusive and candidate == floor):
                return candidate
    return floor + timedelta(days=1)


def _schedule_config_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    cfg = payload.get("schedule_config")
    if isinstance(cfg, dict):
        return cfg
    return {}


def _task_schedule_label(row: ScheduledTask) -> str:
    cfg = _schedule_config_from_payload(row.payload or {})
    if row.schedule_type == "interval":
        minutes = max(1, int(row.interval_seconds or 3600) // 60)
        first = str(cfg.get("start_at") or "").strip()
        return f"每 {minutes} 分钟" + (f"；开始 {first}" if first else "")
    if row.schedule_type == "daily_times":
        times = cfg.get("daily_times") if isinstance(cfg.get("daily_times"), list) else []
        return "每天 " + "、".join(str(x) for x in times)
    first = str(cfg.get("start_at") or "").strip()
    return "一次性" + (f"；执行 {first}" if first else "")


def _header_installation_id(request: Request) -> str:
    return (
        request.headers.get("X-Installation-Id")
        or request.headers.get("x-installation-id")
        or ""
    ).strip()


def _touch_installation_slot_lazy(db: Session, user_id: int, installation_id: str) -> None:
    if not installation_id:
        return
    now = datetime.utcnow()
    row = (
        db.query(UserInstallation)
        .filter(UserInstallation.user_id == user_id, UserInstallation.installation_id == installation_id)
        .first()
    )
    if row:
        last_seen_at = row.last_seen_at
        if last_seen_at and (now - last_seen_at).total_seconds() < _PENDING_INSTALLATION_TOUCH_MIN_SECONDS:
            return
        row.last_seen_at = now
        db.commit()
        return
    ensure_installation_slot(db, user_id, installation_id)


def _pending_cache_key(kind: str, user_id: int, installation_id: str) -> str:
    return f"scheduled:{kind}:pending-empty:{user_id}:{installation_id or '-'}"


def _pending_empty_recent(key: str, ttl_seconds: float) -> bool:
    if cache_flag_recent(key):
        return True
    ts = _pending_empty_cache.get(key)
    return bool(ts and (time.monotonic() - ts) < ttl_seconds)


def _mark_pending_empty(key: str, ttl_seconds: float) -> None:
    cache_mark_flag(key, ttl_seconds)
    _pending_empty_cache[key] = time.monotonic()
    if len(_pending_empty_cache) > 5000:
        cutoff = time.monotonic() - 120
        for old_key, ts in list(_pending_empty_cache.items())[:1000]:
            if ts < cutoff:
                _pending_empty_cache.pop(old_key, None)


def _clear_pending_empty(key: str) -> None:
    cache_delete(key)
    _pending_empty_cache.pop(key, None)


def _clear_pending_empty_for_target(kind: str, user_id: int, installation_id: Optional[str]) -> None:
    _clear_pending_empty(_pending_cache_key(kind, user_id, installation_id or ""))
    if installation_id:
        _clear_pending_empty(_pending_cache_key(kind, user_id, ""))


def _clean_installation_ids(values: Optional[List[str]]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for raw in values or []:
        val = str(raw or "").strip()
        if not val or val in seen:
            continue
        seen.add(val)
        out.append(val[:128])
        if len(out) >= _MAX_TARGET_DEVICES:
            break
    return out


def _normalize_task_kind(value: str) -> str:
    kind = (value or "openclaw_message").strip().lower()
    if kind not in _TASK_KINDS:
        raise HTTPException(status_code=400, detail="不支持的任务类型")
    return kind


def _normalize_goal_video_source_mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"ai_image", "ai", "generated_image", "image_generate", "generate_image"}:
        return _GOAL_VIDEO_SOURCE_AI_IMAGE
    return _GOAL_VIDEO_SOURCE_ASSET_RANDOM


def _normalize_goal_video_task_payload(payload: Dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    if str(payload.get("capability_id") or "").strip() != "goal.video.pipeline":
        return
    cap_payload = payload.get("payload")
    if not isinstance(cap_payload, dict):
        cap_payload = {}
        payload["payload"] = cap_payload
    raw_source_mode = (
        cap_payload.get("source_mode")
        or cap_payload.get("video_source_mode")
        or cap_payload.get("image_source")
        or cap_payload.get("first_frame_source")
    )
    source_mode = _normalize_goal_video_source_mode(raw_source_mode)
    if source_mode == _GOAL_VIDEO_SOURCE_AI_IMAGE:
        cap_payload["source_mode"] = _GOAL_VIDEO_SOURCE_AI_IMAGE
        cap_payload["candidate_group"] = ""
        return
    candidate_group = str(cap_payload.get("candidate_group") or cap_payload.get("candidate_group_name") or "").strip()
    if not candidate_group:
        raise HTTPException(status_code=400, detail="请选择创意成片备选素材组")
    cap_payload["source_mode"] = _GOAL_VIDEO_SOURCE_ASSET_RANDOM
    cap_payload["candidate_group"] = candidate_group


def _scheduled_capability_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("capability_id") or "").strip()


def _disabled_scheduled_capability(payload: Any) -> str:
    capability_id = _scheduled_capability_id(payload)
    return capability_id if capability_id in _DISABLED_SCHEDULED_CAPABILITIES else ""


def _normalize_schedule_type(value: str) -> str:
    schedule_type = (value or "once").strip().lower()
    if schedule_type not in _SCHEDULE_TYPES:
        raise HTTPException(status_code=400, detail="不支持的调度类型")
    return schedule_type


def _task_title(body: ScheduledTaskCreate, task_kind: str) -> str:
    title = (body.title or "").strip()
    if title:
        return title[:160]
    if task_kind == "ip_content_daily":
        return "IP日更文案"
    if task_kind == "capability":
        cid = str((body.payload or {}).get("capability_id") or "").strip()
        return f"调用能力 {cid}"[:160] if cid else "能力调用任务"
    return (body.content or "").strip()[:60] or "远程任务"


def _serialize_task(row: ScheduledTask) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "created_by_user_id": row.created_by_user_id,
        "created_by_role": row.created_by_role,
        "title": row.title,
        "task_kind": row.task_kind,
        "content": row.content,
        "payload": row.payload or {},
        "schedule_type": row.schedule_type,
        "interval_seconds": row.interval_seconds,
        "schedule_config": _schedule_config_from_payload(row.payload or {}),
        "schedule_label": _task_schedule_label(row),
        "installation_ids": row.target_installation_ids or [],
        "status": row.status,
        "next_run_at": _iso(row.next_run_at),
        "last_run_at": _iso(row.last_run_at),
        "run_count": row.run_count,
        "last_run_id": row.last_run_id,
        "last_error": row.last_error,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _serialize_run(row: ScheduledTaskRun) -> Dict[str, Any]:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "user_id": row.user_id,
        "created_by_user_id": row.created_by_user_id,
        "created_by_role": row.created_by_role,
        "installation_id": row.installation_id,
        "claimed_by_installation_id": row.claimed_by_installation_id,
        "title": row.title,
        "task_kind": row.task_kind,
        "content": row.content,
        "payload": row.payload or {},
        "status": row.status,
        "progress": row.progress or {},
        "server_side": _is_server_side_task(row),
        "result_text": row.result_text,
        "result_payload": row.result_payload or {},
        "error": row.error,
        "h5_message_id": row.h5_message_id,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "claimed_at": _iso(row.claimed_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
    }


def _is_server_side_task(task_or_run: Any) -> bool:
    return str(getattr(task_or_run, "task_kind", "") or "").strip() == "ip_content_daily"


def _task_display_kind(row: Any) -> str:
    if _is_server_side_task(row):
        return "IP日更文案"
    return ""


def _is_video_url(value: Any) -> bool:
    s = str(value or "").strip().lower().split("?", 1)[0].split("#", 1)[0]
    return s.endswith(_VIDEO_EXTS)


def _goal_video_payload_has_video(obj: Any) -> bool:
    stack: List[Any] = [obj]
    seen: set[int] = set()
    while stack:
        cur = stack.pop()
        oid = id(cur)
        if oid in seen:
            continue
        seen.add(oid)
        if isinstance(cur, dict):
            for key in ("video_asset_id", "final_asset_id"):
                if str(cur.get(key) or "").strip():
                    return True
            for item in cur.get("saved_assets") or []:
                if not isinstance(item, dict):
                    continue
                mt = str(item.get("media_type") or item.get("type") or "").strip().lower()
                if mt == "video" and str(item.get("asset_id") or item.get("id") or "").strip():
                    return True
                if not mt and any(_is_video_url(item.get(k)) for k in ("filename", "url", "source_url", "public_url")):
                    return True
            for value in cur.values():
                if _is_video_url(value):
                    return True
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(cur, list):
            for item in cur:
                if _is_video_url(item):
                    return True
                if isinstance(item, (dict, list)):
                    stack.append(item)
    return False


def _goal_video_payload_pending_reason(obj: Any) -> str:
    stack: List[Any] = [obj]
    seen: set[int] = set()
    while stack:
        cur = stack.pop()
        oid = id(cur)
        if oid in seen:
            continue
        seen.add(oid)
        if isinstance(cur, dict):
            video = cur.get("video")
            if isinstance(video, dict):
                status = str(video.get("status") or "").strip().lower()
                final = video.get("final_result")
                final_result = final.get("result") if isinstance(final, dict) and isinstance(final.get("result"), dict) else {}
                final_status = str((final_result or {}).get("status") or (final or {}).get("status") or "").strip().lower() if isinstance(final, dict) else ""
                if status in _RUNNING_STATUSES or final_status in _RUNNING_STATUSES:
                    task_id = str(video.get("task_id") or (final_result or {}).get("task_id") or "").strip()
                    return f"创意成片视频仍在生成中{('，task_id=' + task_id) if task_id else ''}"
            status = str(cur.get("status") or cur.get("state") or cur.get("task_status") or cur.get("taskStatus") or "").strip().lower()
            if status in _RUNNING_STATUSES:
                task_id = str(cur.get("task_id") or cur.get("id") or "").strip()
                return f"创意成片视频仍在生成中{('，task_id=' + task_id) if task_id else ''}"
            for value in cur.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(cur, list):
            stack.extend(v for v in cur if isinstance(v, (dict, list)))
    return ""


def _scheduled_media_urls(obj: Any, *, want: str = "") -> List[str]:
    out: List[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        if not isinstance(value, str):
            return
        for raw in re.findall(r"https?://[^\s\"'<>锛屻€傦紱;銆?\]\}]+", value):
            low = raw.lower().split("?", 1)[0].split("#", 1)[0]
            is_video = low.endswith(_VIDEO_EXTS)
            is_image = low.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"))
            if want == "video" and not is_video:
                continue
            if want == "image" and not is_image:
                continue
            if raw not in seen:
                seen.add(raw)
                out.append(raw)

    stack: List[Any] = [obj]
    seen_obj: set[int] = set()
    while stack:
        cur = stack.pop()
        oid = id(cur)
        if oid in seen_obj:
            continue
        seen_obj.add(oid)
        if isinstance(cur, dict):
            for value in cur.values():
                if isinstance(value, str):
                    add(value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(cur, list):
            for value in cur:
                if isinstance(value, str):
                    add(value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
    return out


def _scheduled_asset_ids(obj: Any, *, media_type: str = "") -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    stack: List[Any] = [obj]
    seen_obj: set[int] = set()
    while stack:
        cur = stack.pop()
        oid = id(cur)
        if oid in seen_obj:
            continue
        seen_obj.add(oid)
        if isinstance(cur, dict):
            saved = cur.get("saved_assets")
            if isinstance(saved, list):
                for item in saved:
                    if not isinstance(item, dict):
                        continue
                    mt = str(item.get("media_type") or item.get("type") or "").strip().lower()
                    if media_type and mt and mt != media_type:
                        continue
                    aid = str(item.get("asset_id") or item.get("id") or "").strip()
                    if aid and aid not in seen:
                        seen.add(aid)
                        out.append(aid)
            for key in ("image_asset_id", "asset_id", "final_asset_id"):
                aid = str(cur.get(key) or "").strip()
                if aid and (not media_type or key != "final_asset_id") and aid not in seen:
                    seen.add(aid)
                    out.append(aid)
            for value in cur.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(cur, list):
            stack.extend(v for v in cur if isinstance(v, (dict, list)))
    return out


def _partial_video_resume_payload_from_run(row: ScheduledTaskRun) -> Dict[str, Any]:
    payload = _run_result_payload(row)
    direct = payload.get("resume_payload") if isinstance(payload.get("resume_payload"), dict) else {}
    mcp_result = payload.get("mcp_result") if isinstance(payload.get("mcp_result"), dict) else {}
    resume = dict(direct or (mcp_result.get("resume_payload") if isinstance(mcp_result.get("resume_payload"), dict) else {}) or {})
    capability_id = str(payload.get("capability_id") or resume.get("capability_id") or "").strip()
    if capability_id != "goal.video.pipeline":
        raise HTTPException(status_code=400, detail="run is not a resumable video pipeline record")
    image_asset_ids = [x for x in _scheduled_asset_ids(mcp_result, media_type="image") if x]
    image_urls = _scheduled_media_urls(mcp_result, want="image")
    if resume.get("reference_asset_ids"):
        image_asset_ids = [str(x).strip() for x in resume.get("reference_asset_ids") or [] if str(x).strip()] + image_asset_ids
    if resume.get("reference_image_urls"):
        image_urls = [str(x).strip() for x in resume.get("reference_image_urls") or [] if str(x).strip()] + image_urls
    dedup_ids = list(dict.fromkeys(image_asset_ids))
    dedup_urls = list(dict.fromkeys(image_urls))
    if not dedup_ids and not dedup_urls:
        raise HTTPException(status_code=400, detail="run has no generated image to resume from")
    resume.setdefault("source_mode", "reference_image")
    resume.setdefault("goal", str((payload.get("generated") or {}).get("goal") or row.title or "").strip())
    resume["reference_asset_ids"] = dedup_ids
    resume["reference_image_urls"] = dedup_urls
    resume["resume_from_image"] = True
    resume["action"] = "run_pipeline"
    return {"capability_id": capability_id, "payload": resume}


def _normalize_scheduled_completion_error(body: ScheduledTaskCompleteIn) -> str:
    error = (body.error or "").strip()
    if error:
        return error
    payload = body.result_payload or {}
    if not isinstance(payload, dict):
        return ""
    capability_id = str(payload.get("capability_id") or "").strip()
    if capability_id != "goal.video.pipeline":
        return ""
    if _goal_video_payload_has_video(payload):
        return ""
    return _goal_video_payload_pending_reason(payload) or "创意成片视频未取得视频素材或视频链接"


def _run_result_payload(row: ScheduledTaskRun) -> Dict[str, Any]:
    payload = row.result_payload if isinstance(row.result_payload, dict) else {}
    return dict(payload or {})


def _publish_draft_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    draft = payload.get("publish_draft") if isinstance(payload, dict) else None
    return dict(draft or {}) if isinstance(draft, dict) else {}


def _set_publish_draft(row: ScheduledTaskRun, draft: Dict[str, Any]) -> Dict[str, Any]:
    payload = _run_result_payload(row)
    payload["publish_draft"] = dict(draft or {})
    row.result_payload = payload
    return payload


def _publish_event_payload(row: ScheduledTaskRun, draft: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {
        "run_id": row.id,
        "task_id": row.task_id,
        "title": row.title,
        "publish_draft": dict(draft or {}),
    }
    if extra:
        payload.update(extra)
    return payload


def _add_h5_event(db: Session, message_id: Optional[str], user_id: int, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    if not message_id:
        return
    db.add(
        H5ChatEvent(
            message_id=message_id,
            user_id=user_id,
            event_type=(event_type or "progress")[:32],
            payload=payload or {},
            created_at=datetime.utcnow(),
        )
    )


def _claim_pending_run(
    db: Session,
    *,
    run_id: str,
    user_id: int,
    installation_id: str,
    now: datetime,
) -> Optional[ScheduledTaskRun]:
    claimed_by = installation_id or "unknown"
    stmt = (
        update(ScheduledTaskRun)
        .where(
            ScheduledTaskRun.id == run_id,
            ScheduledTaskRun.user_id == user_id,
            ScheduledTaskRun.status == "pending",
            or_(ScheduledTaskRun.installation_id.is_(None), ScheduledTaskRun.installation_id == installation_id),
        )
        .values(
            status="processing",
            claimed_by_installation_id=claimed_by,
            claimed_at=now,
            started_at=now,
            updated_at=now,
        )
    )
    result = db.execute(stmt)
    if int(result.rowcount or 0) != 1:
        return None
    return db.query(ScheduledTaskRun).filter(ScheduledTaskRun.id == run_id).first()


def _create_run_for_target(db: Session, task: ScheduledTask, installation_id: Optional[str], now: datetime) -> ScheduledTaskRun:
    run_id = uuid.uuid4().hex
    server_side = _is_server_side_task(task)
    message_id = None if server_side else f"task_{run_id}"[:64]
    run = ScheduledTaskRun(
        id=run_id,
        task_id=task.id,
        user_id=task.user_id,
        created_by_user_id=task.created_by_user_id,
        created_by_role=task.created_by_role,
        installation_id=installation_id,
        title=task.title,
        task_kind=task.task_kind,
        content=task.content,
        payload=task.payload or {},
        status="pending",
        progress={"queued_at": now.isoformat()},
        h5_message_id=message_id,
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    _clear_pending_empty_for_target("run", task.user_id, installation_id)
    if message_id:
        msg_content = task.content or task.title
        h5 = H5ChatMessage(
            id=message_id,
            user_id=task.user_id,
            installation_id=installation_id,
            mode="scheduled_task",
            content=f"[定时任务] {msg_content}",
            status="pending",
            created_at=now,
            updated_at=now,
        )
        db.add(h5)
        _add_h5_event(db, message_id, task.user_id, "queued", {"task_id": task.id, "run_id": run_id, "title": task.title})
    task.run_count = int(task.run_count or 0) + 1
    task.last_run_at = now
    task.last_run_id = run_id
    task.updated_at = now
    return run


def _run_async_blocking(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("server-side scheduled task cannot be executed inside an active event loop")


def _execute_server_side_run(db: Session, run: ScheduledTaskRun, now: Optional[datetime] = None) -> None:
    now = now or datetime.utcnow()
    if run.task_kind != "ip_content_daily":
        return
    user = db.query(User).filter(User.id == run.user_id).first()
    if user is None:
        run.status = "failed"
        run.error = "用户不存在"
        run.finished_at = now
        run.updated_at = now
        return
    run.status = "processing"
    run.started_at = now
    run.progress = {"started_at": now.isoformat(), "server_side": True}
    run.updated_at = now
    db.flush()
    db.commit()
    try:
        payload = run.payload if isinstance(run.payload, dict) else {}
        result = _run_async_blocking(
            run_ip_content_daily_scheduled(
                db=db,
                current_user=user,
                options=payload,
                run_id=run.id,
            )
        )
        finished = datetime.utcnow()
        db.refresh(run)
        run.status = "completed"
        run.result_text = "IP日更文案已生成，朋友圈图片请在详情里手动触发。"
        run.result_payload = result
        run.error = None
        run.progress = {"completed_at": finished.isoformat(), "server_side": True}
        run.finished_at = finished
        run.updated_at = finished
        task = db.query(ScheduledTask).filter(ScheduledTask.id == run.task_id).first() if run.task_id else None
        if task:
            task.last_error = None
            task.updated_at = finished
        db.commit()
    except HTTPException as exc:
        failed = datetime.utcnow()
        try:
            db.refresh(run)
        except Exception:
            pass
        run.status = "failed"
        run.error = str(exc.detail or exc)
        run.progress = {"failed_at": failed.isoformat(), "server_side": True}
        run.finished_at = failed
        run.updated_at = failed
        task = db.query(ScheduledTask).filter(ScheduledTask.id == run.task_id).first() if run.task_id else None
        if task:
            task.last_error = run.error
            task.updated_at = failed
        db.commit()
    except Exception as exc:
        failed = datetime.utcnow()
        try:
            db.refresh(run)
        except Exception:
            pass
        run.status = "failed"
        run.error = str(exc)[:2000]
        run.progress = {"failed_at": failed.isoformat(), "server_side": True}
        run.finished_at = failed
        run.updated_at = failed
        task = db.query(ScheduledTask).filter(ScheduledTask.id == run.task_id).first() if run.task_id else None
        if task:
            task.last_error = run.error
            task.updated_at = failed
        db.commit()


def _enqueue_task(db: Session, task: ScheduledTask, now: Optional[datetime] = None) -> List[ScheduledTaskRun]:
    now = now or datetime.utcnow()
    disabled_capability = _disabled_scheduled_capability(task.payload or {}) if task.task_kind == "capability" else ""
    if disabled_capability:
        task.status = "paused"
        task.next_run_at = None
        task.last_error = f"定时任务能力已下线：{disabled_capability}"
        task.updated_at = now
        return []
    targets = _clean_installation_ids(task.target_installation_ids or [])
    if _is_server_side_task(task):
        targets = [""]
    if not targets:
        targets = [""]
    runs = [_create_run_for_target(db, task, target or None, now) for target in targets]
    if task.schedule_type == "once":
        task.status = "completed"
        task.next_run_at = None
    elif task.schedule_type == "interval":
        interval = max(60, int(task.interval_seconds or 3600))
        task.next_run_at = now + timedelta(seconds=interval)
    elif task.schedule_type == "daily_times":
        cfg = _schedule_config_from_payload(task.payload or {})
        times = _normalize_daily_times(cfg.get("daily_times") or [])
        offset = int(cfg.get("timezone_offset_minutes") if cfg.get("timezone_offset_minutes") is not None else 480)
        task.next_run_at = _compute_next_daily_time(
            now_utc=now,
            daily_times=times,
            timezone_offset_minutes=offset,
        )
    if _is_server_side_task(task):
        db.flush()
        for run in runs:
            _execute_server_side_run(db, run, now)
    return runs


def _reserve_due_task_for_enqueue(db: Session, task: ScheduledTask, now: datetime) -> Optional[ScheduledTask]:
    result = db.execute(
        update(ScheduledTask)
        .where(
            ScheduledTask.id == task.id,
            ScheduledTask.status == "active",
            ScheduledTask.next_run_at.isnot(None),
            ScheduledTask.next_run_at <= now,
        )
        .values(next_run_at=None, updated_at=now)
    )
    if int(result.rowcount or 0) != 1:
        return None
    return db.query(ScheduledTask).filter(ScheduledTask.id == task.id).first()


def _enqueue_due_tasks(db: Session, user_id: Optional[int] = None) -> int:
    now = datetime.utcnow()
    q = db.query(ScheduledTask).filter(
        ScheduledTask.status == "active",
        ScheduledTask.task_kind != "ip_content_daily",
        ScheduledTask.schedule_type.in_(["once", "interval", "daily_times"]),
        ScheduledTask.next_run_at.isnot(None),
        ScheduledTask.next_run_at <= now,
    )
    if user_id is not None:
        q = q.filter(ScheduledTask.user_id == user_id)
    q = q.order_by(ScheduledTask.next_run_at.asc()).limit(50).with_for_update(skip_locked=True)
    count = 0
    for candidate in q.all():
        task = _reserve_due_task_for_enqueue(db, candidate, now)
        if not task:
            continue
        _enqueue_task(db, task, now)
        count += 1
    if count:
        db.commit()
    return count


def _cancel_pending_runs_for_task(db: Session, task: ScheduledTask, now: datetime) -> int:
    rows = (
        db.query(ScheduledTaskRun)
        .filter(
            ScheduledTaskRun.task_id == task.id,
            ScheduledTaskRun.user_id == task.user_id,
            ScheduledTaskRun.status == "pending",
        )
        .limit(200)
        .all()
    )
    for row in rows:
        row.status = "cancelled"
        row.error = "任务已暂停"
        row.finished_at = now
        row.updated_at = now
        if row.h5_message_id:
            msg = db.query(H5ChatMessage).filter(H5ChatMessage.id == row.h5_message_id).first()
            if msg:
                msg.status = "cancelled"
                msg.error = "任务已暂停"
                msg.finished_at = now
                msg.updated_at = now
        _add_h5_event(db, row.h5_message_id, row.user_id, "cancelled", {"reason": "task_paused"})
    return len(rows)


def _cancel_unfinished_runs_for_task(
    db: Session,
    task: ScheduledTask,
    now: datetime,
    *,
    message: str,
    event_reason: str,
) -> int:
    rows = (
        db.query(ScheduledTaskRun)
        .filter(
            ScheduledTaskRun.task_id == task.id,
            ScheduledTaskRun.user_id == task.user_id,
            ScheduledTaskRun.status.in_(["pending", "processing"]),
        )
        .all()
    )
    for row in rows:
        row.status = "cancelled"
        row.error = message
        row.finished_at = now
        row.updated_at = now
        if row.h5_message_id:
            msg = db.query(H5ChatMessage).filter(H5ChatMessage.id == row.h5_message_id).first()
            if msg:
                msg.status = "cancelled"
                msg.error = message
                msg.finished_at = now
                msg.updated_at = now
        _add_h5_event(db, row.h5_message_id, row.user_id, "cancelled", {"reason": event_reason})
    return len(rows)


def _assert_user_task_access(row_user_id: int, current_user: User, owner_user: Optional[User] = None) -> None:
    allowed_id = int((owner_user or current_user).id)
    if int(row_user_id) != allowed_id:
        raise HTTPException(status_code=403, detail="无权访问该任务")


def _agent_task_permission(db: Session, ctx: AdminContext) -> None:
    if ctx.role != "agent":
        return
    agent = db.query(User).filter(User.id == ctx.user_id).first()
    if not agent or not getattr(agent, "agent_task_dispatch_enabled", False):
        raise HTTPException(status_code=403, detail="未开通代理商任务下发权限")


def _assert_admin_target_access(db: Session, ctx: AdminContext, target_user_id: int) -> None:
    if ctx.role == "admin":
        return
    _agent_task_permission(db, ctx)
    if target_user_id not in _agent_sub_user_ids(db, int(ctx.user_id or 0)):
        raise HTTPException(status_code=403, detail="无权给该用户下发任务")


def _delete_task_row(db: Session, task: ScheduledTask) -> int:
    now = datetime.utcnow()
    cancelled = _cancel_unfinished_runs_for_task(
        db,
        task,
        now,
        message="任务已删除",
        event_reason="task_deleted",
    )
    db.delete(task)
    return cancelled


def _delete_run_row(db: Session, row: ScheduledTaskRun) -> None:
    status = (row.status or "").strip().lower()
    if status == "processing":
        raise HTTPException(status_code=409, detail="执行中的记录不能删除，请等待完成或先删除任务")
    now = datetime.utcnow()
    if status == "pending" and row.h5_message_id:
        msg = db.query(H5ChatMessage).filter(H5ChatMessage.id == row.h5_message_id).first()
        if msg:
            msg.status = "cancelled"
            msg.error = "执行记录已删除"
            msg.finished_at = now
            msg.updated_at = now
        _add_h5_event(db, row.h5_message_id, row.user_id, "cancelled", {"reason": "run_deleted"})
    if row.task_id:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == row.task_id).first()
        if task and task.last_run_id == row.id:
            task.last_run_id = None
            task.updated_at = now
    db.delete(row)


def _create_task_row(
    db: Session,
    body: ScheduledTaskCreate,
    *,
    target_user_id: int,
    created_by_user_id: Optional[int],
    created_by_role: str,
) -> ScheduledTask:
    task_kind = _normalize_task_kind(body.task_kind)
    schedule_type = _normalize_schedule_type(body.schedule_type)
    content = (body.content or "").strip()
    payload = body.payload or {}
    if task_kind in {"openclaw_message", "chat_message"} and not content:
        raise HTTPException(status_code=400, detail="消息内容不能为空")
    if task_kind == "capability" and not str(payload.get("capability_id") or "").strip():
        raise HTTPException(status_code=400, detail="能力调用任务需要 payload.capability_id")
    if task_kind == "douyin_leads" and not str(payload.get("action") or "").strip():
        raise HTTPException(status_code=400, detail="抖音获客任务需要 payload.action")
    disabled_capability = _disabled_scheduled_capability(payload) if task_kind == "capability" else ""
    if disabled_capability:
        raise HTTPException(status_code=400, detail=f"定时任务能力已下线：{disabled_capability}")
    if task_kind == "ip_content_daily":
        payload = dict(payload)
        if not int(payload.get("template_id") or 0) and not payload.get("keyword_ids") and not payload.get("competitor_ids") and not payload.get("memory_docs"):
            raise HTTPException(status_code=400, detail="IP日更文案任务需要选择模板、关键词、同行账号或记忆资料")
    if task_kind == "capability":
        payload = dict(payload)
        _normalize_goal_video_task_payload(payload)
    interval_seconds = None
    now = datetime.utcnow()
    tz_offset = int(body.timezone_offset_minutes if body.timezone_offset_minutes is not None else 480)
    start_at_utc = None if schedule_type == "daily_times" else _parse_client_datetime(body.start_at, tz_offset)
    schedule_config: Dict[str, Any] = {
        "timezone_offset_minutes": tz_offset,
    }
    if start_at_utc:
        schedule_config["start_at"] = body.start_at
        schedule_config["start_at_utc"] = start_at_utc.isoformat()
    next_run_at = start_at_utc or now
    if schedule_type == "interval":
        interval_seconds = max(60, min(int(body.interval_seconds or 3600), 366 * 24 * 3600))
    elif schedule_type == "daily_times":
        daily_times = _normalize_daily_times(body.daily_times or payload.get("daily_times") or [])
        schedule_config["daily_times"] = daily_times
        next_run_at = _compute_next_daily_time(
            now_utc=now,
            daily_times=daily_times,
            timezone_offset_minutes=tz_offset,
        )
    payload = dict(payload)
    payload["schedule_config"] = schedule_config
    task = ScheduledTask(
        user_id=target_user_id,
        created_by_user_id=created_by_user_id,
        created_by_role=created_by_role,
        title=_task_title(body, task_kind),
        task_kind=task_kind,
        content=content,
        payload=payload,
        schedule_type=schedule_type,
        interval_seconds=interval_seconds,
        target_installation_ids=_clean_installation_ids(body.installation_ids),
        status="active",
        next_run_at=next_run_at,
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    db.flush()
    if not _is_server_side_task(task) and task.next_run_at and task.next_run_at <= now:
        _enqueue_task(db, task, now)
    db.commit()
    db.refresh(task)
    return task


@router.post("/api/scheduled-tasks/tasks", summary="创建定时/一次性任务")
def create_scheduled_task(
    body: ScheduledTaskCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    requested_kind = _normalize_task_kind(body.task_kind)
    xi = _header_installation_id(request)
    if not xi and requested_kind != "ip_content_daily":
        raise HTTPException(status_code=400, detail="missing current installation id")
    if xi:
        ensure_installation_slot(db, owner_user.id, xi)
    if requested_kind == "ip_content_daily":
        body.installation_ids = []
    else:
        body.installation_ids = [xi]
    task = _create_task_row(
        db,
        body,
        target_user_id=owner_user.id,
        created_by_user_id=current_user.id,
        created_by_role="user",
    )
    runs_payload = []
    if task.last_run_id:
        run = (
            db.query(ScheduledTaskRun)
            .filter(ScheduledTaskRun.id == task.last_run_id, ScheduledTaskRun.user_id == owner_user.id)
            .first()
        )
        if run:
            runs_payload.append(_serialize_run(run))
    return {"ok": True, "task": _serialize_task(task), "runs": runs_payload}


@router.get("/api/scheduled-tasks/tasks", summary="任务定义列表")
def list_scheduled_tasks(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    rows = (
        db.query(ScheduledTask)
        .filter(ScheduledTask.user_id == owner_user.id)
        .order_by(ScheduledTask.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"ok": True, "tasks": [_serialize_task(r) for r in rows]}


@router.get("/api/scheduled-tasks/assets/creative-candidate-groups", summary="H5 创意成片备选素材组列表")
def list_h5_creative_candidate_groups(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    rows = db.query(Asset).filter(Asset.user_id == owner_user.id, Asset.media_type == "image").all()
    groups: Dict[str, int] = {}
    for row in rows:
        name = _creative_candidate_group(row.meta)
        if name:
            groups[name] = groups.get(name, 0) + 1
    return {
        "ok": True,
        "groups": [
            {"name": name, "count": count}
            for name, count in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }


@router.get("/api/scheduled-tasks/publish/accounts", summary="H5 定时任务可用发布账号")
def list_h5_scheduled_publish_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    rows = (
        db.query(PublishAccount)
        .filter(PublishAccount.user_id == owner_user.id)
        .order_by(PublishAccount.created_at.desc())
        .all()
    )
    return {
        "ok": True,
        "accounts": [
            {
                "id": row.id,
                "platform": row.platform,
                "platform_name": SUPPORTED_PLATFORMS.get(row.platform, {}).get("name", row.platform),
                "nickname": row.nickname,
                "status": row.status,
            }
            for row in rows
        ],
        "platforms": [{"id": key, "name": value["name"]} for key, value in SUPPORTED_PLATFORMS.items()],
    }


@router.patch("/api/scheduled-tasks/tasks/{task_id}", summary="更新任务状态")
def patch_scheduled_task(
    task_id: int,
    body: ScheduledTaskPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _assert_user_task_access(task.user_id, current_user, owner_user)
    if body.status:
        status = body.status.strip().lower()
        if status not in {"active", "paused", "cancelled"}:
            raise HTTPException(status_code=400, detail="不支持的状态")
        now = datetime.utcnow()
        task.status = status
        if status in {"paused", "cancelled"}:
            _cancel_pending_runs_for_task(db, task, now)
        if status == "active" and not task.next_run_at:
            if task.schedule_type == "interval":
                task.next_run_at = now
            elif task.schedule_type == "daily_times":
                cfg = _schedule_config_from_payload(task.payload or {})
                task.next_run_at = _compute_next_daily_time(
                    now_utc=now,
                    daily_times=_normalize_daily_times(cfg.get("daily_times") or []),
                    timezone_offset_minutes=int(cfg.get("timezone_offset_minutes") if cfg.get("timezone_offset_minutes") is not None else 480),
                )
        task.updated_at = now
    db.commit()
    db.refresh(task)
    return {"ok": True, "task": _serialize_task(task)}


@router.delete("/api/scheduled-tasks/tasks/{task_id}", summary="删除任务")
def delete_scheduled_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _assert_user_task_access(task.user_id, current_user, owner_user)
    cancelled = _delete_task_row(db, task)
    db.commit()
    return {"ok": True, "deleted": True, "cancelled_runs": cancelled}


@router.post("/api/scheduled-tasks/tasks/{task_id}/run-now", summary="立即执行任务")
def run_scheduled_task_now(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _assert_user_task_access(task.user_id, current_user, owner_user)
    runs = _enqueue_task(db, task, datetime.utcnow())
    if task.schedule_type in {"interval", "daily_times"} and task.status != "cancelled":
        task.status = "active"
    db.commit()
    return {"ok": True, "runs": [_serialize_run(r) for r in runs]}


@router.get("/api/scheduled-tasks/runs", summary="执行记录列表")
def list_scheduled_task_runs(
    limit: int = Query(80, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    _enqueue_due_tasks(db, owner_user.id)
    rows = (
        db.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.user_id == owner_user.id)
        .order_by(ScheduledTaskRun.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"ok": True, "runs": [_serialize_run(r) for r in rows]}


@router.get("/api/scheduled-tasks/runs/{run_id}", summary="执行记录详情")
def get_scheduled_task_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    row = _run_for_user(db, run_id, owner_user.id)
    return {"ok": True, "run": _serialize_run(row)}


@router.delete("/api/scheduled-tasks/runs/{run_id}", summary="删除执行记录")
def delete_scheduled_task_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    row = _run_for_user(db, run_id, owner_user.id)
    _delete_run_row(db, row)
    db.commit()
    return {"ok": True, "deleted": True, "run_id": run_id}


@router.get("/api/scheduled-tasks/pending", summary="本地 online 领取待执行任务")
def pending_scheduled_task_runs(
    request: Request,
    limit: int = Query(2, ge=1, le=10),
    current_user_id: int = Depends(get_current_user_id_from_token),
    db: Session = Depends(get_db),
):
    xi = _header_installation_id(request)
    pending_key = _pending_cache_key("run", current_user_id, xi)
    if _pending_empty_recent(pending_key, _RUN_PENDING_EMPTY_CACHE_SECONDS):
        return {"ok": True, "items": [], "throttled": True}
    if xi:
        _touch_installation_slot_lazy(db, current_user_id, xi)
    _enqueue_due_tasks(db, current_user_id)

    now = datetime.utcnow()
    stale_cutoff = now - timedelta(minutes=10)
    stale_rows = (
        db.query(ScheduledTaskRun)
        .filter(
            ScheduledTaskRun.user_id == current_user_id,
            ScheduledTaskRun.status == "processing",
            ScheduledTaskRun.task_kind != "ip_content_daily",
            ScheduledTaskRun.claimed_at.isnot(None),
            ScheduledTaskRun.claimed_at < stale_cutoff,
        )
        .limit(20)
        .all()
    )
    for row in stale_rows:
        row.status = "pending"
        row.claimed_by_installation_id = None
        row.claimed_at = None
        row.updated_at = now
        _add_h5_event(db, row.h5_message_id, row.user_id, "queued", {"reason": "processing_timeout_requeued"})

    candidates = (
        db.query(ScheduledTaskRun)
        .with_for_update(skip_locked=True)
        .filter(ScheduledTaskRun.user_id == current_user_id, ScheduledTaskRun.status == "pending")
        .filter(ScheduledTaskRun.task_kind != "ip_content_daily")
        .filter(or_(ScheduledTaskRun.installation_id.is_(None), ScheduledTaskRun.installation_id == xi))
        .order_by(ScheduledTaskRun.created_at.asc())
        .limit(limit)
        .all()
    )
    rows: List[ScheduledTaskRun] = []
    for candidate in candidates:
        row = _claim_pending_run(db, run_id=candidate.id, user_id=current_user_id, installation_id=xi, now=now)
        if not row:
            continue
        if row.h5_message_id:
            msg = db.query(H5ChatMessage).filter(H5ChatMessage.id == row.h5_message_id).first()
            if msg:
                msg.status = "processing"
                msg.claimed_by_installation_id = xi or "unknown"
                msg.claimed_at = now
                msg.updated_at = now
        _add_h5_event(db, row.h5_message_id, row.user_id, "claimed", {"installation_id": xi or ""})
        rows.append(row)
    db.commit()
    if rows:
        _clear_pending_empty(pending_key)
    else:
        _mark_pending_empty(pending_key, _RUN_PENDING_EMPTY_CACHE_SECONDS)
    return {"ok": True, "items": [_serialize_run(r) for r in rows]}


@router.post("/api/scheduled-tasks/runs/{run_id}/publish-request", summary="提交定时任务结果发布请求")
def request_scheduled_task_publish(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    row = _run_for_user(db, run_id, owner_user.id)
    if row.status != "completed":
        raise HTTPException(status_code=409, detail="任务完成后才能发布")
    payload = _run_result_payload(row)
    draft = _publish_draft_from_payload(payload)
    if not draft:
        raise HTTPException(status_code=400, detail="该记录没有可发布草稿")
    if not str(draft.get("asset_id") or "").strip():
        raise HTTPException(status_code=400, detail="发布草稿缺少素材 asset_id")
    if not str(draft.get("account_id") or "").strip() and not str(draft.get("account_nickname") or "").strip():
        raise HTTPException(status_code=400, detail="发布草稿缺少发布账号")
    status = str(draft.get("status") or "ready").strip().lower()
    if status == "published":
        return {"ok": True, "status": "published", "run": _serialize_run(row)}
    now = datetime.utcnow()
    draft["status"] = "pending"
    draft["requested_at"] = now.isoformat()
    draft.pop("error", None)
    _set_publish_draft(row, draft)
    row.updated_at = now
    _add_h5_event(db, row.h5_message_id, row.user_id, "publish_pending", _publish_event_payload(row, draft))
    _clear_pending_empty_for_target("publish", row.user_id, row.installation_id)
    db.commit()
    db.refresh(row)
    return {"ok": True, "status": "pending", "run": _serialize_run(row)}


@router.post("/api/scheduled-tasks/runs/{run_id}/resume-video", summary="只生成图片的创意成片记录补发视频")
def resume_scheduled_task_video_from_image(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    row = _run_for_user(db, run_id, owner_user.id)
    if row.status not in _FINAL_STATUSES:
        raise HTTPException(status_code=409, detail="任务仍在执行中，不能补发")
    resume_payload = _partial_video_resume_payload_from_run(row)
    now = datetime.utcnow()
    row.payload = resume_payload
    row.status = "pending"
    row.progress = {"queued_at": now.isoformat(), "resume_from_run_id": run_id, "resume_from_image": True}
    row.error = None
    row.result_text = None
    row.result_payload = {}
    row.claimed_by_installation_id = None
    row.claimed_at = None
    row.started_at = None
    row.finished_at = None
    row.updated_at = now
    if row.h5_message_id:
        msg = db.query(H5ChatMessage).filter(H5ChatMessage.id == row.h5_message_id).first()
        if msg:
            msg.status = "pending"
            msg.reply_text = None
            msg.error = None
            msg.claimed_by_installation_id = None
            msg.claimed_at = None
            msg.finished_at = None
            msg.updated_at = now
        _add_h5_event(db, row.h5_message_id, row.user_id, "queued", {"run_id": run_id, "resume_from_image": True})
    _clear_pending_empty_for_target("run", row.user_id, row.installation_id)
    db.commit()
    db.refresh(row)
    return {"ok": True, "status": "pending", "run": _serialize_run(row)}


@router.get("/api/scheduled-tasks/publish/pending", summary="本地 online 领取待发布草稿")
def pending_scheduled_publish_requests(
    request: Request,
    limit: int = Query(1, ge=1, le=5),
    current_user_id: int = Depends(get_current_user_id_from_token),
    db: Session = Depends(get_db),
):
    xi = _header_installation_id(request)
    pending_key = _pending_cache_key("publish", current_user_id, xi)
    if _pending_empty_recent(pending_key, _PUBLISH_PENDING_EMPTY_CACHE_SECONDS):
        return {"ok": True, "items": [], "throttled": True}
    if xi:
        _touch_installation_slot_lazy(db, current_user_id, xi)
    now = datetime.utcnow()
    rows = (
        db.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.user_id == current_user_id, ScheduledTaskRun.status == "completed")
        .filter(or_(ScheduledTaskRun.installation_id.is_(None), ScheduledTaskRun.installation_id == xi))
        .order_by(ScheduledTaskRun.finished_at.asc(), ScheduledTaskRun.created_at.asc())
        .limit(200)
        .all()
    )
    picked: List[ScheduledTaskRun] = []
    for row in rows:
        draft = _publish_draft_from_payload(_run_result_payload(row))
        status = str(draft.get("status") or "").strip().lower()
        if status != "pending":
            continue
        draft["status"] = "processing"
        draft["claimed_by_installation_id"] = xi or "unknown"
        draft["processing_at"] = now.isoformat()
        _set_publish_draft(row, draft)
        row.updated_at = now
        _add_h5_event(db, row.h5_message_id, row.user_id, "publish_claimed", _publish_event_payload(row, draft))
        picked.append(row)
        if len(picked) >= limit:
            break
    db.commit()
    if picked:
        _clear_pending_empty(pending_key)
    else:
        _mark_pending_empty(pending_key, _PUBLISH_PENDING_EMPTY_CACHE_SECONDS)
    return {"ok": True, "items": [_serialize_run(r) for r in picked]}


def _run_for_user(db: Session, run_id: str, user_id: int) -> ScheduledTaskRun:
    row = db.query(ScheduledTaskRun).filter(ScheduledTaskRun.id == run_id, ScheduledTaskRun.user_id == user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    return row


def _assert_worker_can_update(row: ScheduledTaskRun, xi: str) -> None:
    claimed = (row.claimed_by_installation_id or "").strip()
    if claimed and xi and claimed != xi:
        raise HTTPException(status_code=409, detail="任务已由其他设备处理")
    if row.status in _FINAL_STATUSES:
        raise HTTPException(status_code=409, detail="任务已结束")


@router.post("/api/scheduled-tasks/runs/{run_id}/event", summary="本地 online 提交任务进度")
def submit_scheduled_task_event(
    run_id: str,
    body: ScheduledTaskEventIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _run_for_user(db, run_id, current_user.id)
    _assert_worker_can_update(row, _header_installation_id(request))
    now = datetime.utcnow()
    row.progress = body.payload or {}
    if body.type == "heartbeat" and row.status == "processing":
        row.claimed_at = now
    row.updated_at = now
    _add_h5_event(db, row.h5_message_id, row.user_id, body.type, body.payload)
    db.commit()
    return {"ok": True}


@router.post("/api/scheduled-tasks/runs/{run_id}/complete", summary="本地 online 回传任务结果")
def complete_scheduled_task_run(
    run_id: str,
    body: ScheduledTaskCompleteIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _run_for_user(db, run_id, current_user.id)
    _assert_worker_can_update(row, _header_installation_id(request))
    now = datetime.utcnow()
    error = _normalize_scheduled_completion_error(body)
    result_text = (body.result_text or "").strip()
    row.status = "failed" if error else "completed"
    row.result_text = result_text or None
    row.result_payload = body.result_payload or {}
    row.error = error or None
    row.finished_at = now
    row.updated_at = now
    if row.task_id:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == row.task_id).first()
        if task:
            task.last_error = error or None
            task.updated_at = now
    if row.h5_message_id:
        msg = db.query(H5ChatMessage).filter(H5ChatMessage.id == row.h5_message_id).first()
        if msg:
            msg.status = row.status
            msg.reply_text = result_text or None
            msg.error = error or None
            msg.finished_at = now
            msg.updated_at = now
    if error:
        _add_h5_event(db, row.h5_message_id, row.user_id, "error", {"error": error, **(body.result_payload or {})})
    else:
        _add_h5_event(db, row.h5_message_id, row.user_id, "final", {"reply_text": result_text, **(body.result_payload or {})})
    db.commit()
    return {"ok": True, "status": row.status}


@router.post("/api/scheduled-tasks/runs/{run_id}/publish-complete", summary="本地 online 回传发布结果")
def complete_scheduled_task_publish(
    run_id: str,
    body: ScheduledPublishCompleteIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _run_for_user(db, run_id, current_user.id)
    if row.status != "completed":
        raise HTTPException(status_code=409, detail="任务未完成，不能回传发布结果")
    draft = _publish_draft_from_payload(_run_result_payload(row))
    if not draft:
        raise HTTPException(status_code=400, detail="该记录没有可发布草稿")
    claimed = str(draft.get("claimed_by_installation_id") or "").strip()
    xi = _header_installation_id(request)
    if claimed and xi and claimed != xi:
        raise HTTPException(status_code=409, detail="发布任务已由其他设备处理")
    now = datetime.utcnow()
    error = (body.error or "").strip()
    result = body.publish_result if isinstance(body.publish_result, dict) else {}
    draft["status"] = "failed" if error else "published"
    draft["completed_at"] = now.isoformat()
    draft["publish_result"] = result
    if error:
        draft["error"] = error[:500]
    else:
        draft.pop("error", None)
        draft["published_at"] = now.isoformat()
    _set_publish_draft(row, draft)
    row.updated_at = now
    _add_h5_event(
        db,
        row.h5_message_id,
        row.user_id,
        "publish_result",
        _publish_event_payload(row, draft, {"error": error, "publish_result": result}),
    )
    db.commit()
    return {"ok": True, "status": draft["status"], "run_id": run_id}


@router.post("/admin/api/scheduled-tasks", summary="管理员/代理商下发任务")
def admin_create_scheduled_task(
    body: ScheduledTaskCreate,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    target_user_id = int(body.user_id or 0)
    if target_user_id <= 0:
        raise HTTPException(status_code=400, detail="缺少目标用户")
    _assert_admin_target_access(db, ctx, target_user_id)
    if not db.query(User.id).filter(User.id == target_user_id).first():
        raise HTTPException(status_code=404, detail="用户不存在")
    task = _create_task_row(
        db,
        body,
        target_user_id=target_user_id,
        created_by_user_id=ctx.user_id,
        created_by_role=ctx.role,
    )
    return {"ok": True, "task": _serialize_task(task)}


@router.get("/admin/api/scheduled-tasks", summary="管理员/代理商查看任务")
def admin_list_scheduled_tasks(
    user_id: int = Query(..., ge=1),
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    _assert_admin_target_access(db, ctx, user_id)
    rows = (
        db.query(ScheduledTask)
        .filter(ScheduledTask.user_id == user_id)
        .order_by(ScheduledTask.created_at.desc())
        .limit(80)
        .all()
    )
    return {"ok": True, "tasks": [_serialize_task(r) for r in rows]}


@router.delete("/admin/api/scheduled-tasks/{task_id}", summary="管理员/代理商删除任务")
def admin_delete_scheduled_task(
    task_id: int,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _assert_admin_target_access(db, ctx, task.user_id)
    cancelled = _delete_task_row(db, task)
    db.commit()
    return {"ok": True, "deleted": True, "cancelled_runs": cancelled}


@router.get("/admin/api/scheduled-tasks/runs", summary="管理员/代理商查看执行记录")
def admin_list_scheduled_task_runs(
    user_id: int = Query(..., ge=1),
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    _assert_admin_target_access(db, ctx, user_id)
    rows = (
        db.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.user_id == user_id)
        .order_by(ScheduledTaskRun.created_at.desc())
        .limit(80)
        .all()
    )
    return {"ok": True, "runs": [_serialize_run(r) for r in rows]}


@router.delete("/admin/api/scheduled-tasks/runs/{run_id}", summary="管理员/代理商删除执行记录")
def admin_delete_scheduled_task_run(
    run_id: str,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    row = db.query(ScheduledTaskRun).filter(ScheduledTaskRun.id == run_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    _assert_admin_target_access(db, ctx, row.user_id)
    _delete_run_row(db, row)
    db.commit()
    return {"ok": True, "deleted": True, "run_id": run_id}


@router.get("/admin/api/scheduled-tasks/devices", summary="管理员/代理商查看用户设备")
def admin_list_task_devices(
    user_id: int = Query(..., ge=1),
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    _assert_admin_target_access(db, ctx, user_id)
    now = datetime.utcnow()
    rows = (
        db.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.user_id == user_id)
        .order_by(H5ChatDevicePresence.last_seen_at.desc())
        .limit(50)
        .all()
    )
    return {
        "ok": True,
        "devices": [
            {
                "installation_id": r.installation_id,
                "display_name": r.display_name,
                "last_seen_at": _iso(r.last_seen_at),
                "online": ((now - r.last_seen_at).total_seconds() <= 20) if r.last_seen_at else False,
            }
            for r in rows
        ],
    }


@router.post("/api/scheduled-tasks/agent/tasks", summary="代理商下发任务")
def agent_create_scheduled_task(
    body: ScheduledTaskCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not getattr(current_user, "is_agent", False):
        raise HTTPException(status_code=403, detail="非代理商，无权下发任务")
    if not getattr(current_user, "agent_task_dispatch_enabled", False):
        raise HTTPException(status_code=403, detail="未开通代理商任务下发权限")
    target_user_id = int(body.user_id or 0)
    if target_user_id <= 0:
        raise HTTPException(status_code=400, detail="缺少目标用户")
    if target_user_id not in _agent_sub_user_ids(db, int(current_user.id)):
        raise HTTPException(status_code=403, detail="无权给该用户下发任务")
    task = _create_task_row(
        db,
        body,
        target_user_id=target_user_id,
        created_by_user_id=current_user.id,
        created_by_role="agent",
    )
    return {"ok": True, "task": _serialize_task(task)}


@router.get("/api/scheduled-tasks/agent/devices", summary="代理商查看下级用户设备")
def agent_list_task_devices(
    user_id: int = Query(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not getattr(current_user, "is_agent", False):
        raise HTTPException(status_code=403, detail="非代理商，无权访问")
    if not getattr(current_user, "agent_task_dispatch_enabled", False):
        raise HTTPException(status_code=403, detail="未开通代理商任务下发权限")
    if user_id not in _agent_sub_user_ids(db, int(current_user.id)):
        raise HTTPException(status_code=403, detail="无权查看该用户设备")
    now = datetime.utcnow()
    rows = (
        db.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.user_id == user_id)
        .order_by(H5ChatDevicePresence.last_seen_at.desc())
        .limit(50)
        .all()
    )
    return {
        "ok": True,
        "devices": [
            {
                "installation_id": r.installation_id,
                "display_name": r.display_name,
                "last_seen_at": _iso(r.last_seen_at),
                "online": ((now - r.last_seen_at).total_seconds() <= 20) if r.last_seen_at else False,
            }
            for r in rows
        ],
    }
