from __future__ import annotations

import copy
import os
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import or_

from ..db import get_db
from ..models import (
    ContentCompetitorAccount,
    H5WorkflowActivation,
    H5ChatDevicePresence,
    H5AgentTemplateGrant,
    H5MountedAccountDefault,
    H5WorkflowTemplate,
    H5WorkflowTemplateGrant,
    IPContentKeyword,
    IPContentScheduleTemplate,
    OpenClawMemoryDocument,
    ShanjianDigitalHumanProfile,
    UserHiflyAvatarAsset,
    UserHiflyVoiceAsset,
    ScheduledTask,
    User,
)
from .admin import _agent_sub_user_ids
from .auth import get_current_user
from .mobile_identity import online_user_for_mobile_user
from .scheduled_tasks import (
    ScheduledTaskCreate,
    _SERVER_SIDE_TASK_KINDS,
    _cancel_unfinished_runs_for_task,
    _create_task_row,
    _delete_task_row,
    _enqueue_task,
    _local_bestseller_profile_from_persona,
    _serialize_task,
)

router = APIRouter()

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_PERSONAL_DEFAULT_TEMPLATE_NAME = "个人默认配置"
_IP_DAILY_DEFAULT_TASKS = ["industry_hot_oral", "professional_ip_oral", "moments_candidate"]
_DEVICE_ONLINE_TTL_SECONDS = 120
_WORKFLOW_ACTION_PLATFORMS = {"douyin": "抖音", "toutiao": "头条", "wechat_channels": "视频号", "wechat_moments": "朋友圈图文"}
_WORKFLOW_CHILD_CLIENT_ACTIONS = {
    "native_wechat_poll",
    "native_wechat_add_friend",
    "native_wechat_moments_engage",
}
_WORKFLOW_CHILD_ACTION_TYPES = {
    "client_workflow",
    "native_wechat_add_friend",
    "native_wechat_moments_engage",
}
_ENABLED_SYSTEM_WORKFLOW_KEYS = {"system_sales"}
_SALES_DH_PROVIDER_V2 = "shanjian_v2"
_SALES_DH_PROVIDER_LEGACY = "hifly_legacy"


class WorkflowTemplateIn(BaseModel):
    name: str = Field("", max_length=160)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    installation_id: Optional[str] = Field(None, max_length=128)


class WorkflowGrantIn(BaseModel):
    target_user_ids: list[int] = Field(default_factory=list)


class WorkflowActivateIn(BaseModel):
    template_id: int
    installation_id: str = Field("", max_length=128)
    timezone_offset_minutes: Optional[int] = None
    plan_day: Optional[int] = Field(None, ge=1, le=30)


class WorkflowActivateInlineIn(BaseModel):
    template_key: str = Field("", max_length=128)
    name: str = Field("", max_length=160)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    installation_id: str = Field("", max_length=128)
    timezone_offset_minutes: Optional[int] = None
    plan_day: Optional[int] = Field(None, ge=1, le=30)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def _clean_time(value: Any) -> str:
    text = str(value or "").strip()
    if not _TIME_RE.match(text):
        raise HTTPException(status_code=400, detail="节点时间格式应为 HH:MM")
    return text


def _workflow_platform_label(platform: str) -> str:
    key = str(platform or "").strip().lower()
    return _WORKFLOW_ACTION_PLATFORMS.get(key, key or "平台")


def _clean_action_nodes(raw_actions: Any, parent: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if not isinstance(raw_actions, list):
        return actions
    parent_id = str(parent.get("id") or "").strip()
    for raw in raw_actions[:12]:
        if not isinstance(raw, dict) or _is_workflow_placeholder(raw):
            continue
        action_type = str(raw.get("action_type") or raw.get("type") or "publish").strip().lower()
        if action_type != "publish":
            plan = raw.get("plan") if isinstance(raw.get("plan"), dict) else {}
            payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
            action = _clean_text(payload.get("action") or raw.get("ability_key"), 128)
            if action_type not in _WORKFLOW_CHILD_ACTION_TYPES or action not in _WORKFLOW_CHILD_CLIENT_ACTIONS:
                raise HTTPException(status_code=400, detail="动作节点暂时只支持发布或系统销售微信动作")
            params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            params = dict(params or {})
            params.update(
                {
                    "source_workflow_node_id": parent_id,
                    "source_workflow_node_label": parent.get("ability_label") or parent.get("note") or "",
                }
            )
            if action == "native_wechat_add_friend":
                params.setdefault("source_mode", "douyin_private_message_phone")
                params.setdefault("trigger", "clear_mobile")
                params.setdefault("skip_without_clear_mobile", True)
                params.setdefault("targets", [])
            label = str(raw.get("ability_label") or raw.get("label") or plan.get("title") or "系统销售微信动作").strip()[:160]
            action_id = str(raw.get("id") or f"{parent_id}_action_{len(actions) + 1}")[:64]
            actions.append(
                {
                    "id": action_id,
                    "time": _clean_time(raw.get("time")),
                    "end_time": _clean_time(raw.get("end_time")) if str(raw.get("end_time") or "").strip() else "",
                    "time_range": str(raw.get("time_range") or "").strip()[:32],
                    "parent_node_id": parent_id,
                    "action_type": action_type,
                    "type": action_type,
                    "platform": str(raw.get("platform") or "").strip().lower()[:64],
                    "ability_key": str(raw.get("ability_key") or action).strip()[:128],
                    "ability_label": label,
                    "department_id": str(raw.get("department_id") or parent.get("department_id") or "").strip()[:64],
                    "department_name": str(raw.get("department_name") or parent.get("department_name") or "").strip()[:80],
                    "note": str(raw.get("note") or "").strip()[:2000],
                    "is_action_node": True,
                    "param_configured": bool(raw.get("param_configured", True)),
                    "plan": {
                        "title": str(plan.get("title") or label).strip()[:160],
                        "task_kind": "client_workflow",
                        "content": str(plan.get("content") or f"H5 工作流动作：{label}").strip()[:12000],
                        "payload": {"action": action, "params": params},
                    },
                }
            )
            continue
        platform = str(raw.get("platform") or "").strip().lower()
        if platform not in _WORKFLOW_ACTION_PLATFORMS:
            raise HTTPException(status_code=400, detail="发布动作暂时只支持抖音、头条、视频号和朋友圈")
        label = f"发布{_workflow_platform_label(platform)}"
        plan = raw.get("plan") if isinstance(raw.get("plan"), dict) else {}
        payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        params = dict(params or {})
        params.update(
            {
                "source_mode": "parent_latest_run",
                "source_workflow_node_id": parent_id,
                "source_workflow_node_label": parent.get("ability_label") or parent.get("note") or "",
                "platform": platform,
                "media_type": params.get("media_type") or ("image_text" if platform == "wechat_moments" else "video"),
                "ai_publish_copy": bool(params.get("ai_publish_copy", True)),
            }
        )
        action_id = str(raw.get("id") or f"{parent_id}_action_{len(actions) + 1}")[:64]
        actions.append(
            {
                "id": action_id,
                "time": _clean_time(raw.get("time")),
                "end_time": _clean_time(raw.get("end_time")) if str(raw.get("end_time") or "").strip() else "",
                "time_range": str(raw.get("time_range") or "").strip()[:32],
                "parent_node_id": parent_id,
                "action_type": action_type,
                "type": action_type,
                "platform": platform,
                "ability_key": "publish_content",
                "ability_label": str(raw.get("ability_label") or raw.get("label") or label).strip()[:160],
                "department_id": str(raw.get("department_id") or parent.get("department_id") or "").strip()[:64],
                "department_name": str(raw.get("department_name") or parent.get("department_name") or "").strip()[:80],
                "note": str(raw.get("note") or "").strip()[:2000],
                "is_action_node": True,
                "param_configured": bool(raw.get("param_configured", True)),
                "plan": {
                    "title": str(plan.get("title") or label).strip()[:160],
                    "task_kind": "client_workflow",
                    "content": str(plan.get("content") or f"H5 工作流动作：{label}").strip()[:12000],
                    "payload": {"action": "publish_content", "params": params},
                },
            }
        )
    actions.sort(key=lambda item: item["time"])
    parent_end_time = str(parent.get("end_time") or "").strip()
    for index, item in enumerate(actions):
        end_time = str(item.get("end_time") or "").strip()
        if not end_time:
            next_item = actions[index + 1] if index + 1 < len(actions) else None
            end_time = str(next_item.get("time") if next_item else parent_end_time).strip()
        item["end_time"] = end_time
        item["time_range"] = f"{item['time']}-{end_time}" if end_time else item["time"]
    return actions


def _is_workflow_placeholder(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    marker_text = " ".join(
        str(value or "")
        for value in (
            node.get("ability_label"),
            node.get("abilityLabel"),
            node.get("label"),
            node.get("note"),
            plan.get("title"),
            payload.get("note"),
        )
    )
    return bool(
        node.get("comingSoon")
        or node.get("coming_soon")
        or node.get("workflow_placeholder")
        or node.get("placeholder")
        or payload.get("skip_execution")
        or payload.get("action") == "workflow_coming_soon"
        or "敬请期待" in marker_text
    )


def _visible_workflow_nodes(nodes: Any) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    for raw in nodes if isinstance(nodes, list) else []:
        if not isinstance(raw, dict) or _is_workflow_placeholder(raw):
            continue
        item = copy.deepcopy(raw)
        if isinstance(item.get("children"), list):
            child_key = "children"
        elif isinstance(item.get("actions"), list):
            child_key = "actions"
        else:
            child_key = ""
        if child_key:
            item[child_key] = _visible_workflow_nodes(item.get(child_key))
        visible.append(item)
    return visible


def _clean_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for raw in nodes or []:
        if not isinstance(raw, dict):
            continue
        plan = raw.get("plan") if isinstance(raw.get("plan"), dict) else raw
        task_kind = str(plan.get("task_kind") or plan.get("taskKind") or "").strip().lower()
        payload = copy.deepcopy(plan.get("payload")) if isinstance(plan.get("payload"), dict) else {}
        _normalize_douyin_private_switch(raw, plan, payload)
        title = str(plan.get("title") or raw.get("label") or raw.get("ability_label") or "工作流任务").strip()[:160]
        content = str(plan.get("content") or f"H5 工作流：{title}").strip()[:12000]
        if _is_workflow_placeholder(raw) or payload.get("action") == "workflow_coming_soon":
            continue
        if not task_kind:
            raise HTTPException(status_code=400, detail=f"{title} 缺少任务类型")
        if task_kind == "client_workflow" and not str(payload.get("action") or "").strip():
            raise HTTPException(status_code=400, detail=f"{title} 缺少客户端动作")
        if task_kind == "capability" and not str(payload.get("capability_id") or "").strip():
            raise HTTPException(status_code=400, detail=f"{title} 缺少能力 ID")
        item = {
            "id": str(raw.get("id") or f"node_{len(cleaned) + 1}")[:64],
            "time": _clean_time(raw.get("time")),
            "end_time": _clean_time(raw.get("end_time")) if str(raw.get("end_time") or "").strip() else "",
            "time_range": str(raw.get("time_range") or "").strip()[:32],
            "ability_key": str(raw.get("ability_key") or raw.get("abilityKey") or "").strip()[:128],
            "ability_label": str(raw.get("ability_label") or raw.get("abilityLabel") or raw.get("label") or title).strip()[:160],
            "department_id": str(raw.get("department_id") or raw.get("departmentId") or "").strip()[:64],
            "department_name": str(raw.get("department_name") or raw.get("departmentName") or "").strip()[:80],
            "note": str(raw.get("note") or "").strip()[:2000],
            "sales_preset": bool(raw.get("sales_preset") or raw.get("salesPreset")),
            "param_configured": bool(raw.get("param_configured")),
            "plan": {
                "title": title,
                "task_kind": task_kind,
                "content": content,
                "payload": payload,
            },
        }
        raw_children = raw.get("children") if isinstance(raw.get("children"), list) else raw.get("actions")
        children = _clean_action_nodes(raw_children, item)
        if children:
            item["children"] = children
        cleaned.append(item)
    if not cleaned:
        raise HTTPException(status_code=400, detail="请至少添加一个工作流节点")
    cleaned.sort(key=lambda item: item["time"])
    for idx, item in enumerate(cleaned):
        next_item = cleaned[idx + 1] if idx + 1 < len(cleaned) else None
        end_time = str(item.get("end_time") or "").strip()
        if not end_time and next_item:
            end_time = str(next_item.get("time") or "").strip()
        item["end_time"] = end_time
        item["time_range"] = f"{item['time']}-{end_time}" if end_time else item["time"]
    return cleaned[:96]


def _clean_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text)[:limit]


def _bool_param(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    if isinstance(value, (int, float)):
        return value != 0
    text = _clean_text(value, 32).lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return bool(default)


def _normalize_douyin_private_switch(
    node: dict[str, Any],
    plan: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    task_kind = _clean_text(plan.get("task_kind") or plan.get("taskKind"), 64).lower()
    params = dict(payload.get("params") if isinstance(payload.get("params"), dict) else {})
    action = _clean_text(payload.get("action") or params.get("sales_action"), 128).lower()
    ability_key = _clean_text(node.get("ability_key") or node.get("abilityKey"), 128).lower()
    marker = " ".join(
        _clean_text(value, 200)
        for value in (node.get("ability_label"), node.get("abilityLabel"), node.get("note"), plan.get("title"))
    )
    is_private = action == "stranger_message" or (
        task_kind == "douyin_leads"
        and ability_key == "douyin_leads"
        and ("私信接管" in marker or "private takeover" in marker.lower())
    )
    if task_kind != "douyin_leads" or not is_private:
        return
    params["wechat_add_friend_enabled"] = _bool_param(params.get("wechat_add_friend_enabled"), False)
    params["wechat_add_friend_targets_source"] = "douyin_private_message_phone"
    # The reply strategy is selected on the private-message node, while the
    # actual AI prompt/contact are loaded from Online's saved monitor config.
    # Keep the mode in the server-owned workflow payload so H5 one-shot runs
    # cannot silently fall back to the fixed-message path.
    # Keep legacy nodes byte-for-byte compatible when they never had this
    # option. New H5/Online editors always send the field explicitly; the
    # worker treats an omitted value as fixed mode.
    if "reply_mode" in params:
        raw_reply_mode = _clean_text(params.get("reply_mode"), 32).lower()
        params["reply_mode"] = raw_reply_mode if raw_reply_mode in {"fixed", "ai_lead"} else "fixed"
    params.pop("wechat_add_friend_rules", None)
    payload["params"] = params


def _canonical_workflow_nodes(nodes: Any) -> list[dict[str, Any]]:
    prepared = _visible_workflow_nodes(nodes)
    for node in prepared:
        plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
        payload = copy.deepcopy(plan.get("payload")) if isinstance(plan.get("payload"), dict) else {}
        _normalize_douyin_private_switch(node, plan, payload)
        if plan:
            plan["payload"] = payload
            node["plan"] = plan
        if isinstance(node.get("children"), list):
            node["children"] = _canonical_workflow_nodes(node["children"])
        elif isinstance(node.get("actions"), list):
            node["actions"] = _canonical_workflow_nodes(node["actions"])
    return prepared


def _safe_int(value: Any, default: int = 0, *, min_value: Optional[int] = None, max_value: Optional[int] = None) -> int:
    try:
        out = int(value)
    except Exception:
        out = int(default)
    if min_value is not None:
        out = max(int(min_value), out)
    if max_value is not None:
        out = min(int(max_value), out)
    return out


def _clean_id_list(value: Any, limit: int = 50) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    if not isinstance(value, list):
        return out
    for item in value:
        try:
            ident = int(item or 0)
        except Exception:
            continue
        if ident <= 0 or ident in seen:
            continue
        seen.add(ident)
        out.append(ident)
        if len(out) >= limit:
            break
    return out


def _personal_default_template(db: Session, user_id: int) -> Optional[IPContentScheduleTemplate]:
    return (
        db.query(IPContentScheduleTemplate)
        .filter(
            IPContentScheduleTemplate.user_id == user_id,
            IPContentScheduleTemplate.name == _PERSONAL_DEFAULT_TEMPLATE_NAME,
            IPContentScheduleTemplate.status == "active",
        )
        .order_by(IPContentScheduleTemplate.updated_at.desc(), IPContentScheduleTemplate.id.desc())
        .first()
    )


def _first_req_text(requirements: dict[str, Any], *keys: str, limit: int = 500) -> str:
    req = requirements if isinstance(requirements, dict) else {}
    basic = req.get("basic_profile") if isinstance(req.get("basic_profile"), dict) else {}
    business = req.get("business_description") if isinstance(req.get("business_description"), dict) else {}
    aliases = {
        "name": ["profile_name", "name"],
        "gender": ["gender", "sex"],
        "photo": ["profile_photo_asset_id", "profile_photo_url", "photo_asset_id", "photo_url", "portrait_asset_id", "portrait_url"],
        "birth_era": ["birth_era"],
        "current_province": ["current_province", "province"],
        "current_city": ["current_city", "city"],
        "hometown": ["hometown"],
        "role": ["role", "identity"],
        "share_topic": ["share_topic", "industry"],
        "video_style": ["video_style", "style"],
        "after_view_action": ["after_view_action", "cta"],
        "product": ["product", "business", "industry"],
        "target_customer": ["target_customer", "target_age"],
        "advantages": ["advantages", "advantage", "differentiator"],
    }
    expanded: list[str] = []
    for key in keys:
        expanded.extend(aliases.get(key, [key]))
    for key in expanded:
        for source in (req, basic, business):
            text = _clean_text(source.get(key) if isinstance(source, dict) else "", limit)
            if text:
                return text
    return ""


def _missing_sales_persona_fields(requirements: dict[str, Any]) -> list[str]:
    req = requirements if isinstance(requirements, dict) else {}
    profile = _local_bestseller_profile_from_persona(req)
    photo = _first_req_text(req, "photo", limit=1000) or _clean_text(profile.get("photo_asset_id") or profile.get("photo_url"), 1000)
    checks = [
        ("你的名字", _first_req_text(req, "name")),
        ("性别", _first_req_text(req, "gender") or _clean_text(profile.get("gender"))),
        ("出生年代", _first_req_text(req, "birth_era") or _clean_text(profile.get("age_label"))),
        ("现居省份", _first_req_text(req, "current_province") or _clean_text(profile.get("province"))),
        ("现居城市", _first_req_text(req, "current_city") or _clean_text(profile.get("city"))),
        ("籍贯", _first_req_text(req, "hometown") or _clean_text(profile.get("hometown"))),
        ("你是做什么的", _first_req_text(req, "role") or _clean_text(profile.get("identity"))),
        ("主要分享什么", _first_req_text(req, "share_topic") or _clean_text(profile.get("industry"))),
        ("视频风格", _first_req_text(req, "video_style") or _clean_text(profile.get("style"))),
        ("看完后希望用户做什么", _first_req_text(req, "after_view_action")),
        ("产品/业务描述", _first_req_text(req, "product") or _clean_text(profile.get("industry"))),
        ("目标客户", _first_req_text(req, "target_customer") or _clean_text(profile.get("target_age"))),
        ("你的优势/比同行好在哪", _first_req_text(req, "advantages")),
        ("人物照片", photo),
    ]
    return [label for label, value in checks if not _clean_text(value, 1000)]


def _active_keywords_for_ids(db: Session, user_id: int, ids: list[int]) -> list[IPContentKeyword]:
    if not ids:
        return []
    return (
        db.query(IPContentKeyword)
        .filter(IPContentKeyword.user_id == user_id, IPContentKeyword.status == "active", IPContentKeyword.id.in_(ids))
        .order_by(IPContentKeyword.created_at.desc(), IPContentKeyword.id.desc())
        .all()
    )


def _has_active_keywords(db: Session, user_id: int) -> bool:
    return (
        db.query(IPContentKeyword.id)
        .filter(IPContentKeyword.user_id == user_id, IPContentKeyword.status == "active")
        .first()
        is not None
    )


def _active_competitors_for_ids(db: Session, user_id: int, ids: list[int]) -> list[ContentCompetitorAccount]:
    if not ids:
        return []
    return (
        db.query(ContentCompetitorAccount)
        .filter(ContentCompetitorAccount.user_id == user_id, ContentCompetitorAccount.status == "active", ContentCompetitorAccount.id.in_(ids))
        .order_by(ContentCompetitorAccount.created_at.desc(), ContentCompetitorAccount.id.desc())
        .all()
    )


def _has_active_competitors(db: Session, user_id: int) -> bool:
    return (
        db.query(ContentCompetitorAccount.id)
        .filter(ContentCompetitorAccount.user_id == user_id, ContentCompetitorAccount.status == "active")
        .first()
        is not None
    )


def _has_active_memory_docs(db: Session, user_id: int, installation_id: str) -> bool:
    query = db.query(OpenClawMemoryDocument.id).filter(
        OpenClawMemoryDocument.target_user_id == user_id,
        OpenClawMemoryDocument.status == "active",
    )
    iid = _clean_text(installation_id, 128)
    if iid:
        query = query.filter(OpenClawMemoryDocument.installation_id == iid)
    return query.first() is not None


def _current_personal_schedule_template(
    db: Session,
    user_id: int,
    personal: Optional[IPContentScheduleTemplate],
) -> Optional[IPContentScheduleTemplate]:
    meta = personal.meta if personal and isinstance(personal.meta, dict) else {}
    try:
        template_id = int(meta.get("current_template_id") or meta.get("template_id") or 0)
    except Exception:
        template_id = 0
    if template_id <= 0:
        return None
    row = (
        db.query(IPContentScheduleTemplate)
        .filter(IPContentScheduleTemplate.id == template_id, IPContentScheduleTemplate.status == "active")
        .first()
    )
    if row is None:
        return None
    if int(row.user_id) == int(user_id):
        return row
    grant = (
        db.query(H5AgentTemplateGrant.id)
        .filter(
            H5AgentTemplateGrant.template_id == row.id,
            H5AgentTemplateGrant.owner_user_id == row.user_id,
            H5AgentTemplateGrant.target_user_id == user_id,
            H5AgentTemplateGrant.status == "active",
        )
        .first()
    )
    return row if grant else None


def _latest_hifly_avatar(db: Session, user_id: int) -> str:
    row = (
        db.query(UserHiflyAvatarAsset)
        .filter(
            UserHiflyAvatarAsset.user_id == user_id,
            UserHiflyAvatarAsset.status == "success",
            UserHiflyAvatarAsset.hifly_avatar_id.isnot(None),
        )
        .order_by(UserHiflyAvatarAsset.updated_at.desc(), UserHiflyAvatarAsset.id.desc())
        .first()
    )
    return _clean_text(row.hifly_avatar_id if row else "", 128)


def _latest_hifly_voice(db: Session, user_id: int) -> str:
    row = (
        db.query(UserHiflyVoiceAsset)
        .filter(
            UserHiflyVoiceAsset.user_id == user_id,
            UserHiflyVoiceAsset.status == "success",
            UserHiflyVoiceAsset.hifly_voice_id.isnot(None),
        )
        .order_by(UserHiflyVoiceAsset.updated_at.desc(), UserHiflyVoiceAsset.id.desc())
        .first()
    )
    return _clean_text(row.hifly_voice_id if row else "", 128)


def _latest_shanjian_virtualman(db: Session, user_id: int) -> str:
    row = (
        db.query(ShanjianDigitalHumanProfile)
        .filter(
            ShanjianDigitalHumanProfile.user_id == user_id,
            ShanjianDigitalHumanProfile.status == "succeed",
            ShanjianDigitalHumanProfile.virtualman_id.isnot(None),
        )
        .order_by(
            ShanjianDigitalHumanProfile.is_default.desc(),
            ShanjianDigitalHumanProfile.updated_at.desc(),
            ShanjianDigitalHumanProfile.id.desc(),
        )
        .first()
    )
    return _clean_text(row.virtualman_id if row else "", 128)


def _available_shanjian_virtualmans(db: Session, user_id: int) -> list[dict[str, Any]]:
    rows = (
        db.query(ShanjianDigitalHumanProfile)
        .filter(
            ShanjianDigitalHumanProfile.user_id == user_id,
            ShanjianDigitalHumanProfile.status == "succeed",
            ShanjianDigitalHumanProfile.virtualman_id.isnot(None),
        )
        .order_by(ShanjianDigitalHumanProfile.id.asc())
        .all()
    )
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        virtualman_id = _clean_text(row.virtualman_id, 128)
        if not virtualman_id or virtualman_id in seen:
            continue
        seen.add(virtualman_id)
        candidates.append(
            {
                "profile_id": int(row.id),
                "virtualman_id": virtualman_id,
                "title": _clean_text(row.title, 128),
                "cover_url": _clean_text(row.cover_url, 1000),
            }
        )
    return candidates


def _template_language(requirements: dict[str, Any], template: Optional[IPContentScheduleTemplate]) -> str:
    req = requirements if isinstance(requirements, dict) else {}
    meta = template.meta if template and isinstance(template.meta, dict) else {}
    raw = _clean_text(
        req.get("language")
        or req.get("target_language")
        or meta.get("language")
        or meta.get("target_language")
        or meta.get("profile_language")
        or "zh-CN",
        64,
    )
    lowered = raw.lower()
    if lowered in {"zh", "zh-cn", "中文", "简体中文", "chinese"}:
        return "zh-CN"
    if lowered in {"en", "en-us", "english", "英文", "英语"}:
        return "en-US"
    if lowered in {"ja", "ja-jp", "japanese", "日文", "日语"}:
        return "ja-JP"
    if lowered in {"ko", "ko-kr", "korean", "韩文", "韩语"}:
        return "ko-KR"
    return raw or "zh-CN"


def _sales_digital_human_provider(
    snapshot_extra: Optional[dict[str, Any]],
    template: Optional[IPContentScheduleTemplate],
) -> str:
    snapshot = snapshot_extra if isinstance(snapshot_extra, dict) else {}
    meta = template.meta if template and isinstance(template.meta, dict) else {}
    req = template.requirements if template and isinstance(template.requirements, dict) else {}
    raw = _clean_text(
        snapshot.get("sales_digital_human_provider")
        or snapshot.get("digital_human_provider")
        or meta.get("sales_digital_human_provider")
        or meta.get("digital_human_provider")
        or req.get("sales_digital_human_provider")
        or req.get("digital_human_provider")
        or os.environ.get("LOBSTER_H5_SALES_DIGITAL_HUMAN_PROVIDER")
        or _SALES_DH_PROVIDER_V2,
        64,
    ).lower()
    if raw in {"old", "legacy", "v1", "1", "1.0", "hifly", "hifly_legacy", "hifly_v1"}:
        return _SALES_DH_PROVIDER_LEGACY
    if raw in {"new", "v2", "2", "2.0", "shanjian", "shanjian_v2", "digital_human_2", "digital_human_2_0"}:
        return _SALES_DH_PROVIDER_V2
    return _SALES_DH_PROVIDER_V2


def _mounted_default(db: Session, user_id: int, scope: str) -> Optional[H5MountedAccountDefault]:
    return (
        db.query(H5MountedAccountDefault)
        .filter(H5MountedAccountDefault.user_id == user_id, H5MountedAccountDefault.scope == scope)
        .first()
    )


def _publish_default_scope(platform: str) -> str:
    platform_key = _clean_text(platform, 64).lower()
    return f"publish:{platform_key}" if platform_key else "publish"


def _mounted_publish_default(db: Session, user_id: int, platform: str) -> Optional[H5MountedAccountDefault]:
    platform_key = _clean_text(platform, 64).lower()
    if not platform_key:
        return None
    scoped = _mounted_default(db, user_id, _publish_default_scope(platform_key))
    if scoped:
        return scoped
    legacy = _mounted_default(db, user_id, "publish")
    if legacy and _clean_text(legacy.platform, 64).lower() == platform_key:
        return legacy
    return None


def _mounted_default_installation_id(row: H5MountedAccountDefault) -> str:
    raw = _clean_text(row.installation_id, 128)
    if raw:
        return raw
    payload = row.payload if isinstance(row.payload, dict) else {}
    raw = _clean_text(payload.get("installation_id"), 128)
    if raw:
        return raw
    key = _clean_text(payload.get("select_id") or row.account_key, 255)
    head = _clean_text(key.split(":", 1)[0] if ":" in key else "", 128)
    if head and head not in {"server", "wechat"} and len(head) >= 8:
        return head
    return ""


def _device_is_online(db: Session, user_id: int, installation_id: str) -> bool:
    iid = _clean_text(installation_id, 128)
    if not iid:
        return False
    row = (
        db.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.user_id == user_id, H5ChatDevicePresence.installation_id == iid)
        .first()
    )
    if not row or not row.last_seen_at:
        return False
    return (datetime.utcnow() - row.last_seen_at).total_seconds() <= _DEVICE_ONLINE_TTL_SECONDS


def _workflow_child_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = node.get("children") if isinstance(node.get("children"), list) else node.get("actions")
    return [item for item in (children or []) if isinstance(item, dict)]


def _workflow_nodes_with_actions(nodes: list[dict[str, Any]]) -> list[tuple[dict[str, Any], Optional[dict[str, Any]]]]:
    out: list[tuple[dict[str, Any], Optional[dict[str, Any]]]] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        if _is_workflow_placeholder(node):
            out.append((node, None))
            continue
        out.append((node, None))
        for child in _workflow_child_nodes(node):
            out.append((child, node))
    return out


def _apply_workflow_runtime_options(
    nodes: list[dict[str, Any]],
    *,
    local_bestseller_plan_day: Optional[int] = None,
) -> list[dict[str, Any]]:
    digital_human_slot = 0
    plan_day = (
        _safe_int(local_bestseller_plan_day, 1, min_value=1, max_value=30)
        if local_bestseller_plan_day is not None
        else None
    )
    for node, _parent in _workflow_nodes_with_actions(nodes):
        plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
        payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
        if _clean_text(plan.get("task_kind"), 64) != "client_workflow":
            continue
        action = _clean_text(payload.get("action"), 128)
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        params = dict(params)
        if action == "local_bestseller_daily_video" and plan_day is not None:
            # The activation choice is the first day of a recurring employee
            # workflow, not a fixed day to repeat forever.
            params.pop("day", None)
            params["start_day"] = plan_day
            params["day_mode"] = "workflow_elapsed"
        if action == "shanjian_digital_human_video":
            params["virtualman_rotation_slot"] = digital_human_slot
            params["virtualman_selection_mode"] = "daily_sequence"
            digital_human_slot += 1
        if action == "native_wechat_poll":
            session_minutes = _workflow_minutes_between(node.get("time"), node.get("end_time"))
            if session_minutes > 0:
                params["takeover_session_minutes"] = session_minutes
            else:
                params.pop("takeover_session_minutes", None)
            params["message_poll_interval_seconds"] = max(
                1,
                _safe_int(params.get("message_poll_interval_seconds") or 15, 15, min_value=1, max_value=300),
            )
            params["accept_friend_requests_once"] = params.get("accept_friend_requests_once", True) is not False
        payload["params"] = params
        plan["payload"] = payload
        node["plan"] = plan
    return nodes


_SALES_DIGITAL_HUMAN_REQUEST_TEMPLATE_KEYS = {
    "use_template",
    "template_scene",
    "style_id",
    "materials",
    "material_sound_switch",
    "introduce_name",
    "introduce_description",
    "header_switch",
    "material_switch",
    "subtitle_switch",
    "keyword_switch",
    "watermark_show",
    "material_match_way",
    "resource_preprocess_method",
    "material_composition",
}


def _apply_sales_digital_human_defaults(params: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(params or {})
    for key in _SALES_DIGITAL_HUMAN_REQUEST_TEMPLATE_KEYS:
        normalized.pop(key, None)
    normalized["long_video"] = False
    normalized["template_mode"] = "active_personal_template"
    return normalized


def _sales_digital_human_template_id(
    personal: Optional[IPContentScheduleTemplate],
    current: Optional[IPContentScheduleTemplate],
) -> str:
    personal_meta = personal.meta if personal and isinstance(personal.meta, dict) else {}
    current_meta = current.meta if current and isinstance(current.meta, dict) else {}
    raw = current_meta.get("digital_human_template") if "digital_human_template" in current_meta else personal_meta.get("digital_human_template")
    if not isinstance(raw, dict):
        return ""
    return _clean_text(raw.get("style_id") or raw.get("styleId") or raw.get("id"), 128)


def _prepare_publish_action_nodes(
    *,
    db: Session,
    owner: User,
    installation_id: str,
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prepared = copy.deepcopy(nodes)
    missing: list[str] = []
    for parent in prepared:
        if _is_workflow_placeholder(parent):
            continue
        for child in _workflow_child_nodes(parent):
            if _is_workflow_placeholder(child):
                continue
            plan = child.get("plan") if isinstance(child.get("plan"), dict) else {}
            payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
            action = _clean_text(payload.get("action"), 128)
            params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            if action != "publish_content":
                continue
            params = dict(params)
            platform = _clean_text(child.get("platform") or params.get("platform"), 64).lower()
            if platform not in _WORKFLOW_ACTION_PLATFORMS:
                missing.append("发布动作：请选择抖音、头条、视频号或朋友圈")
                continue
            if platform == "wechat_moments":
                current_iid = _clean_text(installation_id, 128)
                if not _device_is_online(db, owner.id, current_iid):
                    missing.append("发布朋友圈：当前启用设备不在线")
                    continue
                params.update(
                    {
                        "platform": "wechat_moments",
                        "platform_name": _workflow_platform_label("wechat_moments"),
                        "account_id": "pc-wechat-default",
                        "account_nickname": "本机微信",
                        "publish_installation_id": current_iid,
                        "installation_id": current_iid,
                        "source_mode": "parent_latest_run",
                        "source_workflow_node_id": _clean_text(child.get("parent_node_id") or parent.get("id"), 64),
                        "source_workflow_node_label": _clean_text(parent.get("ability_label") or parent.get("note"), 160),
                        "media_type": _clean_text(params.get("media_type"), 32) or "image_text",
                        "ai_publish_copy": bool(params.get("ai_publish_copy", True)),
                    }
                )
                payload["params"] = params
                plan["payload"] = payload
                child["plan"] = plan
                continue
            publish_default = _mounted_publish_default(db, owner.id, platform)
            if not publish_default:
                missing.append(f"发布{_workflow_platform_label(platform)}：请先在个人中心设置默认发布账号")
                continue
            default_platform = _clean_text(publish_default.platform, 64).lower()
            if default_platform != platform:
                missing.append(f"发布{_workflow_platform_label(platform)}：默认发布账号不是{_workflow_platform_label(platform)}账号")
                continue
            default_iid = _mounted_default_installation_id(publish_default)
            if default_iid != _clean_text(installation_id, 128):
                missing.append(f"发布{_workflow_platform_label(platform)}：默认发布账号不在当前启用设备上")
                continue
            if not _device_is_online(db, owner.id, default_iid):
                missing.append(f"发布{_workflow_platform_label(platform)}：默认发布账号所在设备不在线")
                continue
            params.update(
                {
                    "platform": default_platform,
                    "platform_name": _workflow_platform_label(default_platform),
                    "account_id": _clean_text(publish_default.account_id, 128),
                    "account_nickname": _clean_text(publish_default.account_label, 255),
                    "publish_installation_id": default_iid,
                    "installation_id": default_iid,
                    "source_mode": "parent_latest_run",
                    "source_workflow_node_id": _clean_text(child.get("parent_node_id") or parent.get("id"), 64),
                    "source_workflow_node_label": _clean_text(parent.get("ability_label") or parent.get("note"), 160),
                    "media_type": _clean_text(params.get("media_type"), 32) or "video",
                    "ai_publish_copy": bool(params.get("ai_publish_copy", True)),
                }
            )
            payload["params"] = params
            plan["payload"] = payload
            child["plan"] = plan
    if missing:
        raise HTTPException(status_code=400, detail="；".join(dict.fromkeys(missing)))
    return prepared


def _sales_action_from_note(note: Any) -> str:
    text = _clean_text(note, 200)
    if "养号" in text:
        return "account_nurture"
    if "发布后采集" in text or "关键词抓取" in text:
        return "search_collect"
    if "回复" in text and "评论" in text:
        return "reply_comments"
    if "@精准" in text or "评论并@" in text or "自己评论区接管" in text:
        return "mention_comment"
    if "关注" in text and "评论" in text:
        return "follow_comment"
    if "主动私信" in text or "私信10" in text:
        return "direct_message"
    if "私信接管" in text or "私信引流" in text:
        return "stranger_message"
    return "search_collect"


def _sales_douyin_action_payload(node: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Reduce a sales Douyin node to the action-only Online contract."""
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    inferred_action = _sales_action_from_note(node.get("note") or node.get("ability_label"))
    action = inferred_action
    if action == "search_collect":
        action = _clean_text(params.get("sales_action"), 64)
    if not action or action == "search_collect":
        requested_action = _clean_text(payload.get("action"), 64)
        if requested_action and requested_action != "search_collect":
            action = requested_action
    if not action:
        action = inferred_action
    result: dict[str, Any] = {"action": action or "search_collect"}
    # Private-message takeover keeps add-friend behavior on the parent node.
    # Older templates may still contain a child; the migration below converts
    # that child into this explicit Online contract.
    if action == "stranger_message" and (
        "wechat_add_friend_enabled" in params or "reply_mode" in params
    ):
        result["params"] = {
            "wechat_add_friend_enabled": _bool_param(params.get("wechat_add_friend_enabled"), False),
            "wechat_add_friend_targets_source": _clean_text(params.get("wechat_add_friend_targets_source"), 128)
            or "douyin_private_message_phone",
        }
        if "reply_mode" in params:
            raw_reply_mode = _clean_text(params.get("reply_mode"), 32).lower()
            result["params"]["reply_mode"] = raw_reply_mode if raw_reply_mode in {"fixed", "ai_lead"} else "fixed"
    return result


_NATIVE_WECHAT_WORKFLOW_ACTIONS = _WORKFLOW_CHILD_CLIENT_ACTIONS


def _native_wechat_key_from_sales_note(note: Any) -> str:
    text = _clean_text(note, 200)
    if "自动加好友" in text:
        return "native_wechat_add_friend"
    if "自动拉群" in text:
        return "native_wechat_poll"
    if "朋友圈点赞" in text or "朋友圈评论" in text:
        return "native_wechat_moments_engage"
    if "私信接管" in text:
        return "native_wechat_poll"
    return ""


def _native_wechat_plan(action_key: str, note: Any, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    note_text = _clean_text(note, 2000)
    base_params = dict(params or {})
    base_params.setdefault("account_id", "pc-wechat-default")
    base_params.setdefault("note", note_text)
    base_params.setdefault("prompt", note_text)
    group_invite = _bool_param(base_params.get("group_invite_enabled"), False)
    if "group_invite_enabled" in base_params:
        base_params["group_invite_enabled"] = group_invite
    if group_invite:
        base_params["group_invite_enabled"] = True
        base_params.setdefault("group_invite_rule_status", "pending_rules")
        base_params.setdefault("trigger", "qualified_intent")
    if action_key == "native_wechat_poll":
        title = "个微私信接管"
        session_minutes = _workflow_minutes_between(
            base_params.get("workflow_node_time") or base_params.get("sales_schedule_start") or base_params.get("time"),
            base_params.get("workflow_node_end_time")
            or base_params.get("sales_schedule_end")
            or base_params.get("end_time"),
        )
        if session_minutes <= 0:
            session_minutes = 30
        base_params["takeover_session_minutes"] = session_minutes
        base_params["message_poll_interval_seconds"] = 15
        base_params["accept_friend_requests_once"] = True
        return {
            "title": title,
            "task_kind": "client_workflow",
            "content": f"H5 工作流：{title}",
            "payload": {"action": action_key, "params": base_params},
        }
    if action_key == "native_wechat_add_friend":
        base_params.setdefault("targets", [])
        return {
            "title": "个微自动加好友",
            "task_kind": "client_workflow",
            "content": "H5 工作流：个微自动加好友",
            "payload": {"action": action_key, "params": base_params},
        }
    if action_key == "native_wechat_moments_engage":
        moment_params = {
            key: value
            for key, value in base_params.items()
            if key not in {
                "group_invite_enabled",
                "group_invite_memory_doc_id",
                "group_invite_keywords",
                "group_invite_contacts",
                "group_invite_primary_contact",
                "group_invite_primary_contact_name",
                "group_invite_welcome_message",
                "group_invite_rule_status",
                "group_invite_targets_source",
                "group_invite_members",
                "group_invite_manager_contacts",
                "followup_action",
                "group_invite_rules",
                "trigger",
            }
        }
        moment_params.setdefault("targets", [])
        moment_params.setdefault("moment_action", "like_comment")
        moment_params.setdefault("max_scrolls", 6)
        return {
            "title": "朋友圈点赞评论",
            "task_kind": "client_workflow",
            "content": "H5 工作流：朋友圈点赞评论",
            "payload": {"action": action_key, "params": moment_params},
        }
    return {}


def _is_sales_workflow(template_name: str, nodes: list[dict[str, Any]], snapshot_extra: Optional[dict[str, Any]]) -> bool:
    template_key = _clean_text((snapshot_extra or {}).get("template_key"), 128)
    if template_key == "system_sales":
        return True
    if "销售" in _clean_text(template_name, 160):
        return True
    for node in nodes or []:
        if bool(node.get("sales_preset") or node.get("salesPreset")):
            return True
        if str(node.get("id") or "").startswith("sales_"):
            return True
        if _clean_text(node.get("department_id"), 64) == "sales":
            return True
    return False


def _node_payload(node: dict[str, Any]) -> dict[str, Any]:
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    return plan.get("payload") if isinstance(plan.get("payload"), dict) else {}


def _normalize_sales_native_wechat_node(node: dict[str, Any]) -> None:
    if not isinstance(node, dict):
        return
    if _is_workflow_placeholder(node):
        return
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    action = _clean_text(payload.get("action") or params.get("action"), 128)
    is_sales = (
        bool(node.get("sales_preset") or node.get("salesPreset"))
        or _clean_text(node.get("id"), 80).startswith("sales_")
        or _clean_text(node.get("department_id") or node.get("departmentId"), 64) == "sales"
    )
    if is_sales and action == "wecom_poll_reply":
        note = _clean_text(node.get("note") or node.get("ability_label") or plan.get("title"), 2000)
        native_key = _native_wechat_key_from_sales_note(note)
        native_params = dict(params)
        native_params.setdefault("workflow_node_time", _clean_text(node.get("time"), 5))
        native_params.setdefault("workflow_node_end_time", _clean_text(node.get("end_time"), 5))
        native_plan = _native_wechat_plan(native_key, note, native_params) if native_key else {}
        if native_plan:
            node["ability_key"] = native_key
            node["department_id"] = "sales"
            node["department_name"] = "销售部"
            node["plan"] = native_plan
    for child in _workflow_child_nodes(node):
        _normalize_sales_native_wechat_node(child)


def _workflow_time_after(value: Any, minutes: int = 15) -> str:
    text = _clean_text(value, 5)
    match = _TIME_RE.match(text)
    if not match:
        return "00:15"
    total = (int(match.group(1)) * 60 + int(match.group(2)) + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _workflow_minutes_between(start: Any, end: Any, *, default: int = 30) -> int:
    start_text = _clean_text(start, 5)
    end_text = _clean_text(end, 5)
    start_match = _TIME_RE.match(start_text)
    end_match = _TIME_RE.match(end_text)
    if not start_match or not end_match:
        return max(1, int(default))
    start_total = int(start_match.group(1)) * 60 + int(start_match.group(2))
    end_total = int(end_match.group(1)) * 60 + int(end_match.group(2))
    if end_total < start_total:
        end_total += 24 * 60
    return max(1, end_total - start_total)


def _is_douyin_private_takeover_node(node: dict[str, Any]) -> bool:
    payload = _node_payload(node)
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    text = _clean_text(
        " ".join(
            str(value or "")
            for value in (node.get("ability_label"), node.get("note"), plan.get("title"))
        ),
        500,
    )
    return (
        _clean_text(plan.get("task_kind"), 64) == "douyin_leads"
        and (
            _clean_text(payload.get("action"), 64) == "stranger_message"
            or "抖音私信接管" in text
        )
    )


def _is_native_wechat_add_friend_node(node: dict[str, Any]) -> bool:
    payload = _node_payload(node)
    return (
        _clean_text(node.get("ability_key"), 128) == "native_wechat_add_friend"
        or _clean_text(payload.get("action"), 128) == "native_wechat_add_friend"
    )


def _ensure_sales_douyin_add_friend_children(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Migrate legacy top-level add-friend rows under every Douyin takeover node."""
    parents = [node for node in nodes if isinstance(node, dict) and _is_douyin_private_takeover_node(node)]
    if not parents:
        return nodes

    legacy_rows = [node for node in nodes if isinstance(node, dict) and _is_native_wechat_add_friend_node(node)]
    prepared = [node for node in nodes if not (isinstance(node, dict) and _is_native_wechat_add_friend_node(node))]
    legacy_params: dict[str, Any] = {}
    if legacy_rows:
        raw_params = _node_payload(legacy_rows[0]).get("params")
        if isinstance(raw_params, dict):
            legacy_params = dict(raw_params)

    for parent in parents:
        parent_id = _clean_text(parent.get("id"), 64)
        children = list(_workflow_child_nodes(parent))
        existing = next((child for child in children if _is_native_wechat_add_friend_node(child)), None)
        if existing is None:
            child_time = _workflow_time_after(parent.get("time"), 15)
            existing = {
                "id": f"{parent_id}_native_add_friend"[:64],
                "time": child_time,
                "parent_node_id": parent_id,
                "action_type": "native_wechat_add_friend",
                "type": "native_wechat_add_friend",
                "ability_key": "native_wechat_add_friend",
                "ability_label": "微信自动加好友",
                "department_id": _clean_text(parent.get("department_id"), 64) or "sales",
                "department_name": _clean_text(parent.get("department_name"), 80) or "销售部",
                "note": "识别抖音私信中客户发送的手机号并自动加好友，没有手机号则跳过",
                "sales_preset": True,
                "is_action_node": True,
                "param_configured": True,
            }
            children.append(existing)

        params = dict(legacy_params)
        current_params = _node_payload(existing).get("params")
        if isinstance(current_params, dict):
            params.update(current_params)
        params.update(
            {
                "source_workflow_node_id": parent_id,
                "source_workflow_node_label": _clean_text(parent.get("ability_label") or parent.get("note"), 160),
                "source_mode": "douyin_private_message_phone",
                "trigger": "clear_mobile",
                "skip_without_clear_mobile": True,
                "targets": [],
            }
        )
        existing["parent_node_id"] = parent_id
        existing["action_type"] = "native_wechat_add_friend"
        existing["type"] = "native_wechat_add_friend"
        existing["ability_key"] = "native_wechat_add_friend"
        if _clean_text(existing.get("time"), 5) in {"", _clean_text(parent.get("time"), 5)}:
            existing["time"] = _workflow_time_after(parent.get("time"), 15)
        existing["plan"] = _native_wechat_plan("native_wechat_add_friend", existing.get("note"), params)
        children.sort(key=lambda child: _clean_text(child.get("time"), 5))
        parent["children"] = children

        parent_plan = parent.get("plan") if isinstance(parent.get("plan"), dict) else {}
        parent_payload = parent_plan.get("payload") if isinstance(parent_plan.get("payload"), dict) else {}
        parent_params = dict(parent_payload.get("params") if isinstance(parent_payload.get("params"), dict) else {})
        rules = [
            rule
            for rule in parent_params.get("wechat_add_friend_rules", [])
            if isinstance(rule, dict) and _clean_text(rule.get("child_node_id"), 64) != _clean_text(existing.get("id"), 64)
        ]
        rules.append(
            {
                "child_node_id": existing.get("id"),
                "time": existing.get("time"),
                "trigger": "clear_mobile",
                "skip_without_clear_mobile": True,
            }
        )
        parent_params.update(
            {
                "wechat_add_friend_enabled": True,
                "wechat_add_friend_targets_source": "douyin_private_message_phone",
                "wechat_add_friend_rules": rules,
            }
        )
        parent_payload["params"] = parent_params
        parent_plan["payload"] = parent_payload
        parent["plan"] = parent_plan

    return prepared


def _ensure_sales_douyin_add_friend_children(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Migrate legacy add-friend children into the Douyin parent switch.

    The name is retained for old callers, but new data never gets a child
    action. A legacy child only supplies the initial switch value.
    """
    parents = [node for node in nodes if isinstance(node, dict) and _is_douyin_private_takeover_node(node)]
    if not parents:
        return nodes

    legacy_rows = [node for node in nodes if isinstance(node, dict) and _is_native_wechat_add_friend_node(node)]
    prepared = [node for node in nodes if not (isinstance(node, dict) and _is_native_wechat_add_friend_node(node))]
    for parent in parents:
        parent_plan = parent.get("plan") if isinstance(parent.get("plan"), dict) else {}
        parent_payload = parent_plan.get("payload") if isinstance(parent_plan.get("payload"), dict) else {}
        parent_params = dict(parent_payload.get("params") if isinstance(parent_payload.get("params"), dict) else {})
        current_enabled = parent_params.get("wechat_add_friend_enabled")
        has_legacy_child = any(
            _is_native_wechat_add_friend_node(child)
            for child in _workflow_child_nodes(parent)
        )
        parent_params["wechat_add_friend_enabled"] = (
            _bool_param(current_enabled, False)
            if current_enabled is not None
            else bool(has_legacy_child or legacy_rows)
        )
        parent_params["wechat_add_friend_targets_source"] = "douyin_private_message_phone"
        parent_params.pop("wechat_add_friend_rules", None)
        parent_payload["params"] = parent_params
        parent_plan["payload"] = parent_payload
        parent["plan"] = parent_plan

        remaining_children = [
            child for child in _workflow_child_nodes(parent)
            if not _is_native_wechat_add_friend_node(child)
        ]
        if remaining_children:
            parent["children"] = remaining_children
        else:
            parent.pop("children", None)
    return prepared


def _prepare_sales_workflow_nodes(
    *,
    db: Session,
    owner: User,
    installation_id: str,
    template_name: str,
    nodes: list[dict[str, Any]],
    snapshot_extra: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not _is_sales_workflow(template_name, nodes, snapshot_extra):
        return nodes

    prepared = copy.deepcopy(nodes)
    for node in prepared:
        _normalize_sales_native_wechat_node(node)
    prepared = _ensure_sales_douyin_add_friend_children(prepared)
    personal = _personal_default_template(db, owner.id)
    current_template = _current_personal_schedule_template(db, owner.id, personal)
    reference_template = current_template or personal
    digital_human_template_id = _sales_digital_human_template_id(personal, current_template)
    reference_owner_id = int(reference_template.user_id) if reference_template else int(owner.id)
    requirements = personal.requirements if personal and isinstance(personal.requirements, dict) else {}
    if current_template and isinstance(current_template.requirements, dict):
        merged_requirements = dict(requirements)
        merged_requirements.update(current_template.requirements)
        requirements = merged_requirements
    keyword_ids = _clean_id_list(reference_template.keyword_ids if reference_template else [])
    competitor_ids = _clean_id_list(reference_template.competitor_ids if reference_template else [])
    keywords = _active_keywords_for_ids(db, reference_owner_id, keyword_ids)
    competitors = _active_competitors_for_ids(db, reference_owner_id, competitor_ids)
    memory_doc_ids = [str(x or "").strip() for x in ((reference_template.memory_doc_ids if reference_template else []) or []) if str(x or "").strip()]
    memory_docs = reference_template.memory_docs if reference_template and isinstance(reference_template.memory_docs, list) else []
    if memory_doc_ids and not memory_docs:
        numeric_doc_ids = [int(value) for value in memory_doc_ids if value.isdigit()]
        memory_rows = (
            db.query(OpenClawMemoryDocument)
            .filter(
                OpenClawMemoryDocument.target_user_id == reference_owner_id,
                OpenClawMemoryDocument.status == "active",
                OpenClawMemoryDocument.id.in_(numeric_doc_ids),
            )
            .order_by(OpenClawMemoryDocument.updated_at.desc(), OpenClawMemoryDocument.id.desc())
            .limit(12)
            .all()
            if numeric_doc_ids
            else []
        )
        memory_docs = [
            {
                "id": row.id,
                "title": row.title,
                "doc_type": row.doc_type,
                "content": (row.content or "")[:4000],
            }
            for row in memory_rows
        ]
    keyword_texts = [_clean_text(row.display_name or row.keyword, 120) for row in keywords if _clean_text(row.display_name or row.keyword, 120)]
    digital_human_provider = _sales_digital_human_provider(snapshot_extra, reference_template)
    hifly_avatar = _latest_hifly_avatar(db, owner.id) if digital_human_provider == _SALES_DH_PROVIDER_LEGACY else ""
    shanjian_virtualman = _latest_shanjian_virtualman(db, owner.id)
    shanjian_virtualmans = _available_shanjian_virtualmans(db, owner.id)
    hifly_voice = _latest_hifly_voice(db, owner.id)
    template_language = _template_language(requirements, reference_template)

    has_hifly = False
    has_ip_daily = False
    has_local_bestseller = False
    has_wechat = False
    missing: list[str] = []

    for node in prepared:
        if _is_workflow_placeholder(node):
            continue
        plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
        task_kind = _clean_text(plan.get("task_kind"), 64)
        payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
        capability_id = _clean_text(payload.get("capability_id"), 128)
        action = _clean_text(payload.get("action"), 128)

        if task_kind == "ip_content_daily":
            has_ip_daily = True
            payload = dict(payload)
            if personal:
                try:
                    template_id = int(payload.get("template_id") or 0)
                except Exception:
                    template_id = 0
                if template_id <= 0:
                    payload["template_id"] = reference_template.id if reference_template else personal.id
                if not payload.get("keyword_ids"):
                    payload["keyword_ids"] = keyword_ids
                if not payload.get("competitor_ids"):
                    payload["competitor_ids"] = competitor_ids
                if not payload.get("memory_doc_ids"):
                    payload["memory_doc_ids"] = memory_doc_ids
                if not payload.get("memory_docs"):
                    payload["memory_docs"] = memory_docs
                # 销售员工统一从 IP 人设定位取资料，避免节点备注占位文案污染生成内容。
                payload["requirements"] = requirements
                if "sync_before" not in payload:
                    payload["sync_before"] = True
            tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
            normalized_tasks = [task for task in tasks if task in _IP_DAILY_DEFAULT_TASKS]
            payload["tasks"] = normalized_tasks or list(_IP_DAILY_DEFAULT_TASKS)
            plan["payload"] = payload

        if task_kind == "douyin_leads":
            # 销售工作流只触发动作；关键词、账号、话术和节奏统一由 Online 本机配置决定。
            plan["payload"] = _sales_douyin_action_payload(node, payload)

        if task_kind == "client_workflow" and action.startswith("local_bestseller"):
            has_local_bestseller = True

        if task_kind == "client_workflow" and action in _NATIVE_WECHAT_WORKFLOW_ACTIONS:
            has_wechat = True

        if task_kind == "client_workflow" and action == "native_wechat_poll":
            payload = dict(payload)
            params = dict(payload.get("params") if isinstance(payload.get("params"), dict) else {})
            params.setdefault("language", template_language)
            params.setdefault("target_language", template_language)
            payload["params"] = params
            plan["payload"] = payload

        if task_kind == "client_workflow" and action == "shanjian_digital_human_video":
            has_hifly = True
            if digital_human_provider == _SALES_DH_PROVIDER_LEGACY:
                params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                inner = dict(params)
                if hifly_avatar:
                    inner.setdefault("avatar", hifly_avatar)
                if hifly_voice:
                    inner.setdefault("voice", hifly_voice)
                if _clean_text(inner.get("script"), 200) in {
                    _clean_text(node.get("note"), 200),
                    _clean_text(node.get("ability_label"), 200),
                    _clean_text(plan.get("title"), 200),
                    "自动创作一条数字人口播视频",
                }:
                    inner.pop("script", None)
                node["ability_key"] = "hifly.video.create_by_tts"
                plan["task_kind"] = "capability"
                plan["payload"] = {"capability_id": "hifly.video.create_by_tts", "payload": inner}
            else:
                payload = dict(payload)
                params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                params = dict(params)
                params.setdefault("requirements", requirements)
                params.setdefault("keyword_ids", keyword_ids)
                params.setdefault("keywords", keyword_texts)
                params.setdefault("keyword_texts", keyword_texts)
                params.setdefault("competitors", [_clean_text(row.display_name or row.account_name or row.account_id, 160) for row in competitors])
                params.setdefault("memory_doc_ids", memory_doc_ids)
                params.setdefault("memory_docs", memory_docs)
                params.setdefault("language", template_language)
                params.setdefault("target_language", template_language)
                params.setdefault("sales_node_label", _clean_text(node.get("ability_label") or node.get("note") or plan.get("title"), 160))
                params["script_source"] = "ip_daily_industry_hot_oral"
                if shanjian_virtualmans:
                    params["virtualman_candidates"] = shanjian_virtualmans
                    params["virtualman_selection_mode"] = "daily_round_robin"
                if shanjian_virtualman:
                    params.setdefault("virtualman_id", shanjian_virtualman)
                if hifly_voice:
                    params.setdefault("voice", hifly_voice)
                    params.setdefault("speaker_id", hifly_voice)
                params = _apply_sales_digital_human_defaults(params)
                payload["params"] = params
                plan["payload"] = payload

        if task_kind == "capability" and capability_id == "hifly.video.create_by_tts":
            has_hifly = True
            payload = dict(payload)
            inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
            inner = dict(inner)
            placeholder_texts = {
                _clean_text(node.get("note"), 200),
                _clean_text(node.get("ability_label"), 200),
                _clean_text(plan.get("title"), 200),
                "自动创作一条数字人口播视频",
            }
            for script_key in ("script", "text"):
                script_value = _clean_text(inner.get(script_key), 200)
                if script_value in placeholder_texts or script_value.startswith("自动创作"):
                    inner.pop(script_key, None)
            if digital_human_provider == _SALES_DH_PROVIDER_LEGACY:
                if hifly_avatar:
                    inner.setdefault("avatar", hifly_avatar)
                if hifly_voice:
                    inner.setdefault("voice", hifly_voice)
                payload["payload"] = inner
                plan["payload"] = payload
            else:
                params = {
                    key: value
                    for key, value in inner.items()
                    if key not in {"avatar", "avatar_id", "st_show", "aigc_flag"}
                }
                params.setdefault("requirements", requirements)
                params.setdefault("keyword_ids", keyword_ids)
                params.setdefault("keywords", keyword_texts)
                params.setdefault("keyword_texts", keyword_texts)
                params.setdefault("competitors", [_clean_text(row.display_name or row.account_name or row.account_id, 160) for row in competitors])
                params.setdefault("memory_doc_ids", memory_doc_ids)
                params.setdefault("memory_docs", memory_docs)
                params.setdefault("language", template_language)
                params.setdefault("target_language", template_language)
                params.setdefault("sales_node_label", _clean_text(node.get("ability_label") or node.get("note") or plan.get("title"), 160))
                params["script_source"] = "ip_daily_industry_hot_oral"
                if shanjian_virtualmans:
                    params["virtualman_candidates"] = shanjian_virtualmans
                    params["virtualman_selection_mode"] = "daily_round_robin"
                if shanjian_virtualman:
                    params.setdefault("virtualman_id", shanjian_virtualman)
                if hifly_voice:
                    params.setdefault("voice", hifly_voice)
                    params.setdefault("speaker_id", hifly_voice)
                params = _apply_sales_digital_human_defaults(params)
                node["ability_key"] = "shanjian_digital_human_video"
                plan["task_kind"] = "client_workflow"
                plan["payload"] = {"action": "shanjian_digital_human_video", "params": params}

    if not personal:
        missing.append("IP人设定位：请先完成资料调查并保存")
    else:
        profile_missing = _missing_sales_persona_fields(requirements)
        if profile_missing:
            missing.append("IP人设定位-资料调查：" + "、".join(profile_missing))
        if not keywords:
            if _has_active_keywords(db, reference_owner_id):
                missing.append("IP人设定位-模板：请在当前启用模板中选择 1 个行业关键词")
            else:
                missing.append("IP人设定位-关键词：请先添加至少 1 个行业关键词")
        if not competitors:
            if _has_active_competitors(db, reference_owner_id):
                missing.append("IP人设定位-模板：请在当前启用模板中选择 1 个同行账号")
            else:
                missing.append("IP人设定位-同行账号：请先添加至少 1 个同行账号")
        elif not any(row.last_fetch_at for row in competitors):
            missing.append("IP人设定位-同行账号：当前模板选择的同行账号还没有同步数据，请先同步同行账号数据")
        if not (memory_doc_ids or memory_docs):
            if _has_active_memory_docs(db, owner.id, installation_id):
                missing.append("IP人设定位-模板：请在当前启用模板中选择 1 份记忆文件")
            else:
                missing.append("IP人设定位-记忆文件：请先生成或保存至少 1 份记忆文件")

    if has_ip_daily and not personal:
        missing.append("IP日更：缺少当前使用模板")
    if has_wechat and not _device_is_online(db, owner.id, _clean_text(installation_id, 128)):
        missing.append("平台账号：当前启用设备不在线，无法执行个人微信节点")
    if has_hifly:
        if digital_human_provider == _SALES_DH_PROVIDER_LEGACY:
            if not hifly_avatar:
                missing.append("素材库：请先创建可用的旧版数字人形象分身")
        elif not shanjian_virtualmans:
            missing.append("素材库：请先创建并训练完成可用的数字人形象分身（数字人2.0）")
        if digital_human_provider == _SALES_DH_PROVIDER_V2 and not digital_human_template_id:
            missing.append("IP人设定位-模板：请为当前模板选择数字人剪辑模板")
        if not hifly_voice:
            missing.append("素材库：请先创建可用的声音分身")
    if has_local_bestseller and personal:
        profile = _local_bestseller_profile_from_persona(requirements)
        if not (_clean_text(profile.get("photo_asset_id"), 128) or _clean_text(profile.get("photo_url"), 1000)):
            missing.append("同城爆款视频：缺少人物照片")

    if missing:
        detail = "销售员工无法启动，缺少：" + "；".join(dict.fromkeys(missing)) + "。请到 IP人设定位、素材库或个人中心补足后再启用。"
        raise HTTPException(status_code=400, detail=detail)
    return prepared


def _template_payload(row: H5WorkflowTemplate, *, owner: Optional[User] = None, source: str = "own", grants: Optional[list[int]] = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "owner_user_id": row.owner_user_id,
        "installation_id": _clean_text(row.installation_id, 128),
        "owner_name": owner.email if owner else "",
        "name": row.name,
        "nodes": _canonical_workflow_nodes(row.nodes),
        "status": row.status,
        "source": source,
        "meta": row.meta or {},
        "granted_user_ids": grants or [],
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _activation_payload(row: H5WorkflowActivation, template: Optional[H5WorkflowTemplate] = None) -> dict[str, Any]:
    snapshot = row.template_snapshot if isinstance(row.template_snapshot, dict) else {}
    template_nodes = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else None
    if template_nodes is None and template is not None:
        template_nodes = template.nodes or []
    return {
        "id": row.id,
        "user_id": row.user_id,
        "installation_id": row.installation_id,
        "template_id": row.template_id,
        "template_key": snapshot.get("template_key") or "",
        "template_source": snapshot.get("source") or "",
        "template_name": template.name if template else snapshot.get("name", ""),
        "template_nodes": _canonical_workflow_nodes(template_nodes),
        "status": row.status,
        "scheduled_task_ids": row.scheduled_task_ids or [],
        "started_at": _iso(row.started_at),
        "stopped_at": _iso(row.stopped_at),
        "updated_at": _iso(row.updated_at),
    }


def _own_template(db: Session, template_id: int, owner_user_id: int) -> H5WorkflowTemplate:
    row = (
        db.query(H5WorkflowTemplate)
        .filter(
            H5WorkflowTemplate.id == template_id,
            H5WorkflowTemplate.owner_user_id == owner_user_id,
            H5WorkflowTemplate.status == "active",
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="模板不存在")
    return row


def _system_workflow_template(
    db: Session,
    owner_user_id: int,
    system_template_key: str,
    *,
    exclude_template_id: Optional[int] = None,
    installation_id: str = "",
) -> Optional[H5WorkflowTemplate]:
    query = db.query(H5WorkflowTemplate).filter(
        H5WorkflowTemplate.owner_user_id == owner_user_id,
        H5WorkflowTemplate.status == "active",
        or_(H5WorkflowTemplate.installation_id == installation_id, H5WorkflowTemplate.installation_id == ""),
    )
    if exclude_template_id is not None:
        query = query.filter(H5WorkflowTemplate.id != exclude_template_id)
    rows = query.order_by(H5WorkflowTemplate.id.asc()).all()
    # Prefer a template already bound to this slot; only fall back to a legacy
    # unbound mirror when migrating an older account.
    rows.sort(key=lambda item: (0 if _clean_text(item.installation_id, 128) == installation_id and installation_id else 1, item.id))
    for item in rows:
        if _clean_text((item.meta or {}).get("system_template_key"), 128) == system_template_key:
            return item
    return None


def _accessible_template(db: Session, template_id: int, owner_user_id: int) -> H5WorkflowTemplate:
    row = db.query(H5WorkflowTemplate).filter(H5WorkflowTemplate.id == template_id, H5WorkflowTemplate.status == "active").first()
    if not row:
        raise HTTPException(status_code=404, detail="模板不存在")
    if row.owner_user_id == owner_user_id:
        return row
    grant = (
        db.query(H5WorkflowTemplateGrant)
        .filter(
            H5WorkflowTemplateGrant.template_id == row.id,
            H5WorkflowTemplateGrant.target_user_id == owner_user_id,
            H5WorkflowTemplateGrant.status == "active",
        )
        .first()
    )
    if not grant:
        raise HTTPException(status_code=403, detail="无权使用该模板")
    return row


def _pause_task_ids(db: Session, task_ids: list[int], now: datetime) -> None:
    if not task_ids:
        return
    rows = db.query(ScheduledTask).filter(ScheduledTask.id.in_(task_ids)).all()
    for task in rows:
        if task.status == "active":
            task.status = "paused"
            task.next_run_at = None
            task.updated_at = now
        _cancel_unfinished_runs_for_task(
            db,
            task,
            now,
            message="\u5de5\u4f5c\u6d41\u5df2\u505c\u7528\uff0c\u6267\u884c\u5df2\u505c\u6b62",
            event_reason="workflow_stopped",
        )


def _stop_active_for_device(db: Session, user_id: int, installation_id: str, now: datetime) -> list[int]:
    stopped_ids: list[int] = []
    rows = (
        db.query(H5WorkflowActivation)
        .filter(
            H5WorkflowActivation.user_id == user_id,
            H5WorkflowActivation.installation_id == installation_id,
            H5WorkflowActivation.status == "active",
        )
        .all()
    )
    for row in rows:
        row.status = "stopped"
        row.stopped_at = now
        row.updated_at = now
        stopped_ids.append(row.id)
        _pause_task_ids(db, [int(x) for x in (row.scheduled_task_ids or []) if str(x).isdigit()], now)
    return stopped_ids


def _stop_active_for_template(db: Session, template_id: int, template_owner_user_id: int, now: datetime) -> list[int]:
    stopped_ids: list[int] = []
    rows = (
        db.query(H5WorkflowActivation)
        .filter(
            H5WorkflowActivation.template_id == template_id,
            H5WorkflowActivation.template_owner_user_id == template_owner_user_id,
            H5WorkflowActivation.status == "active",
        )
        .all()
    )
    for row in rows:
        row.status = "stopped"
        row.stopped_at = now
        row.updated_at = now
        stopped_ids.append(row.id)
        _pause_task_ids(db, [int(x) for x in (row.scheduled_task_ids or []) if str(x).isdigit()], now)
    return stopped_ids


def _workflow_node_should_start_now(
    node: dict[str, Any],
    *,
    task_kind: str,
    now_utc: datetime,
    timezone_offset_minutes: int,
) -> bool:
    """Start a long-running takeover when it is enabled inside today's window."""
    if task_kind != "client_workflow":
        return False
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    if str(payload.get("action") or "").strip() != "native_wechat_poll":
        return False
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    start_text = str(node.get("time") or params.get("sales_schedule_start") or "").strip()
    end_text = str(
        node.get("end_time")
        or params.get("workflow_node_end_time")
        or params.get("sales_schedule_end")
        or "23:59"
    ).strip()
    if not re.match(r"^\d{2}:\d{2}$", start_text) or not re.match(r"^\d{2}:\d{2}$", end_text):
        return False
    start_hour, start_minute = (int(item) for item in start_text.split(":", 1))
    end_hour, end_minute = (int(item) for item in end_text.split(":", 1))
    if start_hour > 23 or end_hour > 23 or start_minute > 59 or end_minute > 59:
        return False
    local_now = now_utc + timedelta(minutes=int(timezone_offset_minutes or 0))
    current = local_now.hour * 60 + local_now.minute
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute
    if end < start:
        return current >= start or current <= end
    return start <= current <= end


def _activate_nodes_for_device(
    *,
    db: Session,
    current_user: User,
    owner: User,
    installation_id: str,
    template_id: int,
    template_owner_user_id: int,
    template_name: str,
    nodes: list[dict[str, Any]],
    timezone_offset_minutes: Optional[int],
    snapshot_extra: Optional[dict[str, Any]] = None,
):
    nodes = _prepare_sales_workflow_nodes(
        db=db,
        owner=owner,
        installation_id=installation_id,
        template_name=template_name,
        nodes=nodes,
        snapshot_extra=snapshot_extra,
    )
    selected_plan_day = None
    if isinstance(snapshot_extra, dict) and "plan_day" in snapshot_extra:
        selected_plan_day = _safe_int(snapshot_extra.get("plan_day"), 1, min_value=1, max_value=30)
    nodes = _apply_workflow_runtime_options(
        nodes,
        local_bestseller_plan_day=selected_plan_day,
    )
    nodes = _prepare_publish_action_nodes(
        db=db,
        owner=owner,
        installation_id=installation_id,
        nodes=nodes,
    )
    now = datetime.utcnow()
    stopped_ids = _stop_active_for_device(db, owner.id, installation_id, now)
    db.commit()
    created_task_ids: list[int] = []
    try:
        for node, parent_node in _workflow_nodes_with_actions(nodes):
            if _is_workflow_placeholder(node):
                continue
            plan = node.get("plan") or {}
            task_kind = str(plan.get("task_kind") or "").strip().lower()
            payload = dict(plan.get("payload") or {})
            if task_kind == "douyin_leads":
                # Each H5 workflow trigger is one finite Online action. The
                # workflow schedule may trigger it again later, but it must
                # never start a persistent Douyin monitor.
                payload["h5_task_source"] = "workflow"
                payload["h5_one_shot"] = True
                payload["douyin_execution_mode"] = "one_shot"
            payload["h5_context"] = {
                **(payload.get("h5_context") if isinstance(payload.get("h5_context"), dict) else {}),
                "workflow_template_id": template_id,
                "workflow_template_name": template_name,
                "workflow_template_key": (snapshot_extra or {}).get("template_key") or "",
                "workflow_node_id": node.get("id"),
                "workflow_node_time": node.get("time"),
                "workflow_node_end_time": node.get("end_time") or "",
                "workflow_node_time_range": node.get("time_range") or (
                    f"{node.get('time')}-{node.get('end_time')}" if node.get("end_time") else node.get("time")
                ),
                "ability_key": node.get("ability_key"),
                "ability_label": node.get("ability_label"),
                "department_id": node.get("department_id"),
                "department_name": node.get("department_name"),
            }
            if parent_node:
                payload["h5_context"].update(
                    {
                        "workflow_parent_node_id": parent_node.get("id"),
                        "workflow_parent_node_time": parent_node.get("time"),
                        "workflow_parent_node_end_time": parent_node.get("end_time") or "",
                        "workflow_parent_node_time_range": parent_node.get("time_range") or (
                            f"{parent_node.get('time')}-{parent_node.get('end_time')}"
                            if parent_node.get("end_time")
                            else parent_node.get("time")
                        ),
                        "workflow_parent_ability_key": parent_node.get("ability_key"),
                        "workflow_parent_ability_label": parent_node.get("ability_label"),
                        "workflow_action_type": node.get("action_type") or node.get("type"),
                        "workflow_action_platform": node.get("platform"),
                    }
                )
            scheduled = _create_task_row(
                db,
                ScheduledTaskCreate(
                    title=str(plan.get("title") or node.get("ability_label") or template_name),
                    task_kind=task_kind,
                    content=str(plan.get("content") or f"H5 工作流：{node.get('ability_label') or template_name}"),
                    payload=payload,
                    schedule_type="daily_times",
                    daily_times=[node["time"]],
                    timezone_offset_minutes=timezone_offset_minutes if timezone_offset_minutes is not None else 480,
                    installation_ids=[] if task_kind in _SERVER_SIDE_TASK_KINDS else [installation_id],
                ),
                target_user_id=owner.id,
                created_by_user_id=current_user.id,
                created_by_role="workflow",
            )
            if _workflow_node_should_start_now(
                node,
                task_kind=task_kind,
                now_utc=now,
                timezone_offset_minutes=timezone_offset_minutes if timezone_offset_minutes is not None else 480,
            ):
                scheduled.next_run_at = now
                _enqueue_task(db, scheduled, now, scheduled_at=scheduled.next_run_at)
            created_task_ids.append(int(scheduled.id))
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        for tid in created_task_ids:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == tid).first()
            if task:
                _delete_task_row(db, task)
        db.commit()
        raise
    snapshot = {"name": template_name, "nodes": nodes}
    if snapshot_extra:
        snapshot.update(snapshot_extra)
    activation = H5WorkflowActivation(
        user_id=owner.id,
        installation_id=installation_id,
        template_id=template_id,
        template_owner_user_id=template_owner_user_id,
        status="active",
        scheduled_task_ids=created_task_ids,
        template_snapshot=snapshot,
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(activation)
    db.commit()
    db.refresh(activation)
    tasks = db.query(ScheduledTask).filter(ScheduledTask.id.in_(created_task_ids)).all() if created_task_ids else []
    return activation, stopped_ids, tasks


@router.get("/api/h5-workflows/templates", summary="H5 工作流模板列表")
def list_workflow_templates(
    installation_id: str = Query("", max_length=128),
    x_installation_id: str = Header("", alias="X-Installation-Id", max_length=128),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    iid = _clean_text(installation_id or x_installation_id, 128)
    template_scope = or_(H5WorkflowTemplate.installation_id == "", H5WorkflowTemplate.installation_id == iid) if iid else H5WorkflowTemplate.installation_id == ""
    own_rows = (
        db.query(H5WorkflowTemplate)
        .filter(H5WorkflowTemplate.owner_user_id == owner.id, H5WorkflowTemplate.status == "active", template_scope)
        .order_by(H5WorkflowTemplate.updated_at.desc())
        .all()
    )
    grants = (
        db.query(H5WorkflowTemplateGrant)
        .filter(H5WorkflowTemplateGrant.target_user_id == owner.id, H5WorkflowTemplateGrant.status == "active")
        .all()
    )
    granted_ids = [g.template_id for g in grants]
    granted_rows = []
    if granted_ids:
        granted_rows = (
            db.query(H5WorkflowTemplate)
            .filter(H5WorkflowTemplate.id.in_(granted_ids), H5WorkflowTemplate.status == "active", template_scope)
            .order_by(H5WorkflowTemplate.updated_at.desc())
            .all()
        )
    grant_map: dict[int, list[int]] = {}
    if own_rows:
        own_ids = [r.id for r in own_rows]
        for item in (
            db.query(H5WorkflowTemplateGrant)
            .filter(H5WorkflowTemplateGrant.template_id.in_(own_ids), H5WorkflowTemplateGrant.status == "active")
            .all()
        ):
            grant_map.setdefault(item.template_id, []).append(item.target_user_id)
    owners = {
        row.id: db.query(User).filter(User.id == row.owner_user_id).first()
        for row in granted_rows
    }
    return {
        "ok": True,
        "templates": [
            *[_template_payload(row, source="own", grants=grant_map.get(row.id, [])) for row in own_rows],
            *[_template_payload(row, owner=owners.get(row.id), source="granted") for row in granted_rows if row.owner_user_id != owner.id],
        ],
        "can_grant": bool(getattr(current_user, "is_agent", False)),
    }


@router.post("/api/h5-workflows/templates", summary="保存 H5 工作流模板")
def create_workflow_template(
    body: WorkflowTemplateIn,
    x_installation_id: str = Header("", alias="X-Installation-Id", max_length=128),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    name = (body.name or "").strip()[:160]
    if not name:
        raise HTTPException(status_code=400, detail="请填写模板名称")
    meta = dict(body.meta or {})
    installation_id = _clean_text(body.installation_id or x_installation_id, 128)
    system_template_key = _clean_text(meta.get("system_template_key"), 128)
    if system_template_key and system_template_key not in _ENABLED_SYSTEM_WORKFLOW_KEYS:
        raise HTTPException(status_code=400, detail="该系统员工模板暂未开放")
    if system_template_key:
        existing = _system_workflow_template(db, owner.id, system_template_key, installation_id=installation_id)
        if existing:
            existing.name = name
            existing.nodes = _clean_nodes(body.nodes)
            existing.meta = {**(existing.meta or {}), **meta}
            existing.installation_id = installation_id
            existing.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(existing)
            return {"ok": True, "created": False, "template": _template_payload(existing, source="own")}
    row = H5WorkflowTemplate(
        owner_user_id=owner.id,
        installation_id=installation_id,
        name=name,
        nodes=_clean_nodes(body.nodes),
        status="active",
        meta=meta,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "created": True, "template": _template_payload(row, source="own")}


@router.patch("/api/h5-workflows/templates/{template_id}", summary="更新 H5 工作流模板")
def update_workflow_template(
    template_id: int,
    body: WorkflowTemplateIn,
    x_installation_id: str = Header("", alias="X-Installation-Id", max_length=128),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    row = _own_template(db, template_id, owner.id)
    name = (body.name or "").strip()[:160]
    if not name:
        raise HTTPException(status_code=400, detail="请填写模板名称")
    meta: Optional[dict[str, Any]] = None
    if body.installation_id is not None or x_installation_id:
        row.installation_id = _clean_text(body.installation_id or x_installation_id, 128)
    if body.meta:
        meta = dict(body.meta)
        system_template_key = _clean_text(meta.get("system_template_key"), 128)
        if system_template_key and system_template_key not in _ENABLED_SYSTEM_WORKFLOW_KEYS:
            raise HTTPException(status_code=400, detail="该系统员工模板暂未开放")
        duplicate_system_template = system_template_key and _system_workflow_template(
            db,
            owner.id,
            system_template_key,
            exclude_template_id=row.id,
            installation_id=_clean_text(row.installation_id, 128),
        )
        if duplicate_system_template:
            meta.pop("system_template_key", None)
            if meta.get("source") == "system_mirror":
                meta.pop("source", None)
    row.name = name
    row.nodes = _clean_nodes(body.nodes)
    if meta is not None:
        row.meta = meta
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"ok": True, "template": _template_payload(row, source="own")}


@router.delete("/api/h5-workflows/templates/{template_id}", summary="删除 H5 工作流模板")
def delete_workflow_template(
    template_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    row = _own_template(db, template_id, owner.id)
    now = datetime.utcnow()
    row.status = "deleted"
    row.updated_at = now
    stopped_ids = _stop_active_for_template(db, row.id, owner.id, now)
    db.commit()
    return {"ok": True, "deleted": True, "stopped_activation_ids": stopped_ids}


@router.get("/api/h5-workflows/agent/sub-users", summary="代理商下级用户列表")
def list_agent_sub_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not getattr(current_user, "is_agent", False):
        return {"ok": True, "sub_users": []}
    rows = (
        db.query(User)
        .filter(User.id.in_(_agent_sub_user_ids(db, int(current_user.id))))
        .order_by(User.created_at.desc())
        .all()
    )
    return {
        "ok": True,
        "sub_users": [
            {
                "id": row.id,
                "email": row.email,
                "is_agent": bool(row.is_agent),
                "agent_level": int(row.agent_level or 0),
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ],
    }


@router.post("/api/h5-workflows/templates/{template_id}/grants", summary="授权 H5 工作流模板给下级")
def grant_workflow_template(
    template_id: int,
    body: WorkflowGrantIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    if not getattr(current_user, "is_agent", False):
        raise HTTPException(status_code=403, detail="只有代理商可以授权模板")
    row = _own_template(db, template_id, owner.id)
    allowed = set(_agent_sub_user_ids(db, int(current_user.id)))
    requested: list[int] = []
    for raw in body.target_user_ids or []:
        try:
            uid = int(raw or 0)
        except Exception:
            uid = 0
        if uid > 0 and uid not in requested:
            requested.append(uid)
    if any(uid not in allowed for uid in requested):
        raise HTTPException(status_code=403, detail="只能授权给自己的下级用户")
    target_ids = requested
    now = datetime.utcnow()
    existing = (
        db.query(H5WorkflowTemplateGrant)
        .filter(H5WorkflowTemplateGrant.template_id == row.id, H5WorkflowTemplateGrant.owner_user_id == owner.id)
        .all()
    )
    target_set = set(target_ids)
    for grant in existing:
        grant.status = "active" if grant.target_user_id in target_set else "revoked"
        grant.updated_at = now
        target_set.discard(grant.target_user_id)
    for uid in target_set:
        db.add(
            H5WorkflowTemplateGrant(
                template_id=row.id,
                owner_user_id=owner.id,
                target_user_id=uid,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
    db.commit()
    return {"ok": True, "template_id": row.id, "target_user_ids": target_ids}


@router.get("/api/h5-workflows/active", summary="当前设备启用的 H5 工作流")
def get_active_workflow(
    installation_id: str = Query("", max_length=128),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    iid = (installation_id or "").strip()
    if not iid:
        return {"ok": True, "activation": None}
    row = (
        db.query(H5WorkflowActivation)
        .filter(
            H5WorkflowActivation.user_id == owner.id,
            H5WorkflowActivation.installation_id == iid,
            H5WorkflowActivation.status == "active",
        )
        .order_by(H5WorkflowActivation.started_at.desc())
        .first()
    )
    template = db.query(H5WorkflowTemplate).filter(H5WorkflowTemplate.id == row.template_id).first() if row else None
    return {"ok": True, "activation": _activation_payload(row, template) if row else None}


@router.post("/api/h5-workflows/activate", summary="启用 H5 工作流模板")
def activate_workflow_template(
    body: WorkflowActivateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    iid = (body.installation_id or "").strip()
    if not iid:
        raise HTTPException(status_code=400, detail="请选择设备")
    template = _accessible_template(db, body.template_id, owner.id)
    bound_iid = _clean_text(template.installation_id, 128)
    if bound_iid and bound_iid != iid:
        raise HTTPException(status_code=409, detail="该员工已绑定其他设备槽位，请从当前设备的员工列表进入")
    if not bound_iid and template.owner_user_id == owner.id:
        template.installation_id = iid
        template.updated_at = datetime.utcnow()
        db.commit()
    nodes = _clean_nodes(template.nodes or [])
    template_meta = template.meta if isinstance(template.meta, dict) else {}
    system_template_key = _clean_text(template_meta.get("system_template_key"), 128)
    snapshot_extra = None
    if system_template_key in _ENABLED_SYSTEM_WORKFLOW_KEYS:
        snapshot_extra = {"template_key": system_template_key, "source": "own"}
    if body.plan_day is not None:
        snapshot_extra = {**(snapshot_extra or {"source": "own"}), "plan_day": body.plan_day}
    activation, stopped_ids, tasks = _activate_nodes_for_device(
        db=db,
        current_user=current_user,
        owner=owner,
        installation_id=iid,
        template_id=template.id,
        template_owner_user_id=template.owner_user_id,
        template_name=template.name,
        nodes=nodes,
        timezone_offset_minutes=body.timezone_offset_minutes,
        snapshot_extra=snapshot_extra,
    )
    return {
        "ok": True,
        "activation": _activation_payload(activation, template),
        "stopped_activation_ids": stopped_ids,
        "tasks": [_serialize_task(task) for task in tasks],
    }


@router.post("/api/h5-workflows/activate-inline", summary="启用 H5 工作流快照")
def activate_inline_workflow_template(
    body: WorkflowActivateInlineIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    iid = (body.installation_id or "").strip()
    if not iid:
        raise HTTPException(status_code=400, detail="请选择设备")
    template_key = (body.template_key or "").strip()[:128]
    if not template_key:
        raise HTTPException(status_code=400, detail="缺少系统模板标识")
    if template_key not in _ENABLED_SYSTEM_WORKFLOW_KEYS:
        raise HTTPException(status_code=400, detail="该系统员工暂未开放")
    name = (body.name or "系统员工模板").strip()[:160] or "系统员工模板"
    nodes = _clean_nodes(body.nodes or [])
    activation, stopped_ids, tasks = _activate_nodes_for_device(
        db=db,
        current_user=current_user,
        owner=owner,
        installation_id=iid,
        template_id=0,
        template_owner_user_id=owner.id,
        template_name=name,
        nodes=nodes,
        timezone_offset_minutes=body.timezone_offset_minutes,
        snapshot_extra={
            "template_key": template_key,
            "source": "system",
            **({"plan_day": body.plan_day} if body.plan_day is not None else {}),
        },
    )
    return {
        "ok": True,
        "activation": _activation_payload(activation),
        "stopped_activation_ids": stopped_ids,
        "tasks": [_serialize_task(task) for task in tasks],
    }


@router.post("/api/h5-workflows/activations/{activation_id}/stop", summary="停用 H5 工作流")
def stop_workflow_activation(
    activation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    row = (
        db.query(H5WorkflowActivation)
        .filter(
            H5WorkflowActivation.id == activation_id,
            H5WorkflowActivation.user_id == owner.id,
            H5WorkflowActivation.status == "active",
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="工作流未启用")
    now = datetime.utcnow()
    row.status = "stopped"
    row.stopped_at = now
    row.updated_at = now
    _pause_task_ids(db, [int(x) for x in (row.scheduled_task_ids or []) if str(x).isdigit()], now)
    db.commit()
    return {"ok": True, "activation": _activation_payload(row)}
