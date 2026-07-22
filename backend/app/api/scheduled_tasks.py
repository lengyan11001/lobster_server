from __future__ import annotations

import asyncio
import copy
import os
import uuid
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, update
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
    CreativeGenerationJob,
    User,
    UserInstallation,
    IPContentDraftRecord,
    IPContentScheduleTemplate,
)
from .publish import SUPPORTED_PLATFORMS
from .admin import AdminContext, _agent_sub_user_ids, _verify_admin_token
from .auth import get_current_user, get_current_user_id_from_token
from .ip_content_studio import _draft_record_payload, run_ip_content_daily_scheduled
from .lead_collection_templates import run_lead_collection_templates_scheduled
from .linkedin_mining import (
    create_linkedin_mining_job_from_payload,
    linkedin_mining_job_payload,
    run_linkedin_mining_job_to_completion,
)
from .social_leads import (
    create_social_leads_job_from_payload,
    run_social_leads_job_to_completion,
    social_leads_job_payload,
)
from .wechat_channels_transcript import run_wechat_channels_transcript_payload_to_completion
from .installation_slots import ensure_installation_slot
from .mobile_identity import online_user_for_mobile_user
from ..services.runtime_cache import cache_delete, cache_flag_recent, cache_mark_flag

router = APIRouter()

_TASK_KINDS = {"openclaw_message", "chat_message", "capability", "ip_content_daily", "lead_collection_templates", "social_leads", "linkedin_mining", "wechat_channels_transcript", "douyin_leads", "client_workflow"}
_SERVER_SIDE_TASK_KINDS = {"ip_content_daily", "lead_collection_templates", "social_leads", "linkedin_mining", "wechat_channels_transcript"}
_SCHEDULE_TYPES = {"once", "interval", "daily_times"}
_FINAL_STATUSES = {"completed", "failed", "cancelled"}
_MAX_TARGET_DEVICES = 20
_VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v", ".avi")
_RUNNING_STATUSES = {"running", "processing", "pending", "queued", "waiting"}
_GOAL_VIDEO_SOURCE_AI_IMAGE = "ai_image"
_GOAL_VIDEO_SOURCE_REFERENCE_IMAGE = "reference_image"
_GOAL_VIDEO_SOURCE_ASSET_RANDOM = "asset_random"
_DISABLED_SCHEDULED_CAPABILITIES = {"create.video.pipeline", "create.ppt.pipeline"}
_PENDING_INSTALLATION_TOUCH_MIN_SECONDS = 60
_RUN_PENDING_EMPTY_CACHE_SECONDS = 20.0
_PUBLISH_PENDING_EMPTY_CACHE_SECONDS = 20.0
_pending_empty_cache: Dict[str, float] = {}
_PERSONAL_DEFAULT_TEMPLATE_NAME = "\u4e2a\u4eba\u9ed8\u8ba4\u914d\u7f6e"
_LOCAL_BESTSELLER_ACTIONS = {"local_bestseller_plan", "local_bestseller_scene_batch", "local_bestseller_daily_video"}
_SERIAL_CLIENT_TASK_KINDS = {"douyin_leads"}
_WECHAT_MOMENTS_PLATFORM = "wechat_moments"
_WECHAT_MOMENTS_ACCOUNT_ID = "pc-wechat-default"
_WECHAT_MOMENTS_PLATFORM_NAME = "微信朋友圈"


def _server_side_timeout_seconds(task_kind: str) -> float:
    defaults = {
        "ip_content_daily": ("LOBSTER_IP_CONTENT_SCHEDULE_TIMEOUT_SEC", 600.0),
        "lead_collection_templates": ("LOBSTER_LEAD_COLLECTION_SCHEDULE_TIMEOUT_SEC", 1800.0),
        "social_leads": ("LOBSTER_SOCIAL_LEADS_SCHEDULE_TIMEOUT_SEC", 1800.0),
        "linkedin_mining": ("LOBSTER_LINKEDIN_MINING_SCHEDULE_TIMEOUT_SEC", 1800.0),
        "wechat_channels_transcript": ("LOBSTER_WECHAT_TRANSCRIPT_SCHEDULE_TIMEOUT_SEC", 1800.0),
    }
    env_name, default_value = defaults.get(task_kind, ("LOBSTER_SERVER_SIDE_SCHEDULE_TIMEOUT_SEC", 900.0))
    raw = os.environ.get(env_name) or os.environ.get("LOBSTER_SERVER_SIDE_SCHEDULE_TIMEOUT_SEC") or ""
    if raw:
        try:
            return max(60.0, float(raw))
        except (TypeError, ValueError):
            pass
    return default_value


def _merge_run_progress(run: ScheduledTaskRun, patch: Dict[str, Any]) -> Dict[str, Any]:
    base = run.progress if isinstance(run.progress, dict) else {}
    merged = dict(base)
    merged.update(patch)
    return merged


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
    title: Optional[str] = Field(None, max_length=160)
    schedule_type: Optional[str] = None
    interval_seconds: Optional[int] = None
    start_at: Optional[str] = None
    daily_times: Any = None
    timezone_offset_minutes: Optional[int] = None


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


class ScheduledPublishRequestIn(BaseModel):
    publish_draft: Dict[str, Any] = Field(default_factory=dict)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="seconds") + "Z"


def _clean_profile_text(value: Any, limit: int = 300) -> str:
    return str(value or "").strip()[:limit]


def _clean_profile_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_profile_text(*values: Any, limit: int = 300) -> str:
    for value in values:
        text = _clean_profile_text(value, limit)
        if text:
            return text
    return ""


def _personal_default_requirements(db: Session, user_id: int) -> Dict[str, Any]:
    row = (
        db.query(IPContentScheduleTemplate)
        .filter(
            IPContentScheduleTemplate.user_id == user_id,
            IPContentScheduleTemplate.name == _PERSONAL_DEFAULT_TEMPLATE_NAME,
            IPContentScheduleTemplate.status == "active",
        )
        .order_by(IPContentScheduleTemplate.updated_at.desc(), IPContentScheduleTemplate.id.desc())
        .first()
    )
    return row.requirements if row and isinstance(row.requirements, dict) else {}


def _local_bestseller_profile_from_persona(requirements: Dict[str, Any]) -> Dict[str, str]:
    req = requirements if isinstance(requirements, dict) else {}
    basic = _clean_profile_dict(req.get("basic_profile"))
    business = _clean_profile_dict(req.get("business_description"))
    profile = {
        "name": _first_profile_text(req.get("profile_name"), req.get("name"), basic.get("name")),
        "nickname": _first_profile_text(req.get("nickname"), req.get("short_video_nickname"), basic.get("nickname"), req.get("profile_name"), basic.get("name")),
        "gender": _first_profile_text(req.get("gender"), req.get("sex"), basic.get("gender"), basic.get("sex")),
        "identity": _first_profile_text(req.get("identity"), req.get("role"), basic.get("role")),
        "industry": _first_profile_text(req.get("industry"), req.get("product"), business.get("product"), req.get("share_topic"), basic.get("share_topic"), req.get("role"), basic.get("role")),
        "city": _first_profile_text(req.get("current_city"), req.get("city"), basic.get("current_city"), basic.get("city")),
        "province": _first_profile_text(req.get("current_province"), req.get("province"), basic.get("current_province"), basic.get("province")),
        "hometown": _first_profile_text(req.get("hometown"), basic.get("hometown")),
        "age_label": _first_profile_text(req.get("birth_era"), basic.get("birth_era")),
        "target_age": _first_profile_text(req.get("target_customer"), business.get("target_customer")),
        "style": _first_profile_text(req.get("video_style"), basic.get("video_style"), req.get("style"), basic.get("style")),
        "photo_asset_id": _first_profile_text(
            req.get("profile_photo_asset_id"),
            req.get("photo_asset_id"),
            req.get("portrait_asset_id"),
            req.get("image_asset_id"),
            basic.get("profile_photo_asset_id"),
            basic.get("photo_asset_id"),
            basic.get("portrait_asset_id"),
            basic.get("image_asset_id"),
        ),
        "photo_url": _first_profile_text(
            req.get("profile_photo_url"),
            req.get("photo_url"),
            req.get("portrait_url"),
            req.get("image_url"),
            basic.get("profile_photo_url"),
            basic.get("photo_url"),
            basic.get("portrait_url"),
            basic.get("image_url"),
            limit=1000,
        ),
    }
    return {key: value for key, value in profile.items() if value}


def _missing_local_bestseller_profile_fields(profile: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    if not _clean_profile_text(profile.get("gender")):
        missing.append("性别")
    if not _clean_profile_text(profile.get("identity")):
        missing.append("你是做什么的")
    if not _clean_profile_text(profile.get("industry")):
        missing.append("业务/产品或主要分享内容")
    if not _clean_profile_text(profile.get("province")):
        missing.append("现居省份")
    if not _clean_profile_text(profile.get("city")):
        missing.append("现居城市")
    if not _clean_profile_text(profile.get("hometown")):
        missing.append("籍贯")
    if not _clean_profile_text(profile.get("age_label")):
        missing.append("出生年代")
    if not _clean_profile_text(profile.get("target_age")):
        missing.append("想卖给谁/目标客户")
    if not _clean_profile_text(profile.get("style")):
        missing.append("视频风格")
    if not (_clean_profile_text(profile.get("photo_asset_id")) or _clean_profile_text(profile.get("photo_url"))):
        missing.append("人物照片")
    return missing


def _enrich_local_bestseller_workflow_payload(
    db: Session,
    *,
    payload: Dict[str, Any],
    target_user_id: int,
    now: datetime,
) -> Dict[str, Any]:
    action = _clean_profile_text(payload.get("action"), 80)
    if action not in _LOCAL_BESTSELLER_ACTIONS:
        return payload
    out = dict(payload)
    out["action"] = "local_bestseller_daily_video"
    params = out.get("params") if isinstance(out.get("params"), dict) else {}
    params = dict(params)
    persona_profile = _local_bestseller_profile_from_persona(_personal_default_requirements(db, target_user_id))
    existing_profile = params.get("profile") if isinstance(params.get("profile"), dict) else {}
    merged_profile = {key: _clean_profile_text(value, 1000) for key, value in existing_profile.items() if _clean_profile_text(value, 1000)}
    # IP persona wins for employee workflows; old templates often carried placeholder profile values.
    merged_profile.update(persona_profile)
    params["profile"] = merged_profile
    try:
        days = int(params.get("days") or 30)
    except Exception:
        days = 30
    params["days"] = max(1, min(days, 30))
    params.setdefault("day_mode", "workflow_elapsed")
    params["missing_profile_fields"] = _missing_local_bestseller_profile_fields(merged_profile)
    out["params"] = params
    h5_context = out.get("h5_context") if isinstance(out.get("h5_context"), dict) else {}
    h5_context = dict(h5_context)
    h5_context.setdefault("workflow_started_at", _iso(now))
    h5_context.setdefault("workflow_day_start", _iso(now))
    h5_context["persona_source"] = "ip_persona_default"
    out["h5_context"] = h5_context
    return out


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
    if raw in {"reference_image", "reference", "resume_image", "resume_from_image", "existing_image"}:
        return _GOAL_VIDEO_SOURCE_REFERENCE_IMAGE
    return _GOAL_VIDEO_SOURCE_ASSET_RANDOM


def _goal_video_reference_present(payload: Dict[str, Any]) -> bool:
    if str(payload.get("reference_image_url") or "").strip():
        return True
    if str(payload.get("reference_asset_id") or "").strip():
        return True
    for key in ("reference_image_urls", "reference_asset_ids"):
        values = payload.get(key)
        if isinstance(values, list) and any(str(item or "").strip() for item in values):
            return True
    return False


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
    if source_mode == _GOAL_VIDEO_SOURCE_REFERENCE_IMAGE:
        if not _goal_video_reference_present(cap_payload):
            raise HTTPException(status_code=400, detail="请选择或上传素材图片")
        cap_payload["source_mode"] = _GOAL_VIDEO_SOURCE_REFERENCE_IMAGE
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
    if task_kind == "lead_collection_templates":
        return "线索采集模板定时任务"
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


_CREATIVE_VIDEO_JOB_ID_KEYS = {
    "job_id",
    "video_job_id",
    "video_task_id",
    "creative_job_id",
    "creative_video_job_id",
    "creative_generation_job_id",
    "generation_job_id",
}


def _append_unique_text(out: List[str], seen: set[str], value: Any) -> None:
    text = str(value or "").strip()
    if not text or text in seen:
        return
    seen.add(text)
    out.append(text)


def _extract_creative_video_job_ids(*values: Any) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()

    def scan_text(text: str) -> None:
        for match in re.findall(r"/jobs/([A-Za-z0-9_-]{16,128})", text):
            _append_unique_text(out, seen, match)
        for match in re.findall(r"\b[a-fA-F0-9]{24,64}\b", text):
            _append_unique_text(out, seen, match)

    def walk(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                key_name = str(key or "").strip().lower()
                if key_name in _CREATIVE_VIDEO_JOB_ID_KEYS:
                    _append_unique_text(out, seen, item)
                elif key_name in {"poll_path", "video_poll_path"}:
                    scan_text(str(item or ""))
                if isinstance(item, (dict, list)):
                    walk(item)
            return
        if isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    walk(item)
            return
        scan_text(str(value or ""))

    for value in values:
        walk(value)
    return out


def _creative_video_entry_from_dict(item: Any, *, title: str = "") -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    url = str(
        item.get("url")
        or item.get("public_url")
        or item.get("source_url")
        or item.get("video_url")
        or item.get("final_url")
        or item.get("output_url")
        or item.get("media_url")
        or ""
    ).strip()
    asset_id = str(
        item.get("asset_id")
        or item.get("id")
        or item.get("video_asset_id")
        or item.get("final_asset_id")
        or ""
    ).strip()
    media_type = str(item.get("media_type") or item.get("type") or "").strip().lower()
    kind = str(item.get("kind") or item.get("category") or "").strip().lower()
    if not url and not asset_id:
        return None
    if url and not (_is_video_url(url) or media_type == "video" or "video" in kind):
        return None
    return {
        "url": url,
        "source_url": url,
        "asset_id": asset_id,
        "media_type": "video",
        "title": str(item.get("title") or title or "").strip(),
        "description": str(item.get("description") or item.get("hint") or "").strip(),
        "filename": str(item.get("filename") or "").strip(),
    }


def _creative_job_final_video_entry(job: CreativeGenerationJob, *, title: str = "") -> Optional[Dict[str, Any]]:
    payload = job.result_payload if isinstance(job.result_payload, dict) else {}
    candidates: List[Any] = [
        payload.get("final_video"),
        payload.get("video"),
        payload.get("output_video"),
        payload.get("result_video"),
    ]
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    candidates.extend([
        result.get("final_video"),
        result.get("video"),
        result.get("output_video"),
        result.get("result_video"),
    ])
    candidates.extend(job.saved_assets or [])
    candidates.extend(payload.get("saved_assets") or [])
    candidates.extend(result.get("saved_assets") or [])
    for item in candidates:
        entry = _creative_video_entry_from_dict(item, title=title)
        if entry:
            return entry

    stack: List[Any] = [payload]
    seen_obj: set[int] = set()
    while stack:
        cur = stack.pop()
        oid = id(cur)
        if oid in seen_obj:
            continue
        seen_obj.add(oid)
        if isinstance(cur, dict):
            entry = _creative_video_entry_from_dict(cur, title=title)
            if entry:
                return entry
            for value in cur.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(cur, list):
            stack.extend(value for value in cur if isinstance(value, (dict, list)))
    return None


def _prepend_unique_list(items: List[Any], new_item: Any, key_fn) -> List[Any]:
    key = key_fn(new_item)
    if not key:
        return items
    rest = [item for item in items if key_fn(item) != key]
    return [new_item] + rest


def _enrich_run_with_creative_video(db: Session, row: ScheduledTaskRun, data: Dict[str, Any]) -> Dict[str, Any]:
    payload = data.get("result_payload") if isinstance(data.get("result_payload"), dict) else {}
    ids = _extract_creative_video_job_ids(payload, row.payload or {}, row.result_text or "")
    if not ids:
        return data
    jobs = (
        db.query(CreativeGenerationJob)
        .filter(
            CreativeGenerationJob.user_id == row.user_id,
            CreativeGenerationJob.job_id.in_(ids),
            CreativeGenerationJob.deleted_at.is_(None),
        )
        .all()
    )
    job_by_id = {str(job.job_id or ""): job for job in jobs}
    final_entry: Optional[Dict[str, Any]] = None
    final_job_id = ""
    for job_id in ids:
        job = job_by_id.get(job_id)
        if not job:
            continue
        entry = _creative_job_final_video_entry(job, title=row.title or "")
        if entry:
            final_entry = entry
            final_job_id = job.job_id
            break
    if not final_entry:
        return data

    enriched = copy.deepcopy(payload)
    local_result = enriched.get("local_result")
    if not isinstance(local_result, dict):
        local_result = {}
        enriched["local_result"] = local_result
    local_result["final_video"] = final_entry
    local_result["video_url"] = final_entry.get("url") or ""
    local_result["video_asset_id"] = final_entry.get("asset_id") or ""
    local_result["video_status"] = "completed"
    local_result["video_job_id"] = final_job_id or local_result.get("video_job_id") or ""

    item = local_result.get("item")
    if isinstance(item, dict):
        item["final_video"] = final_entry
        item["video_url"] = final_entry.get("url") or item.get("video_url") or ""
        item["video_asset_id"] = final_entry.get("asset_id") or item.get("video_asset_id") or ""
        item["video_status"] = "completed"
        item["video_job_id"] = final_job_id or item.get("video_job_id") or ""

    refs = enriched.get("result_refs")
    if not isinstance(refs, dict):
        refs = {}
        enriched["result_refs"] = refs
    url = str(final_entry.get("url") or "").strip()
    asset_id = str(final_entry.get("asset_id") or "").strip()
    if url:
        refs["urls"] = _prepend_unique_list(
            [str(item) for item in (refs.get("urls") or []) if str(item or "").strip()],
            url,
            lambda item: str(item or "").strip(),
        )
    if asset_id:
        refs["asset_ids"] = _prepend_unique_list(
            [str(item) for item in (refs.get("asset_ids") or []) if str(item or "").strip()],
            asset_id,
            lambda item: str(item or "").strip(),
        )
    saved_assets = refs.get("saved_assets") if isinstance(refs.get("saved_assets"), list) else []
    refs["saved_assets"] = _prepend_unique_list(
        saved_assets,
        final_entry,
        lambda item: str((item or {}).get("asset_id") or (item or {}).get("url") or "").strip() if isinstance(item, dict) else "",
    )

    data["result_payload"] = enriched
    return data


def _serialize_run_compact(row: ScheduledTaskRun) -> Dict[str, Any]:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "created_by_role": row.created_by_role,
        "installation_id": row.installation_id,
        "claimed_by_installation_id": row.claimed_by_installation_id,
        "title": row.title,
        "task_kind": row.task_kind,
        "payload": row.payload or {},
        "status": row.status,
        "progress": row.progress or {},
        "error": row.error,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "claimed_at": _iso(row.claimed_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
    }


def _is_server_side_task(task_or_run: Any) -> bool:
    return str(getattr(task_or_run, "task_kind", "") or "").strip() in _SERVER_SIDE_TASK_KINDS


def _task_display_kind(row: Any) -> str:
    kind = str(getattr(row, "task_kind", "") or "").strip()
    if kind == "lead_collection_templates":
        return "线索采集模板"
    if kind == "ip_content_daily":
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


def _refresh_ip_content_daily_payload(db: Session, row: ScheduledTaskRun) -> Dict[str, Any]:
    payload = _run_result_payload(row)
    if not payload.get("ip_content_daily"):
        return payload
    groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []
    record_ids: List[str] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        records = group.get("records") if isinstance(group.get("records"), list) else []
        for rec in records:
            record_id = str((rec or {}).get("record_id") or "").strip() if isinstance(rec, dict) else ""
            if record_id and record_id not in record_ids:
                record_ids.append(record_id)
    if not record_ids:
        return payload
    rows = (
        db.query(IPContentDraftRecord)
        .filter(IPContentDraftRecord.user_id == row.user_id, IPContentDraftRecord.record_id.in_(record_ids))
        .all()
    )
    by_id = {item.record_id: _draft_record_payload(item) for item in rows}
    if not by_id:
        return payload
    changed = False
    refreshed_groups: List[Dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            refreshed_groups.append(group)
            continue
        updated_group = dict(group)
        records = group.get("records") if isinstance(group.get("records"), list) else []
        updated_records = []
        for rec in records:
            if not isinstance(rec, dict):
                updated_records.append(rec)
                continue
            current = by_id.get(str(rec.get("record_id") or "").strip())
            updated_records.append(current or rec)
            changed = changed or bool(current)
        updated_group["records"] = updated_records
        refreshed_groups.append(updated_group)
    if not changed:
        return payload
    payload["groups"] = refreshed_groups
    payload["records_by_task"] = {str(group.get("task") or ""): group.get("records") or [] for group in refreshed_groups if isinstance(group, dict)}
    return payload


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


def _publish_account_id_value(value: Any) -> Any:
    text = str(value or "").strip()
    if text.isdigit():
        try:
            return int(text)
        except Exception:
            return text
    return text


def _publish_draft_platform(draft: Dict[str, Any]) -> str:
    return str((draft or {}).get("platform") or "").strip().lower()


def _publish_draft_is_wechat_moments(draft: Dict[str, Any]) -> bool:
    return _publish_draft_platform(draft) in {_WECHAT_MOMENTS_PLATFORM, "wechat", "moments"}


def _publish_draft_has_moments_content(draft: Dict[str, Any]) -> bool:
    if str((draft or {}).get("asset_id") or "").strip():
        return True
    if str((draft or {}).get("source_url") or (draft or {}).get("url") or "").strip():
        return True
    text_parts = [
        str((draft or {}).get("description") or "").strip(),
        str((draft or {}).get("title") or "").strip(),
        str((draft or {}).get("content") or "").strip(),
    ]
    return any(text_parts)


def _reported_publish_accounts_from_devices(
    db: Session,
    user_id: int,
    *,
    installation_id: str = "",
) -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    query = db.query(H5ChatDevicePresence).filter(H5ChatDevicePresence.user_id == user_id)
    if installation_id:
        query = query.filter(H5ChatDevicePresence.installation_id == installation_id)
    devices = query.order_by(H5ChatDevicePresence.last_seen_at.desc()).limit(30).all()
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for device in devices:
        payload = device.account_payload if isinstance(device.account_payload, dict) else {}
        rows = payload.get("accounts") if isinstance(payload.get("accounts"), list) else []
        age = (now - device.last_seen_at).total_seconds() if device.last_seen_at else 999999
        device_online = age <= 90
        for item in rows:
            if not isinstance(item, dict):
                continue
            platform = str(item.get("platform") or "").strip()
            account_id = str(item.get("account_id") or item.get("id") or "").strip()
            nickname = str(item.get("nickname") or "").strip()
            if not platform or not account_id or not nickname:
                continue
            select_id = f"{device.installation_id}:{platform}:{account_id}"
            if select_id in seen:
                continue
            seen.add(select_id)
            out.append(
                {
                    "id": select_id,
                    "select_id": select_id,
                    "account_id": _publish_account_id_value(account_id),
                    "platform": platform,
                    "platform_name": str(item.get("platform_name") or SUPPORTED_PLATFORMS.get(platform, {}).get("name", platform)),
                    "nickname": nickname,
                    "status": str(item.get("status") or ("online" if item.get("online") else "")).strip(),
                    "installation_id": device.installation_id,
                    "device_name": device.display_name or device.installation_id,
                    "device_online": device_online,
                    "source": "device",
                    "managed_by": str(item.get("managed_by") or "").strip(),
                    "is_origin_slot": bool(item.get("is_origin_slot")) if item.get("is_origin_slot") is not None else False,
                }
            )
    return out


def _reported_wechat_moments_accounts_from_devices(
    db: Session,
    user_id: int,
    *,
    installation_id: str = "",
) -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    query = db.query(H5ChatDevicePresence).filter(H5ChatDevicePresence.user_id == user_id)
    if installation_id:
        query = query.filter(H5ChatDevicePresence.installation_id == installation_id)
    devices = query.order_by(H5ChatDevicePresence.last_seen_at.desc()).limit(30).all()
    out: List[Dict[str, Any]] = []
    for device in devices:
        age = (now - device.last_seen_at).total_seconds() if device.last_seen_at else 999999
        if age > 90:
            continue
        select_id = f"{device.installation_id}:{_WECHAT_MOMENTS_PLATFORM}:{_WECHAT_MOMENTS_ACCOUNT_ID}"
        out.append(
            {
                "id": select_id,
                "select_id": select_id,
                "account_id": _WECHAT_MOMENTS_ACCOUNT_ID,
                "platform": _WECHAT_MOMENTS_PLATFORM,
                "platform_name": _WECHAT_MOMENTS_PLATFORM_NAME,
                "nickname": "本机微信",
                "status": "online",
                "installation_id": device.installation_id,
                "device_name": device.display_name or device.installation_id,
                "device_online": True,
                "source": "pc_wechat",
                "managed_by": "",
                "is_origin_slot": False,
            }
        )
    return out


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


def _serial_client_run_key(row: ScheduledTaskRun, installation_id: str) -> str:
    kind = str(row.task_kind or "").strip()
    if kind not in _SERIAL_CLIENT_TASK_KINDS:
        return ""
    effective_installation_id = (
        str(row.installation_id or "").strip()
        or str(row.claimed_by_installation_id or "").strip()
        or str(installation_id or "").strip()
        or "unknown"
    )
    return f"{kind}:{effective_installation_id}"


def _serial_client_run_is_blocked(
    db: Session,
    *,
    candidate: ScheduledTaskRun,
    installation_id: str,
    claimed_keys: set[str],
) -> bool:
    key = _serial_client_run_key(candidate, installation_id)
    if not key:
        return False
    if key in claimed_keys:
        return True
    q = db.query(ScheduledTaskRun.id).filter(
        ScheduledTaskRun.user_id == candidate.user_id,
        ScheduledTaskRun.id != candidate.id,
        ScheduledTaskRun.task_kind == candidate.task_kind,
        ScheduledTaskRun.status == "processing",
    )
    effective_installation_id = str(candidate.installation_id or "").strip() or str(installation_id or "").strip()
    if effective_installation_id:
        q = q.filter(
            or_(
                ScheduledTaskRun.installation_id == effective_installation_id,
                ScheduledTaskRun.claimed_by_installation_id == effective_installation_id,
            )
        )
    return q.first() is not None


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


def _set_server_side_run_progress(
    db: Session,
    run: ScheduledTaskRun,
    *,
    stage: str,
    text: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    now = datetime.utcnow()
    try:
        db.refresh(run)
        patch: Dict[str, Any] = {
            "stage": stage,
            "text": text,
            "server_side": True,
            "progress_updated_at": now.isoformat(),
        }
        if extra:
            patch.update(extra)
        run.progress = _merge_run_progress(run, patch)
        run.updated_at = now
        db.commit()
    except Exception:
        db.rollback()


def _execute_server_side_run(db: Session, run: ScheduledTaskRun, now: Optional[datetime] = None) -> None:
    now = now or datetime.utcnow()
    if run.task_kind not in _SERVER_SIDE_TASK_KINDS:
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
    timeout_seconds = _server_side_timeout_seconds(run.task_kind)

    def progress(stage: str, text: str, extra: Optional[Dict[str, Any]] = None) -> None:
        _set_server_side_run_progress(db, run, stage=stage, text=text, extra=extra)

    try:
        payload = run.payload if isinstance(run.payload, dict) else {}
        if run.task_kind == "ip_content_daily":
            progress("start", "服务器开始执行 IP 日更文案", {"timeout_seconds": timeout_seconds})
            result = _run_async_blocking(
                asyncio.wait_for(
                    run_ip_content_daily_scheduled(
                        db=db,
                        current_user=user,
                        options=payload,
                        run_id=run.id,
                        progress=progress,
                    ),
                    timeout=timeout_seconds,
                )
            )
            result_text = "IP日更文案已生成，朋友圈图片请在详情里手动触发。"
        elif run.task_kind == "lead_collection_templates":
            progress("start", "服务器开始执行线索采集模板", {"timeout_seconds": timeout_seconds})
            result = _run_async_blocking(
                asyncio.wait_for(
                    run_lead_collection_templates_scheduled(
                        db=db,
                        current_user=user,
                        template_ids=payload.get("template_ids") or [],
                        title=str(payload.get("title") or run.title or "").strip(),
                        run_id=run.id,
                    ),
                    timeout=timeout_seconds,
                )
            )
            result_text = "线索采集模板已执行完成。"
        elif run.task_kind == "social_leads":
            progress("start", "服务端开始执行社媒线索采集", {"timeout_seconds": timeout_seconds})
            job = create_social_leads_job_from_payload(db=db, current_user=user, payload=payload, auto_run=False)
            job = _run_async_blocking(
                asyncio.wait_for(
                    run_social_leads_job_to_completion(db=db, current_user=user, row=job),
                    timeout=timeout_seconds,
                )
            )
            result = social_leads_job_payload(job, db=db, include_sources=True)
            result_text = "社媒线索采集已完成"
        elif run.task_kind == "linkedin_mining":
            progress("start", "服务端开始执行 LinkedIn 线索采集", {"timeout_seconds": timeout_seconds})
            job = create_linkedin_mining_job_from_payload(db=db, current_user=user, payload=payload, auto_run=False)
            job = _run_async_blocking(
                asyncio.wait_for(
                    run_linkedin_mining_job_to_completion(db=db, current_user=user, row=job),
                    timeout=timeout_seconds,
                )
            )
            result = linkedin_mining_job_payload(job)
            result_text = "LinkedIn 线索采集已完成"
        elif run.task_kind == "wechat_channels_transcript":
            progress("start", "服务端开始执行视频号文案提取", {"timeout_seconds": timeout_seconds})
            result = _run_async_blocking(
                asyncio.wait_for(
                    run_wechat_channels_transcript_payload_to_completion(db=db, current_user=user, payload=payload),
                    timeout=timeout_seconds,
                )
            )
            result_text = "视频号文案提取已完成"
        else:
            return
        finished = datetime.utcnow()
        db.refresh(run)
        failed_count = int(result.get("failed_count") or 0) if isinstance(result, dict) else 0
        job_count = int(result.get("job_count") or 0) if isinstance(result, dict) else 0
        result_status = str(result.get("status") or "").strip().lower() if isinstance(result, dict) else ""
        result_error = str(result.get("error") or "").strip() if isinstance(result, dict) else ""
        run.status = "failed" if (run.task_kind == "lead_collection_templates" and job_count > 0 and failed_count >= job_count) or result_status == "failed" else "completed"
        run.result_text = result_text
        run.result_payload = result
        run.error = "线索采集模板全部执行失败" if run.status == "failed" else None
        run.progress = {"completed_at": finished.isoformat(), "server_side": True}
        if run.status == "failed" and result_error:
            run.error = result_error
        run.finished_at = finished
        run.updated_at = finished
        task = db.query(ScheduledTask).filter(ScheduledTask.id == run.task_id).first() if run.task_id else None
        if task:
            task.last_error = None
            task.updated_at = finished
        db.commit()
    except (asyncio.TimeoutError, TimeoutError):
        failed = datetime.utcnow()
        try:
            db.refresh(run)
        except Exception:
            pass
        message = f"服务器执行超过 {int(timeout_seconds)} 秒，已自动停止等待，请稍后重试。"
        run.status = "failed"
        run.error = message
        run.progress = _merge_run_progress(
            run,
            {
                "failed_at": failed.isoformat(),
                "server_side": True,
                "stage": "timeout",
                "text": message,
                "timeout_seconds": timeout_seconds,
            },
        )
        run.finished_at = failed
        run.updated_at = failed
        task = db.query(ScheduledTask).filter(ScheduledTask.id == run.task_id).first() if run.task_id else None
        if task:
            task.last_error = run.error
            task.updated_at = failed
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


def _fail_stale_server_side_runs(db: Session, now: Optional[datetime] = None) -> int:
    now = now or datetime.utcnow()
    count = 0
    for task_kind in _SERVER_SIDE_TASK_KINDS:
        timeout_seconds = _server_side_timeout_seconds(task_kind)
        cutoff = now - timedelta(seconds=timeout_seconds)
        rows = (
            db.query(ScheduledTaskRun)
            .filter(
                ScheduledTaskRun.task_kind == task_kind,
                ScheduledTaskRun.status == "processing",
                ScheduledTaskRun.started_at.isnot(None),
                ScheduledTaskRun.started_at < cutoff,
            )
            .limit(50)
            .all()
        )
        for row in rows:
            message = f"服务器执行超过 {int(timeout_seconds)} 秒，已自动标记失败，请重试。"
            row.status = "failed"
            row.error = message
            row.progress = _merge_run_progress(
                row,
                {
                    "failed_at": now.isoformat(),
                    "server_side": True,
                    "stage": "stale_timeout",
                    "text": message,
                    "timeout_seconds": timeout_seconds,
                },
            )
            row.finished_at = now
            row.updated_at = now
            task = db.query(ScheduledTask).filter(ScheduledTask.id == row.task_id).first() if row.task_id else None
            if task:
                task.last_error = message
                task.updated_at = now
            count += 1
    if count:
        db.commit()
    return count


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
        ScheduledTask.task_kind.notin_(list(_SERVER_SIDE_TASK_KINDS)),
        ScheduledTask.schedule_type.in_(["once", "interval", "daily_times"]),
        ScheduledTask.next_run_at.isnot(None),
        ScheduledTask.next_run_at <= now,
    )
    if user_id is not None:
        q = q.filter(ScheduledTask.user_id == user_id)
    q = q.order_by(ScheduledTask.next_run_at.asc(), ScheduledTask.id.asc()).limit(50).with_for_update(skip_locked=True)
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
    if task_kind == "client_workflow" and not str(payload.get("action") or "").strip():
        raise HTTPException(status_code=400, detail="客户端工作流任务需要 payload.action")
    disabled_capability = _disabled_scheduled_capability(payload) if task_kind == "capability" else ""
    if disabled_capability:
        raise HTTPException(status_code=400, detail=f"定时任务能力已下线：{disabled_capability}")
    if task_kind == "ip_content_daily":
        payload = dict(payload)
        if not int(payload.get("template_id") or 0) and not payload.get("keyword_ids") and not payload.get("competitor_ids") and not payload.get("memory_docs"):
            raise HTTPException(status_code=400, detail="IP日更文案任务需要选择模板、关键词、同行账号或记忆资料")
    if task_kind == "lead_collection_templates":
        payload = dict(payload)
        ids = payload.get("template_ids") or []
        valid_ids: List[int] = []
        if isinstance(ids, list):
            for item in ids:
                try:
                    tid = int(item or 0)
                except Exception:
                    continue
                if tid > 0:
                    valid_ids.append(tid)
        if not valid_ids:
            raise HTTPException(status_code=400, detail="线索采集定时任务需要选择至少一个采集模板")
        payload["template_ids"] = valid_ids
    if task_kind == "social_leads":
        payload = dict(payload)
        if not str(payload.get("platform") or "").strip():
            raise HTTPException(status_code=400, detail="线索采集定时任务需要平台")
        if not payload.get("keywords"):
            raise HTTPException(status_code=400, detail="线索采集定时任务需要精准用户方向关键词")
    if task_kind == "linkedin_mining":
        payload = dict(payload)
        if not (payload.get("seed_profile_urls") or payload.get("seed_company_urls") or payload.get("keywords") or payload.get("hashtags")):
            raise HTTPException(status_code=400, detail="LinkedIn线索采集定时任务需要主页、公司、关键词或话题")
    if task_kind == "wechat_channels_transcript":
        payload = dict(payload)
        if not str(payload.get("query") or payload.get("username") or "").strip():
            raise HTTPException(status_code=400, detail="视频号文案提取定时任务需要账号、链接或关键词")
    if task_kind == "capability":
        payload = dict(payload)
        _normalize_goal_video_task_payload(payload)
    interval_seconds = None
    now = datetime.utcnow()
    if task_kind == "client_workflow":
        payload = _enrich_local_bestseller_workflow_payload(db, payload=dict(payload), target_user_id=target_user_id, now=now)
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
    if not xi and requested_kind not in _SERVER_SIDE_TASK_KINDS:
        raise HTTPException(status_code=400, detail="missing current installation id")
    if xi:
        ensure_installation_slot(db, owner_user.id, xi)
    if requested_kind in _SERVER_SIDE_TASK_KINDS:
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
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    query = db.query(ScheduledTask).filter(ScheduledTask.user_id == owner_user.id)
    total = query.with_entities(func.count(ScheduledTask.id)).scalar() or 0
    rows = query.order_by(ScheduledTask.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "ok": True,
        "tasks": [_serialize_task(r) for r in rows],
        "pagination": {"total": int(total), "limit": int(limit), "offset": int(offset), "has_next": offset + limit < int(total)},
    }


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
    installation_id: str = Query("", max_length=128),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    device_accounts = _reported_publish_accounts_from_devices(
        db,
        owner_user.id,
        installation_id=(installation_id or "").strip(),
    )
    wechat_moments_accounts = _reported_wechat_moments_accounts_from_devices(
        db,
        owner_user.id,
        installation_id=(installation_id or "").strip(),
    )
    rows = (
        db.query(PublishAccount)
        .filter(PublishAccount.user_id == owner_user.id)
        .order_by(PublishAccount.created_at.desc())
        .all()
    )
    server_accounts = [
        {
            "id": f"server:{row.id}",
            "select_id": f"server:{row.id}",
            "account_id": row.id,
            "platform": row.platform,
            "platform_name": SUPPORTED_PLATFORMS.get(row.platform, {}).get("name", row.platform),
            "nickname": row.nickname,
            "status": row.status,
            "installation_id": "",
            "device_name": "",
            "device_online": False,
            "source": "server",
        }
        for row in rows
    ]
    return {
        "ok": True,
        "accounts": wechat_moments_accounts + device_accounts + server_accounts,
        "platforms": [{"id": _WECHAT_MOMENTS_PLATFORM, "name": _WECHAT_MOMENTS_PLATFORM_NAME}]
        + [{"id": key, "name": value["name"]} for key, value in SUPPORTED_PLATFORMS.items()],
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
    fields_set = set(getattr(body, "__fields_set__", set()) or getattr(body, "model_fields_set", set()) or set())
    if "title" in fields_set:
        title = (body.title or "").strip()
        if title:
            task.title = title[:160]
            task.updated_at = datetime.utcnow()
    schedule_fields = {"schedule_type", "interval_seconds", "start_at", "daily_times", "timezone_offset_minutes"}
    if fields_set & schedule_fields:
        now = datetime.utcnow()
        current_cfg = _schedule_config_from_payload(task.payload or {})
        schedule_type = _normalize_schedule_type(body.schedule_type or task.schedule_type)
        tz_offset = int(
            body.timezone_offset_minutes
            if body.timezone_offset_minutes is not None
            else current_cfg.get("timezone_offset_minutes")
            if current_cfg.get("timezone_offset_minutes") is not None
            else 480
        )
        payload = dict(task.payload or {})
        schedule_config: Dict[str, Any] = {"timezone_offset_minutes": tz_offset}
        start_at_value = body.start_at if "start_at" in fields_set else str(current_cfg.get("start_at") or "")
        start_at_utc = None if schedule_type == "daily_times" else _parse_client_datetime(start_at_value, tz_offset)
        if start_at_utc:
            schedule_config["start_at"] = start_at_value
            schedule_config["start_at_utc"] = start_at_utc.isoformat()
        interval_seconds = None
        next_run_at = start_at_utc or now
        if schedule_type == "interval":
            raw_interval = body.interval_seconds if body.interval_seconds is not None else task.interval_seconds or 3600
            interval_seconds = max(60, min(int(raw_interval or 3600), 366 * 24 * 3600))
        elif schedule_type == "daily_times":
            raw_times = body.daily_times if "daily_times" in fields_set else current_cfg.get("daily_times") or []
            daily_times = _normalize_daily_times(raw_times)
            schedule_config["daily_times"] = daily_times
            next_run_at = _compute_next_daily_time(
                now_utc=now,
                daily_times=daily_times,
                timezone_offset_minutes=tz_offset,
            )
        payload["schedule_config"] = schedule_config
        _cancel_pending_runs_for_task(db, task, now)
        task.payload = payload
        task.schedule_type = schedule_type
        task.interval_seconds = interval_seconds
        task.next_run_at = next_run_at
        task.updated_at = now
        if task.status == "active" and task.next_run_at and task.next_run_at <= now:
            _enqueue_task(db, task, now)
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
    offset: int = Query(0, ge=0),
    compact: bool = Query(False),
    date: str = Query("", max_length=10),
    timezone_offset_minutes: int = Query(480, ge=-720, le=840),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    _enqueue_due_tasks(db, owner_user.id)
    query = db.query(ScheduledTaskRun).filter(ScheduledTaskRun.user_id == owner_user.id)
    date_key = (date or "").strip()
    if date_key:
        try:
            local_start = datetime.strptime(date_key, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid date") from None
        start_utc = local_start - timedelta(minutes=int(timezone_offset_minutes or 0))
        end_utc = start_utc + timedelta(days=1)
        query = query.filter(ScheduledTaskRun.created_at >= start_utc, ScheduledTaskRun.created_at < end_utc)
    total = query.with_entities(func.count(ScheduledTaskRun.id)).scalar() or 0
    rows = query.order_by(ScheduledTaskRun.created_at.desc()).offset(offset).limit(limit).all()
    serializer = _serialize_run_compact if compact else _serialize_run
    return {
        "ok": True,
        "runs": [serializer(r) for r in rows],
        "pagination": {"total": int(total), "limit": int(limit), "offset": int(offset), "has_next": offset + limit < int(total)},
    }


@router.get("/api/scheduled-tasks/runs/{run_id}", summary="执行记录详情")
def get_scheduled_task_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    row = _run_for_user(db, run_id, owner_user.id)
    data = _serialize_run(row)
    if row.task_kind == "ip_content_daily":
        data["result_payload"] = _refresh_ip_content_daily_payload(db, row)
    data = _enrich_run_with_creative_video(db, row, data)
    return {"ok": True, "run": data}


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
            ScheduledTaskRun.task_kind.notin_(list(_SERVER_SIDE_TASK_KINDS)),
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
        .filter(ScheduledTaskRun.task_kind.notin_(list(_SERVER_SIDE_TASK_KINDS)))
        .filter(or_(ScheduledTaskRun.installation_id.is_(None), ScheduledTaskRun.installation_id == xi))
        .order_by(ScheduledTaskRun.created_at.asc(), ScheduledTaskRun.id.asc())
        .limit(limit)
        .all()
    )
    rows: List[ScheduledTaskRun] = []
    claimed_serial_keys: set[str] = set()
    for candidate in candidates:
        serial_key = _serial_client_run_key(candidate, xi)
        if _serial_client_run_is_blocked(
            db,
            candidate=candidate,
            installation_id=xi,
            claimed_keys=claimed_serial_keys,
        ):
            continue
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
        if serial_key:
            claimed_serial_keys.add(serial_key)
    db.commit()
    if rows:
        _clear_pending_empty(pending_key)
    else:
        _mark_pending_empty(pending_key, _RUN_PENDING_EMPTY_CACHE_SECONDS)
    return {"ok": True, "items": [_serialize_run(r) for r in rows]}


@router.post("/api/scheduled-tasks/runs/{run_id}/publish-request", summary="提交定时任务结果发布请求")
def request_scheduled_task_publish(
    run_id: str,
    body: Optional[ScheduledPublishRequestIn] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    row = _run_for_user(db, run_id, owner_user.id)
    if row.status != "completed":
        raise HTTPException(status_code=409, detail="任务完成后才能发布")
    payload = _run_result_payload(row)
    draft = dict(_publish_draft_from_payload(payload) or {})
    incoming = body.publish_draft if body and isinstance(body.publish_draft, dict) else {}
    if incoming:
        draft.update({k: v for k, v in incoming.items() if v is not None})
    if not draft:
        raise HTTPException(status_code=400, detail="该记录没有可发布草稿")
    is_wechat_moments = _publish_draft_is_wechat_moments(draft)
    if is_wechat_moments:
        draft["platform"] = _WECHAT_MOMENTS_PLATFORM
        draft["platform_name"] = str(draft.get("platform_name") or _WECHAT_MOMENTS_PLATFORM_NAME).strip()
        draft["account_id"] = str(draft.get("account_id") or _WECHAT_MOMENTS_ACCOUNT_ID).strip()
        draft["account_nickname"] = str(draft.get("account_nickname") or "本机微信").strip()
        draft["media_type"] = str(draft.get("media_type") or "image_text").strip()
        if not _publish_draft_has_moments_content(draft):
            raise HTTPException(status_code=400, detail="朋友圈发布缺少正文或素材")
    elif not str(draft.get("asset_id") or "").strip():
        raise HTTPException(status_code=400, detail="发布草稿缺少素材 asset_id")
    if not str(draft.get("account_id") or "").strip() and not str(draft.get("account_nickname") or "").strip():
        raise HTTPException(status_code=400, detail="发布草稿缺少发布账号")
    status = str(draft.get("status") or "ready").strip().lower()
    if status == "published":
        return {"ok": True, "status": "published", "run": _serialize_run(row)}
    now = datetime.utcnow()
    target_installation = str(draft.get("installation_id") or "").strip()
    if target_installation:
        draft["installation_id"] = target_installation
        row.installation_id = target_installation
        row.claimed_by_installation_id = None
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
