from __future__ import annotations

import copy
import re
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

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
    _cancel_pending_runs_for_task,
    _create_task_row,
    _delete_task_row,
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
    "native_wechat_group_invite",
    "native_wechat_moments_engage",
}
_ENABLED_SYSTEM_WORKFLOW_KEYS = {"system_sales"}


class WorkflowTemplateIn(BaseModel):
    name: str = Field("", max_length=160)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class WorkflowGrantIn(BaseModel):
    target_user_ids: list[int] = Field(default_factory=list)


class WorkflowActivateIn(BaseModel):
    template_id: int
    installation_id: str = Field("", max_length=128)
    timezone_offset_minutes: Optional[int] = None


class WorkflowActivateInlineIn(BaseModel):
    template_key: str = Field("", max_length=128)
    name: str = Field("", max_length=160)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    installation_id: str = Field("", max_length=128)
    timezone_offset_minutes: Optional[int] = None


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
        if not isinstance(raw, dict):
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
                params.setdefault("source_mode", "douyin_private_message_wechat_id")
                params.setdefault("trigger", "clear_wechat_id")
                params.setdefault("skip_without_clear_wechat_id", True)
                params.setdefault("targets", [])
            if action == "native_wechat_poll" and (
                action_type == "native_wechat_group_invite"
                or params.get("followup_action") == "group_invite"
                or "自动拉群" in _clean_text(raw.get("note") or raw.get("ability_label"), 200)
            ):
                params["followup_action"] = "group_invite"
                params["group_invite_enabled"] = True
                params.setdefault("group_invite_rule_status", "pending_rules")
                params.setdefault("trigger", "qualified_intent")
                params.setdefault("group_invite_targets_source", "qualified_intent")
                params.setdefault("group_invite_members", [])
                params.setdefault("group_invite_manager_contacts", [])
            label = str(raw.get("ability_label") or raw.get("label") or plan.get("title") or "系统销售微信动作").strip()[:160]
            action_id = str(raw.get("id") or f"{parent_id}_action_{len(actions) + 1}")[:64]
            actions.append(
                {
                    "id": action_id,
                    "time": _clean_time(raw.get("time")),
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
    return actions


def _is_workflow_placeholder(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    return bool(
        node.get("comingSoon")
        or node.get("coming_soon")
        or node.get("workflow_placeholder")
        or node.get("placeholder")
        or payload.get("skip_execution")
        or payload.get("action") == "workflow_coming_soon"
    )


def _clean_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for raw in nodes or []:
        if not isinstance(raw, dict):
            continue
        plan = raw.get("plan") if isinstance(raw.get("plan"), dict) else raw
        task_kind = str(plan.get("task_kind") or plan.get("taskKind") or "").strip().lower()
        payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
        title = str(plan.get("title") or raw.get("label") or raw.get("ability_label") or "工作流任务").strip()[:160]
        content = str(plan.get("content") or f"H5 工作流：{title}").strip()[:12000]
        if _is_workflow_placeholder(raw) or payload.get("action") == "workflow_coming_soon":
            placeholder_payload = dict(payload)
            placeholder_payload["action"] = "workflow_coming_soon"
            placeholder_payload["skip_execution"] = True
            cleaned.append(
                {
                    "id": str(raw.get("id") or f"node_{len(cleaned) + 1}")[:64],
                    "time": _clean_time(raw.get("time")),
                    "ability_key": str(raw.get("ability_key") or raw.get("abilityKey") or "").strip()[:128],
                    "ability_label": str(raw.get("ability_label") or raw.get("abilityLabel") or raw.get("label") or title).strip()[:160],
                    "department_id": str(raw.get("department_id") or raw.get("departmentId") or "").strip()[:64],
                    "department_name": str(raw.get("department_name") or raw.get("departmentName") or "").strip()[:80],
                    "note": str(raw.get("note") or "").strip()[:2000],
                    "sales_preset": bool(raw.get("sales_preset") or raw.get("salesPreset")),
                    "comingSoon": True,
                    "workflow_placeholder": True,
                    "param_configured": bool(raw.get("param_configured")),
                    "plan": {
                        "title": title,
                        "task_kind": "workflow_placeholder",
                        "content": content,
                        "payload": placeholder_payload,
                    },
                }
            )
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
    return cleaned[:96]


def _clean_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text)[:limit]


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


def _mounted_default(db: Session, user_id: int, scope: str) -> Optional[H5MountedAccountDefault]:
    return (
        db.query(H5MountedAccountDefault)
        .filter(H5MountedAccountDefault.user_id == user_id, H5MountedAccountDefault.scope == scope)
        .first()
    )


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


def _prepare_publish_action_nodes(
    *,
    db: Session,
    owner: User,
    installation_id: str,
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prepared = copy.deepcopy(nodes)
    publish_default = _mounted_default(db, owner.id, "publish")
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
            if not publish_default:
                missing.append(f"发布{_workflow_platform_label(platform)}：请先在个人中心设置默认发布账号")
                continue
            default_platform = _clean_text(publish_default.platform, 64).lower()
            if default_platform != platform:
                missing.append(f"发布{_workflow_platform_label(platform)}：默认发布账号不是{_workflow_platform_label(platform)}账号")
                continue
            default_iid = _clean_text(publish_default.installation_id, 128)
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
    if "@精准" in text:
        return "mention_comment"
    if "关注" in text and "评论" in text:
        return "follow_comment"
    if "主动私信" in text or "私信10" in text:
        return "direct_message"
    if "私信接管" in text or "私信引流" in text:
        return "stranger_message"
    return "search_collect"


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
    group_invite = base_params.get("followup_action") == "group_invite" or "自动拉群" in note_text
    if group_invite:
        base_params["followup_action"] = "group_invite"
        base_params["group_invite_enabled"] = True
        base_params.setdefault("group_invite_rule_status", "pending_rules")
        base_params.setdefault("trigger", "qualified_intent")
    if action_key == "native_wechat_poll":
        title = "个微自动拉群" if group_invite else "个微私信接管"
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
        base_params.setdefault("targets", [])
        base_params.setdefault("moment_action", "like_comment")
        base_params.setdefault("max_scrolls", 6)
        return {
            "title": "朋友圈点赞评论",
            "task_kind": "client_workflow",
            "content": "H5 工作流：朋友圈点赞评论",
            "payload": {"action": action_key, "params": base_params},
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
        native_plan = _native_wechat_plan(native_key, note, params) if native_key else {}
        if native_plan:
            node["ability_key"] = native_key
            node["department_id"] = "sales"
            node["department_name"] = "销售部"
            node["plan"] = native_plan
    for child in _workflow_child_nodes(node):
        _normalize_sales_native_wechat_node(child)


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
    personal = _personal_default_template(db, owner.id)
    current_template = _current_personal_schedule_template(db, owner.id, personal)
    reference_template = current_template or personal
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
    keyword_texts = [_clean_text(row.display_name or row.keyword, 120) for row in keywords if _clean_text(row.display_name or row.keyword, 120)]
    city = _first_req_text(requirements, "current_city")
    province = _first_req_text(requirements, "current_province")
    regions = [x for x in [city, province] if x] or ["全国"]
    douyin_default = _mounted_default(db, owner.id, "douyin")
    douyin_iid = _clean_text(douyin_default.installation_id if douyin_default else "", 128)
    hifly_avatar = _latest_hifly_avatar(db, owner.id)
    hifly_voice = _latest_hifly_voice(db, owner.id)

    has_hifly = False
    has_douyin = False
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
            has_douyin = True
            payload = dict(payload)
            params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            params = dict(params)
            sales_action = _clean_text(params.get("sales_action"), 64) or _sales_action_from_note(node.get("note") or node.get("ability_label"))
            params["sales_action"] = sales_action
            params["sales_node_label"] = _clean_text(node.get("ability_label") or node.get("note"), 160)
            if sales_action and sales_action != "search_collect":
                params["max_results"] = _safe_int(params.get("max_results") or params.get("max_users") or 10, 10)
                params["max_users"] = _safe_int(params.get("max_users") or params.get("max_results") or 10, 10)
            if keyword_texts:
                params["keywords"] = keyword_texts
                if not _clean_text(params.get("keyword"), 200) or _clean_text(params.get("keyword"), 200) == params["sales_node_label"]:
                    params["keyword"] = keyword_texts[0]
                params.setdefault("query", params.get("keyword"))
                params.setdefault("search_keyword", params.get("keyword"))
            if not params.get("regions") or params.get("regions") == ["全国"]:
                params["regions"] = regions
            if douyin_default:
                params["account_key"] = _clean_text(douyin_default.account_key, 255)
                params["account_id"] = _clean_text(douyin_default.account_id, 128)
                params["account_label"] = _clean_text(douyin_default.account_label, 255)
                params["douyin_installation_id"] = douyin_iid
            payload["params"] = params
            payload["action"] = _clean_text(payload.get("action"), 64) or "search_collect"
            plan["payload"] = payload

        if task_kind == "client_workflow" and action.startswith("local_bestseller"):
            has_local_bestseller = True

        if task_kind == "client_workflow" and action in _NATIVE_WECHAT_WORKFLOW_ACTIONS:
            has_wechat = True

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
            if not _clean_text(inner.get("avatar"), 128) and hifly_avatar:
                inner["avatar"] = hifly_avatar
            if not _clean_text(inner.get("voice"), 128) and hifly_voice:
                inner["voice"] = hifly_voice
            payload["payload"] = inner
            plan["payload"] = payload

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
    if has_douyin:
        if not douyin_default:
            missing.append("平台账号：请在个人中心设置默认抖音获客账号")
        elif not douyin_iid:
            missing.append("平台账号：默认抖音账号缺少设备信息")
        elif douyin_iid != _clean_text(installation_id, 128):
            missing.append("平台账号：默认抖音账号不在当前启用设备上")
        elif not _device_is_online(db, owner.id, douyin_iid):
            missing.append("平台账号：默认抖音账号所在设备不在线")
    if has_wechat and not _device_is_online(db, owner.id, _clean_text(installation_id, 128)):
        missing.append("平台账号：当前启用设备不在线，无法执行个人微信节点")
    if has_hifly:
        if not hifly_avatar:
            missing.append("素材库：请先创建可用的数字人形象分身")
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
        "owner_name": owner.email if owner else "",
        "name": row.name,
        "nodes": row.nodes or [],
        "status": row.status,
        "source": source,
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
        "template_nodes": template_nodes or [],
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
        _cancel_pending_runs_for_task(db, task, now)


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
            payload["h5_context"] = {
                **(payload.get("h5_context") if isinstance(payload.get("h5_context"), dict) else {}),
                "workflow_template_id": template_id,
                "workflow_template_name": template_name,
                "workflow_template_key": (snapshot_extra or {}).get("template_key") or "",
                "workflow_node_id": node.get("id"),
                "workflow_node_time": node.get("time"),
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    own_rows = (
        db.query(H5WorkflowTemplate)
        .filter(H5WorkflowTemplate.owner_user_id == owner.id, H5WorkflowTemplate.status == "active")
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
            .filter(H5WorkflowTemplate.id.in_(granted_ids), H5WorkflowTemplate.status == "active")
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    name = (body.name or "").strip()[:160]
    if not name:
        raise HTTPException(status_code=400, detail="请填写模板名称")
    row = H5WorkflowTemplate(
        owner_user_id=owner.id,
        name=name,
        nodes=_clean_nodes(body.nodes),
        status="active",
        meta=body.meta or {},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "template": _template_payload(row, source="own")}


@router.patch("/api/h5-workflows/templates/{template_id}", summary="更新 H5 工作流模板")
def update_workflow_template(
    template_id: int,
    body: WorkflowTemplateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    row = _own_template(db, template_id, owner.id)
    name = (body.name or "").strip()[:160]
    if not name:
        raise HTTPException(status_code=400, detail="请填写模板名称")
    row.name = name
    row.nodes = _clean_nodes(body.nodes)
    row.meta = body.meta or {}
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
    row.status = "deleted"
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "deleted": True}


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
    nodes = _clean_nodes(template.nodes or [])
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
        snapshot_extra={"template_key": template_key, "source": "system"},
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
