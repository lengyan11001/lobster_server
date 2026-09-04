from __future__ import annotations

import copy
import os
import re
import threading
import time
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
    _h5_dh_context_params,
    _delete_task_row,
    _local_bestseller_profile_from_persona,
    _serialize_task,
)
from .ip_content_studio import _personal_default_resource_overrides
from ..services.user_feature_flags import user_feature_flags

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
_SYSTEM_WORKFLOW_OWNER_ID = 0
_SYSTEM_WORKFLOW_CATALOG_SOURCE = "system_catalog"
_ENABLED_SYSTEM_WORKFLOW_KEYS = {
    "system_sales",
    "system_short_video_wechat",
    "system_douyin_leads",
}
_SALES_DH_PROVIDER_V2 = "shanjian_v2"
_SALES_DH_PROVIDER_LEGACY = "hifly_legacy"

_WORKFLOW_TEMPLATE_CACHE_TTL_SECONDS = 3.0
_WORKFLOW_TEMPLATE_CACHE_LOCK = threading.Lock()
_WORKFLOW_TEMPLATE_CACHE: dict[tuple[int, str], tuple[float, list[dict[str, Any]]]] = {}
_WORKFLOW_TEMPLATE_CACHE_KEY_LOCKS: dict[tuple[int, str], threading.Lock] = {}

# Keep server-side workflow activation aligned with the node picker in H5 and
# Online. The picker uses package visibility as the capability permission;
# activation must not be able to bypass that rule through a direct request.
_WORKFLOW_NODE_CAPABILITY_IDS = {
    "image_composer_studio": "goal.image.pipeline",
    "hifly.video.create_by_tts": "hifly.video.create_by_tts",
    "comfly.seedance.tvc.pipeline": "comfly.seedance.tvc.pipeline",
    "comfly.daihuo.pipeline": "comfly.daihuo.pipeline",
    "ip_content_daily": "ip_content_daily",
    "wewrite.article.pipeline": "wewrite.article.pipeline",
}
_WORKFLOW_NODE_PACKAGE_IDS = {
    "hifly.video.create_by_tts": "hifly_digital_human_skill",
    "comfly.seedance.tvc.pipeline": "comfly_seedance_tvc_skill",
    "comfly.daihuo.pipeline": "comfly_veo_skill",
    "image_composer_studio": "goal_video_pipeline_skill",
    "ip_content_daily": "ip_content_daily_skill",
    "wewrite.article.pipeline": "wewrite_official_account_skill",
    "linkedin_leads": "linkedin_leads",
    "linkedin_mining": "linkedin_leads",
    "reddit_leads": "reddit_leads",
    "x_leads": "x_leads",
    "tiktok_leads": "tiktok_leads",
}
_WORKFLOW_ACTION_CAPABILITY_IDS = {
    "image_studio_generate": "goal.image.pipeline",
}
_WORKFLOW_TASK_CAPABILITY_IDS = {
    "ip_content_daily": "ip_content_daily",
}
_WORKFLOW_PACKAGE_LABELS = {
    "hifly_digital_human_skill": "数字人口播视频",
    "comfly_seedance_tvc_skill": "创意分镜头视频",
    "comfly_veo_skill": "爆款TVC",
    "goal_video_pipeline_skill": "AI设计图",
    "ip_content_daily_skill": "IP日更文案",
    "wewrite_official_account_skill": "公众号文章",
    "linkedin_leads": "LinkedIn线索挖掘",
    "reddit_leads": "Reddit线索采集",
    "x_leads": "X线索采集",
    "tiktok_leads": "TikTok线索采集",
}


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


def _workflow_visible_package_ids(db: Session, user_id: int) -> set[str]:
    """Return the same package visibility used by the H5/Online node picker."""
    from .skills import _skill_store_admin, _user_visible_package_ids

    user = db.query(User).filter(User.id == int(user_id or 0)).first()
    if not user:
        return set()
    if _skill_store_admin(user):
        from .skills import _load_registry

        return set((_load_registry().get("packages") or {}).keys())
    return set(_user_visible_package_ids(db, user, is_overseas_client=False))


def _workflow_capability_package_map() -> dict[str, str]:
    """Map executable workflow capability ids to their registered package."""
    from .skills import _load_registry

    packages = _load_registry().get("packages") or {}
    result: dict[str, str] = {}
    for package_id, package in packages.items():
        if not isinstance(package, dict):
            continue
        for capability_id in (package.get("capabilities") or {}).keys():
            normalized = str(capability_id or "").strip()
            if normalized:
                result[normalized] = str(package_id or "").strip()
    return result


def _workflow_node_access_requirements(node: dict[str, Any], capability_packages: dict[str, str]) -> tuple[set[str], set[str]]:
    """Return feature gates and package ids required by one workflow node."""
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    nested_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    key = _clean_text(node.get("ability_key") or node.get("abilityKey") or node.get("key"), 128).lower()
    action = _clean_text(payload.get("action"), 128).lower()
    task_kind = _clean_text(plan.get("task_kind") or plan.get("taskKind"), 64).lower()
    capability_id = _clean_text(
        payload.get("capability_id")
        or nested_payload.get("capability_id")
        or params.get("capability_id")
        or _WORKFLOW_ACTION_CAPABILITY_IDS.get(action)
        or _WORKFLOW_TASK_CAPABILITY_IDS.get(task_kind)
        or _WORKFLOW_NODE_CAPABILITY_IDS.get(key),
        128,
    )
    if not capability_id and key in capability_packages:
        capability_id = key

    required_features: set[str] = set()
    required_packages: set[str] = set()
    effective_key = action or key
    if effective_key in {"native_wechat_poll", "native_wechat_add_friend", "native_wechat_moments_engage", "native_wechat_group_invite"}:
        required_features.add("private_domain_entry")
    if effective_key == "douyin_leads" or key == "douyin_leads" or task_kind == "douyin_leads":
        required_features.add("douyin_leads_access")
    if effective_key in {"linkedin_leads", "linkedin_mining", "reddit_leads", "x_leads", "tiktok_leads", "global_trade_leads_skill"}:
        required_features.add("overseas_platform_entry")
    if task_kind == "social_leads":
        platform = _clean_text(payload.get("platform"), 32).lower()
        if platform in {"reddit", "x", "tiktok"}:
            required_features.add("overseas_platform_entry")
            required_packages.add({"reddit": "reddit_leads", "x": "x_leads", "tiktok": "tiktok_leads"}[platform])

    if capability_id:
        package_id = capability_packages.get(capability_id)
        if package_id:
            required_packages.add(package_id)
    package_from_key = _WORKFLOW_NODE_PACKAGE_IDS.get(effective_key) or _WORKFLOW_NODE_PACKAGE_IDS.get(key)
    if package_from_key:
        required_packages.add(package_from_key)
    return required_features, required_packages


def _assert_workflow_feature_permissions(db: Session, user_id: int, nodes: list[dict[str, Any]]) -> None:
    """Prevent direct API activation from bypassing the H5/Online node gates."""
    flags = user_feature_flags(db, int(user_id or 0))
    required_features: set[str] = set()
    required_packages: set[str] = set()
    capability_packages = _workflow_capability_package_map()

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        features, packages = _workflow_node_access_requirements(node, capability_packages)
        required_features.update(features)
        required_packages.update(packages)
        children = list(node.get("children") or [])
        children.extend(node.get("actions") or [])
        for child in children:
            visit(child)

    for node in nodes or []:
        visit(node)
    denied_features = sorted(key for key in required_features if not flags.get(key, False))
    if denied_features:
        labels = {
            "douyin_leads_access": "抖音获客",
            "private_domain_entry": "私域销冠",
            "overseas_platform_entry": "海外平台",
        }
        raise HTTPException(status_code=403, detail="当前账号未开通：" + "、".join(labels.get(key, key) for key in denied_features))
    if required_packages:
        visible_packages = _workflow_visible_package_ids(db, int(user_id or 0))
        denied_packages = sorted(package_id for package_id in required_packages if package_id not in visible_packages)
        if denied_packages:
            labels = [_WORKFLOW_PACKAGE_LABELS.get(package_id, package_id) for package_id in denied_packages]
            raise HTTPException(status_code=403, detail="当前账号未开通：" + "、".join(labels))


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
    for raw in _clean_legacy_sales_action_children(nodes or []):
        if not isinstance(raw, dict):
            continue
        plan = raw.get("plan") if isinstance(raw.get("plan"), dict) else raw
        task_kind = str(plan.get("task_kind") or plan.get("taskKind") or "").strip().lower()
        payload = copy.deepcopy(plan.get("payload")) if isinstance(plan.get("payload"), dict) else {}
        _normalize_douyin_private_switch(raw, plan, payload)
        if task_kind == "douyin_leads" and _is_sales_node(raw) and _sales_douyin_node_action(raw) == "search_collect":
            collection_params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            collection_params = dict(collection_params)
            collection_params.update(_sales_douyin_collection_reply_params(collection_params))
            collection_params["followup_actions"] = []
            collection_params.pop("touch_actions", None)
            collection_params["customer_scope"] = "current_collection_batch"
            payload["params"] = collection_params
        elif task_kind == "douyin_leads" and _is_sales_node(raw) and _sales_douyin_node_action(raw) == "precise_touch":
            touch_params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            touch_params = dict(touch_params)
            has_explicit_actions = "touch_actions" in touch_params or "followup_actions" in touch_params
            raw_actions = touch_params.get("touch_actions") if "touch_actions" in touch_params else touch_params.get("followup_actions")
            touch_params["touch_actions"] = (
                _sales_douyin_followup_actions(raw_actions)
                if has_explicit_actions
                else list(_SALES_DOUYIN_FOLLOWUP_ACTIONS)
            )
            touch_params.pop("followup_actions", None)
            for key in ("reply_precise_comments", "reply_comment_mode", "reply_comment_text", "reply_comment_prompt", "reply_comment_seed_text"):
                touch_params.pop(key, None)
            touch_params["customer_scope"] = "precise_pool"
            if touch_params.get("max_users") not in (None, "", []):
                try:
                    touch_params["max_users"] = max(1, min(200, int(touch_params["max_users"])))
                except (TypeError, ValueError):
                    touch_params["max_users"] = 20
            else:
                touch_params["max_users"] = 20
            payload["params"] = touch_params
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


def _is_legacy_group_invite_child(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    action_type = _clean_text(node.get("action_type") or node.get("type"), 64).lower()
    action = _clean_text(payload.get("action") or node.get("ability_key"), 128).lower()
    label = _clean_text(node.get("ability_label") or node.get("label") or node.get("note"), 200)
    return (
        action_type == "native_wechat_group_invite"
        or action == "native_wechat_group_invite"
        or (action_type == "native_wechat_group_invite" and action == "native_wechat_poll")
        or (action == "native_wechat_poll" and "鑷姩鎷夌兢" in label)
        or _bool_param(params.get("followup_action") == "group_invite", False)
    )


def _is_legacy_add_friend_child(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    return (
        _clean_text(node.get("action_type") or node.get("type"), 64).lower() == "native_wechat_add_friend"
        or _clean_text(payload.get("action") or node.get("ability_key"), 128).lower() == "native_wechat_add_friend"
    )


def _is_douyin_private_sales_node(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    task_kind = _clean_text(plan.get("task_kind") or plan.get("taskKind"), 64).lower()
    action = _clean_text(payload.get("action") or node.get("ability_key"), 128).lower()
    marker = " ".join(
        _clean_text(value, 200)
        for value in (node.get("ability_label"), node.get("label"), node.get("note"), plan.get("title"))
    )
    has_legacy_add_child = any(_is_legacy_add_friend_child(child) for child in _workflow_child_nodes(node))
    return task_kind == "douyin_leads" and (
        action == "stranger_message"
        or "绉佷俊鎺ョ" in marker
        or "鎶栭煶绉佷俊" in marker
        or has_legacy_add_child
    )


def _is_sales_node(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    return bool(
        node.get("sales_preset")
        or node.get("salesPreset")
        or _clean_text(node.get("id"), 80).startswith("sales_")
        or _clean_text(node.get("department_id") or node.get("departmentId"), 64) == "sales"
    )


def _merge_legacy_sales_child_params(parent: dict[str, Any], child: dict[str, Any], *, group_invite: bool) -> None:
    plan = parent.get("plan") if isinstance(parent.get("plan"), dict) else {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    params = dict(payload.get("params") if isinstance(payload.get("params"), dict) else {})
    child_plan = child.get("plan") if isinstance(child.get("plan"), dict) else {}
    child_payload = child_plan.get("payload") if isinstance(child_plan.get("payload"), dict) else {}
    child_params = child_payload.get("params") if isinstance(child_payload.get("params"), dict) else {}
    if group_invite:
        params.update({
            key: value for key, value in child_params.items()
            if key.startswith("group_invite_") or key in {"followup_action", "trigger"}
        })
        params["group_invite_enabled"] = True
        params["followup_action"] = "group_invite"
        params.setdefault("group_invite_rule_status", "pending_rules")
        params.setdefault("trigger", "qualified_intent")
    else:
        current = params.get("wechat_add_friend_enabled")
        params["wechat_add_friend_enabled"] = _bool_param(current, True) if current is not None else True
        params["wechat_add_friend_targets_source"] = "douyin_private_message_phone"
        params.pop("wechat_add_friend_rules", None)
    payload["params"] = params
    plan["payload"] = payload
    parent["plan"] = plan


def _clean_legacy_sales_action_children(nodes: Any) -> list[dict[str, Any]]:
    """Fold pre-property sales children into their parent node before validation."""
    if not isinstance(nodes, list):
        return []
    items = copy.deepcopy([item for item in nodes if isinstance(item, dict)])
    douyin_parents = [item for item in items if _is_douyin_private_sales_node(item)]
    legacy_top_add = [item for item in items if _is_legacy_add_friend_child(item)] if douyin_parents else []
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if douyin_parents and _is_legacy_add_friend_child(item) and item not in douyin_parents:
            continue
        raw_children = item.get("children") if isinstance(item.get("children"), list) else item.get("actions")
        if isinstance(raw_children, list):
            remaining = []
            for child in raw_children:
                if not isinstance(child, dict):
                    continue
                if _is_sales_node(item) and _is_legacy_group_invite_child(child):
                    _merge_legacy_sales_child_params(item, child, group_invite=True)
                    continue
                if _is_legacy_add_friend_child(child) and _is_douyin_private_sales_node(item):
                    _merge_legacy_sales_child_params(item, child, group_invite=False)
                    continue
                remaining.append(child)
            if remaining:
                item["children"] = remaining
                if "actions" in item:
                    item.pop("actions", None)
            else:
                item.pop("children", None)
                item.pop("actions", None)
        if _is_douyin_private_sales_node(item) and legacy_top_add:
            current = (_node_payload(item).get("params") or {}).get("wechat_add_friend_enabled")
            if current is None:
                _merge_legacy_sales_child_params(item, legacy_top_add[0], group_invite=False)
        if _is_douyin_private_sales_node(item):
            item_plan = item.get("plan") if isinstance(item.get("plan"), dict) else {}
            item_payload = item_plan.get("payload") if isinstance(item_plan.get("payload"), dict) else {}
            item_payload["action"] = "stranger_message"
            item_plan["payload"] = item_payload
            item["plan"] = item_plan
        cleaned.append(item)
    return _fold_legacy_sales_douyin_followup_nodes(cleaned)


def _sales_douyin_node_action(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    inferred = _sales_action_from_note(
        node.get("note") or node.get("ability_label") or node.get("label") or plan.get("title")
    )
    explicit = _clean_text(payload.get("action") or params.get("sales_action"), 64).lower()
    valid_actions = {
        "search_collect",
        "precise_touch",
        "self_comment_monitor",
        "account_nurture",
        "reply_comments",
        "follow_comment",
        "mention_comment",
        "direct_message",
        "stranger_message",
    }
    if explicit in valid_actions:
        return inferred if explicit == "search_collect" and inferred != "search_collect" else explicit
    return inferred


def _fold_legacy_sales_douyin_followup_nodes(nodes: Any) -> list[dict[str, Any]]:
    """Move legacy standalone precision actions onto the preceding collection node."""
    items = [item for item in (nodes if isinstance(nodes, list) else []) if isinstance(item, dict)]
    prepared: list[dict[str, Any]] = []
    current_collection: Optional[dict[str, Any]] = None
    for item in items:
        plan = item.get("plan") if isinstance(item.get("plan"), dict) else {}
        task_kind = _clean_text(plan.get("task_kind") or plan.get("taskKind"), 64).lower()
        ability_key = _clean_text(item.get("ability_key") or item.get("abilityKey"), 128).lower()
        action = _sales_douyin_node_action(item)
        is_sales_douyin = _is_sales_node(item) and task_kind == "douyin_leads" and ability_key == "douyin_leads"
        if is_sales_douyin and action == "search_collect":
            current_collection = item
            prepared.append(item)
            continue
        if is_sales_douyin and action in (*_SALES_DOUYIN_FOLLOWUP_ACTIONS, "reply_comments") and current_collection is not None:
            if action == "reply_comments":
                current_plan = current_collection.get("plan") if isinstance(current_collection.get("plan"), dict) else {}
                current_payload = current_plan.get("payload") if isinstance(current_plan.get("payload"), dict) else {}
                current_params = dict(current_payload.get("params") if isinstance(current_payload.get("params"), dict) else {})
                current_params["reply_precise_comments"] = True
                legacy_plan = item.get("plan") if isinstance(item.get("plan"), dict) else {}
                legacy_payload = legacy_plan.get("payload") if isinstance(legacy_plan.get("payload"), dict) else {}
                legacy_params = legacy_payload.get("params") if isinstance(legacy_payload.get("params"), dict) else {}
                for source_key, target_key in (
                    ("reply_comment_mode", "reply_comment_mode"),
                    ("reply_comment_text", "reply_comment_text"),
                    ("reply_comment_prompt", "reply_comment_prompt"),
                    ("reply_comment_seed_text", "reply_comment_seed_text"),
                    ("comment_mode", "reply_comment_mode"),
                    ("comment_text", "reply_comment_text"),
                    ("comment_prompt", "reply_comment_prompt"),
                    ("comment_seed_text", "reply_comment_seed_text"),
                ):
                    if legacy_params.get(source_key) not in (None, "", []):
                        current_params[target_key] = legacy_params.get(source_key)
                current_payload["params"] = current_params
                current_plan["payload"] = current_payload
                current_collection["plan"] = current_plan
            # Legacy standalone touch nodes must not make collection execute
            # touch actions. The standalone precise-touch node owns that work.
            continue
        prepared.append(item)
    return prepared


def _canonical_workflow_nodes(nodes: Any) -> list[dict[str, Any]]:
    prepared = _clean_legacy_sales_action_children(_visible_workflow_nodes(nodes))
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
    "template_id",
    "templateId",
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
    raw, current_configured = _sales_digital_human_meta_value(current_meta, "digital_human_template", "digital_human_template_configured")
    if not current_configured:
        raw = personal_meta.get("digital_human_template")
    if not isinstance(raw, dict):
        return ""
    return _clean_text(
        raw.get("style_id")
        or raw.get("styleId")
        or raw.get("template_id")
        or raw.get("templateId")
        or raw.get("id"),
        128,
    )


def _sales_digital_human_meta_value(
    meta: dict[str, Any],
    key: str,
    configured_key: str,
) -> tuple[Any, bool]:
    """Resolve a selected digital-human value without treating empty legacy
    metadata as an intentional override.

    New saves include ``*_configured`` so an explicit clear remains a clear;
    old rows with an empty current-template value still fall back to the
    personal selection.
    """
    if key not in meta:
        return None, False
    value = meta.get(key)
    # ``None`` is the legacy representation of an explicit "do not use"
    # choice for the template itself; keep that clear instead of falling back.
    if value is None:
        return None, True
    if meta.get(configured_key) is True:
        return value, True
    if isinstance(value, dict):
        if any(value.get(name) for name in ("avatars", "voices")):
            return value, True
        if any(
            str(value.get(name) or "").strip()
            for name in ("style_id", "styleId", "template_id", "templateId", "id")
        ):
            return value, True
        return value, False
    return value, bool(value)


def _sales_digital_human_resources(
    personal: Optional[IPContentScheduleTemplate],
    current: Optional[IPContentScheduleTemplate],
) -> dict[str, list[dict[str, Any]]]:
    """Read the avatar/voice allow-list stored on the active personal template.

    The list is deliberately taken from template metadata only.  Falling back to
    the user's latest/all assets here would make a workflow silently use assets
    that were never selected in the template.
    """
    personal_meta = personal.meta if personal and isinstance(personal.meta, dict) else {}
    current_meta = current.meta if current and isinstance(current.meta, dict) else {}
    raw, current_configured = _sales_digital_human_meta_value(
        current_meta,
        "digital_human_resources",
        "digital_human_resources_configured",
    )
    if not current_configured:
        raw = personal_meta.get("digital_human_resources")
    if not isinstance(raw, dict):
        return {"avatars": [], "voices": []}

    avatars: list[dict[str, Any]] = []
    seen_avatars: set[str] = set()
    for item in raw.get("avatars") if isinstance(raw.get("avatars"), list) else []:
        if not isinstance(item, dict):
            continue
        status = _clean_text(item.get("status") or item.get("state"), 32).lower()
        if status and status not in {"succeed", "success", "completed", "complete", "done", "ready", "published", "active"}:
            continue
        provider = _clean_text(item.get("provider") or item.get("source"), 32).lower()
        virtualman_id = _clean_text(item.get("virtualman_id") or item.get("virtualmanId"), 128)
        avatar_id = _clean_text(item.get("avatar") or item.get("avatar_id") or item.get("avatarId"), 128)
        if provider in {"shanjian", "shanjian_v2", "digital_human"} and not virtualman_id:
            virtualman_id = avatar_id
        identifier = virtualman_id if provider in {"shanjian", "digital_human", "shanjian_v2"} else avatar_id
        if not identifier:
            identifier = virtualman_id or avatar_id
        if not identifier:
            continue
        key = f"{provider}:{identifier}"
        if key in seen_avatars:
            continue
        seen_avatars.add(key)
        avatars.append(
            {
                "provider": provider or ("shanjian" if virtualman_id else "hifly"),
                "virtualman_id": virtualman_id,
                "avatar": avatar_id,
                "profile_id": _safe_int(item.get("profile_id") or item.get("source_record_id") or item.get("id")),
                "title": _clean_text(item.get("title") or item.get("name"), 128),
                "cover_url": _clean_text(item.get("cover_url") or item.get("coverUrl") or item.get("image_url"), 1000),
            }
        )

    voices: list[dict[str, Any]] = []
    seen_voices: set[str] = set()
    for item in raw.get("voices") if isinstance(raw.get("voices"), list) else []:
        if not isinstance(item, dict):
            continue
        status = _clean_text(item.get("status") or item.get("state"), 32).lower()
        if status and status not in {"succeed", "success", "completed", "complete", "done", "ready", "published", "active"}:
            continue
        provider = _clean_text(item.get("provider") or item.get("source"), 32).lower()
        voice = _clean_text(item.get("voice") or item.get("voice_id") or item.get("speaker_id") or item.get("speakerId"), 128)
        if not voice or voice in seen_voices:
            continue
        seen_voices.add(voice)
        voices.append(
            {
                "provider": provider or "hifly",
                "voice": voice,
                "title": _clean_text(item.get("title") or item.get("name"), 128),
                "source_record_id": _safe_int(item.get("source_record_id") or item.get("id")),
            }
        )
    return {"avatars": avatars, "voices": voices}


def _prepare_publish_action_nodes(
    *,
    db: Session,
    owner: User,
    installation_id: str,
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prepared = _clean_legacy_sales_action_children(nodes)
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
    if "我的评论区" in text:
        return "self_comment_monitor"
    if "精准用户触达" in text or "精准触达" in text:
        return "precise_touch"
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


_SALES_DOUYIN_FOLLOWUP_ACTIONS = (
    "follow_comment",
    "mention_comment",
    "direct_message",
)


def _sales_douyin_collection_reply_params(params: Any) -> dict[str, Any]:
    source = params if isinstance(params, dict) else {}
    legacy_values = []
    for key in ("followup_actions", "touch_actions"):
        values = source.get(key)
        if isinstance(values, list):
            legacy_values.extend(values)
    legacy_reply = "reply_comments" in {
        _clean_text(item, 64).lower() for item in (legacy_values if isinstance(legacy_values, list) else [])
    }
    mode = _clean_text(source.get("reply_comment_mode") or source.get("comment_mode") or "fixed", 32).lower() or "fixed"
    has_reply_config = any(
        key in source
        for key in (
            "reply_precise_comments",
            "reply_comment_mode",
            "reply_comment_text",
            "reply_comment_prompt",
            "reply_comment_seed_text",
        )
    ) or legacy_reply
    if not has_reply_config:
        return {}
    return {
        "reply_precise_comments": _bool_param(source.get("reply_precise_comments"), legacy_reply),
        "reply_comment_mode": mode if mode in {"fixed", "ai", "rewrite"} else "fixed",
        "reply_comment_text": _clean_text(source.get("reply_comment_text") or source.get("comment_text"), 500),
        "reply_comment_prompt": _clean_text(source.get("reply_comment_prompt") or source.get("comment_prompt"), 1000),
        "reply_comment_seed_text": _clean_text(source.get("reply_comment_seed_text") or source.get("comment_seed_text"), 500),
    }


def _sales_douyin_followup_actions(value: Any) -> list[str]:
    rows = value if isinstance(value, list) else []
    selected = {_clean_text(item, 64).lower() for item in rows}
    return [action for action in _SALES_DOUYIN_FOLLOWUP_ACTIONS if action in selected]


def _sales_douyin_action_payload(node: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Reduce a sales Douyin node to the action-only Online contract."""
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    inferred_action = _sales_action_from_note(node.get("note") or node.get("ability_label"))
    requested_action = _clean_text(payload.get("action") or params.get("sales_action"), 64).lower()
    valid_actions = {
        "search_collect",
        "precise_touch",
        "self_comment_monitor",
        "account_nurture",
        "reply_comments",
        "follow_comment",
        "mention_comment",
        "direct_message",
        "stranger_message",
    }
    if requested_action in valid_actions:
        action = inferred_action if requested_action == "search_collect" and inferred_action != "search_collect" else requested_action
    else:
        action = inferred_action
    result: dict[str, Any] = {"action": action or "search_collect"}
    if (action or "search_collect") == "search_collect":
        result["params"] = {
            "customer_scope": "current_collection_batch",
        }
        result["params"].update(_sales_douyin_collection_reply_params(params))
        for key in ("keyword", "regions", "max_results", "max_videos_per_run", "mode"):
            value = params.get(key)
            if key == "keyword" and any(marker in _clean_text(value, 200) for marker in ("抖音获客", "精准用户触达", "精准触达")):
                continue
            if value not in (None, "", []):
                result["params"][key] = copy.deepcopy(value)
    if action == "precise_touch":
        has_explicit_actions = "touch_actions" in params or "followup_actions" in params
        raw_actions = params.get("touch_actions") if "touch_actions" in params else params.get("followup_actions")
        touch_actions = _sales_douyin_followup_actions(raw_actions)
        if not has_explicit_actions:
            touch_actions = list(_SALES_DOUYIN_FOLLOWUP_ACTIONS)
        result["params"] = {
            "touch_actions": touch_actions,
            "customer_scope": "precise_pool",
        }
        if params.get("max_users") not in (None, "", []):
            try:
                result["params"]["max_users"] = max(1, min(200, int(params.get("max_users"))))
            except (TypeError, ValueError):
                result["params"]["max_users"] = 20
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
    # Activation and runtime use the same server-side effective template
    # context. Workflow nodes may retain action/timing controls, but they
    # never retain an activation-time copy of personal template resources.
    template_context = _h5_dh_context_params(db, owner.id)
    resource_overrides = (
        _personal_default_resource_overrides(personal, reference_template)
        if personal and reference_template
        else {"keyword_ids": False, "competitor_ids": False, "memory_doc_ids": False}
    )
    keyword_ids = _clean_id_list(template_context.get("keyword_ids"), 100)
    competitor_ids = _clean_id_list(template_context.get("competitor_ids"), 100)
    keyword_owner_id = owner.id if resource_overrides.get("keyword_ids") else reference_owner_id
    competitor_owner_id = owner.id if resource_overrides.get("competitor_ids") else reference_owner_id
    keywords = _active_keywords_for_ids(db, keyword_owner_id, keyword_ids)
    competitors = _active_competitors_for_ids(db, competitor_owner_id, competitor_ids)
    requirements = template_context.get("requirements") if isinstance(template_context.get("requirements"), dict) else {}
    keyword_texts = [
        _clean_text(value, 120)
        for value in (template_context.get("keyword_texts") if isinstance(template_context.get("keyword_texts"), list) else [])
        if _clean_text(value, 120)
    ]
    competitor_texts = [
        _clean_text(value, 160)
        for value in (template_context.get("competitors") if isinstance(template_context.get("competitors"), list) else [])
        if _clean_text(value, 160)
    ]
    memory_doc_ids = [
        str(value or "").strip()
        for value in (template_context.get("memory_doc_ids") if isinstance(template_context.get("memory_doc_ids"), list) else [])
        if str(value or "").strip()
    ]
    memory_docs = template_context.get("memory_docs") if isinstance(template_context.get("memory_docs"), list) else []
    digital_human_provider = _sales_digital_human_provider(snapshot_extra, reference_template)
    digital_human_resources = template_context.get("digital_human_resources")
    if not isinstance(digital_human_resources, dict):
        digital_human_resources = {"avatars": [], "voices": []}
    selected_avatars = digital_human_resources.get("avatars") if isinstance(digital_human_resources.get("avatars"), list) else []
    selected_voices = digital_human_resources.get("voices") if isinstance(digital_human_resources.get("voices"), list) else []
    hifly_avatar_rows = [
        row for row in selected_avatars
        if row.get("provider") not in {"shanjian", "shanjian_v2", "digital_human"}
        and _clean_text(row.get("avatar"), 128)
    ]
    shanjian_virtualmans = [
        {
            "profile_id": _safe_int(row.get("profile_id")),
            "virtualman_id": _clean_text(row.get("virtualman_id"), 128),
            "title": _clean_text(row.get("title"), 128),
            "cover_url": _clean_text(row.get("cover_url"), 1000),
        }
        for row in selected_avatars
        if _clean_text(row.get("virtualman_id"), 128)
    ]
    hifly_avatar = _clean_text((hifly_avatar_rows[0] if hifly_avatar_rows else {}).get("avatar"), 128)
    shanjian_virtualman = _clean_text((shanjian_virtualmans[0] if shanjian_virtualmans else {}).get("virtualman_id"), 128)
    hifly_voice = _clean_text((selected_voices[0] if selected_voices else {}).get("voice"), 128)
    template_language = _clean_text(template_context.get("language"), 64) or _template_language(requirements, reference_template)

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
                # The workflow schedule is durable, but its selected IP
                # template is resolved live on every run.
                payload["template_source"] = "personal_current"
                for stale_key in (
                    "template_id",
                    "keyword_ids",
                    "competitor_ids",
                    "memory_doc_ids",
                    "memory_docs",
                    "requirements",
                ):
                    payload.pop(stale_key, None)
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
            params["language"] = template_language
            params["target_language"] = template_language
            payload["params"] = params
            plan["payload"] = payload

        if task_kind == "client_workflow" and action == "shanjian_digital_human_video":
            has_hifly = True
            if digital_human_provider == _SALES_DH_PROVIDER_LEGACY:
                params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                inner = dict(params)
                inner.pop("avatar", None)
                inner.pop("avatar_id", None)
                if hifly_avatar:
                    inner["avatar"] = hifly_avatar
                inner.pop("voice", None)
                inner.pop("speaker_id", None)
                if hifly_voice:
                    inner["voice"] = hifly_voice
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
                params["requirements"] = copy.deepcopy(requirements)
                params["keyword_ids"] = list(keyword_ids)
                params["keywords"] = list(keyword_texts)
                params["keyword_texts"] = list(keyword_texts)
                params["competitors"] = list(competitor_texts)
                params["memory_doc_ids"] = list(memory_doc_ids)
                params["memory_docs"] = copy.deepcopy(memory_docs)
                params["language"] = template_language
                params["target_language"] = template_language
                params.setdefault("sales_node_label", _clean_text(node.get("ability_label") or node.get("note") or plan.get("title"), 160))
                params["script_source"] = "ip_daily_industry_hot_oral"
                params["virtualman_candidates"] = copy.deepcopy(shanjian_virtualmans)
                params["virtualman_selection_mode"] = "daily_round_robin" if shanjian_virtualmans else "fixed"
                params.pop("virtualman_id", None)
                if shanjian_virtualman:
                    params["virtualman_id"] = shanjian_virtualman
                params.pop("voice", None)
                params.pop("speaker_id", None)
                if hifly_voice:
                    params["voice"] = hifly_voice
                    params["speaker_id"] = hifly_voice
                params["voice_candidates"] = copy.deepcopy(selected_voices)
                params["voice_selection_mode"] = "daily_round_robin" if selected_voices else "fixed"
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
                inner.pop("avatar", None)
                inner.pop("avatar_id", None)
                if hifly_avatar:
                    inner["avatar"] = hifly_avatar
                inner.pop("voice", None)
                inner.pop("speaker_id", None)
                if hifly_voice:
                    inner["voice"] = hifly_voice
                payload["payload"] = inner
                plan["payload"] = payload
            else:
                params = {
                    key: value
                    for key, value in inner.items()
                    if key not in {"avatar", "avatar_id", "st_show", "aigc_flag"}
                }
                params["requirements"] = copy.deepcopy(requirements)
                params["keyword_ids"] = list(keyword_ids)
                params["keywords"] = list(keyword_texts)
                params["keyword_texts"] = list(keyword_texts)
                params["competitors"] = list(competitor_texts)
                params["memory_doc_ids"] = list(memory_doc_ids)
                params["memory_docs"] = copy.deepcopy(memory_docs)
                params["language"] = template_language
                params["target_language"] = template_language
                params.setdefault("sales_node_label", _clean_text(node.get("ability_label") or node.get("note") or plan.get("title"), 160))
                params["script_source"] = "ip_daily_industry_hot_oral"
                params["virtualman_candidates"] = copy.deepcopy(shanjian_virtualmans)
                params["virtualman_selection_mode"] = "daily_round_robin" if shanjian_virtualmans else "fixed"
                params.pop("virtualman_id", None)
                if shanjian_virtualman:
                    params["virtualman_id"] = shanjian_virtualman
                params.pop("voice", None)
                params.pop("speaker_id", None)
                if hifly_voice:
                    params["voice"] = hifly_voice
                    params["speaker_id"] = hifly_voice
                params["voice_candidates"] = copy.deepcopy(selected_voices)
                params["voice_selection_mode"] = "daily_round_robin" if selected_voices else "fixed"
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
            if not hifly_avatar_rows:
                missing.append("素材库：请先创建可用的旧版数字人形象分身")
        elif not shanjian_virtualmans:
            missing.append("素材库：请先创建并训练完成可用的数字人形象分身（数字人2.0）")
        if digital_human_provider == _SALES_DH_PROVIDER_V2 and not digital_human_template_id:
            missing.append("IP人设定位-模板：请为当前模板选择数字人剪辑模板")
        if not selected_voices:
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


def _clear_workflow_template_cache() -> None:
    with _WORKFLOW_TEMPLATE_CACHE_LOCK:
        _WORKFLOW_TEMPLATE_CACHE.clear()


def _workflow_template_cache_lock(key: tuple[int, str]) -> threading.Lock:
    with _WORKFLOW_TEMPLATE_CACHE_LOCK:
        return _WORKFLOW_TEMPLATE_CACHE_KEY_LOCKS.setdefault(key, threading.Lock())


def _cached_workflow_template_payloads(key: tuple[int, str]) -> Optional[list[dict[str, Any]]]:
    now = time.monotonic()
    with _WORKFLOW_TEMPLATE_CACHE_LOCK:
        entry = _WORKFLOW_TEMPLATE_CACHE.get(key)
        if not entry:
            return None
        created_at, payloads = entry
        if now - created_at >= _WORKFLOW_TEMPLATE_CACHE_TTL_SECONDS:
            _WORKFLOW_TEMPLATE_CACHE.pop(key, None)
            return None
        return copy.deepcopy(payloads)


def _store_workflow_template_payloads(key: tuple[int, str], payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stored = copy.deepcopy(payloads)
    now = time.monotonic()
    with _WORKFLOW_TEMPLATE_CACHE_LOCK:
        _WORKFLOW_TEMPLATE_CACHE[key] = (now, stored)
        expired = [
            cache_key
            for cache_key, (created_at, _) in _WORKFLOW_TEMPLATE_CACHE.items()
            if now - created_at >= _WORKFLOW_TEMPLATE_CACHE_TTL_SECONDS
        ]
        for cache_key in expired:
            _WORKFLOW_TEMPLATE_CACHE.pop(cache_key, None)
    return copy.deepcopy(stored)


def _workflow_template_payloads(db: Session, owner: User, installation_id: str) -> list[dict[str, Any]]:
    key = (int(owner.id), str(installation_id or ""))
    cached = _cached_workflow_template_payloads(key)
    if cached is not None:
        return cached

    # Multiple UI entry points can request the same device at once. Serialize
    # only that user/device key, then re-check the cache after the first query.
    with _workflow_template_cache_lock(key):
        cached = _cached_workflow_template_payloads(key)
        if cached is not None:
            return cached

        system_rows = (
            db.query(H5WorkflowTemplate)
            .filter(
                H5WorkflowTemplate.owner_user_id == _SYSTEM_WORKFLOW_OWNER_ID,
                H5WorkflowTemplate.status == "active",
            )
            .order_by(H5WorkflowTemplate.id.asc())
            .all()
        )
        system_rows = [row for row in system_rows if _is_system_catalog_template(row)]
        template_scope = (
            or_(H5WorkflowTemplate.installation_id == "", H5WorkflowTemplate.installation_id == installation_id)
            if installation_id
            else H5WorkflowTemplate.installation_id == ""
        )
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
        granted_ids = [grant.template_id for grant in grants]
        granted_rows = []
        if granted_ids:
            # A grant transfers the template to the target account.  The
            # template's installation_id belongs to the granting account's
            # device and must not hide the template on the target's device.
            # Keep the installation scope for owned templates above, while
            # resolving granted templates by the active grant alone.
            granted_rows = (
                db.query(H5WorkflowTemplate)
                .filter(H5WorkflowTemplate.id.in_(granted_ids), H5WorkflowTemplate.status == "active")
                .order_by(H5WorkflowTemplate.updated_at.desc())
                .all()
            )
        grant_map: dict[int, list[int]] = {}
        if own_rows:
            own_ids = [row.id for row in own_rows]
            for grant in (
                db.query(H5WorkflowTemplateGrant)
                .filter(H5WorkflowTemplateGrant.template_id.in_(own_ids), H5WorkflowTemplateGrant.status == "active")
                .all()
            ):
                grant_map.setdefault(grant.template_id, []).append(grant.target_user_id)

        owner_ids = {
            int(row.owner_user_id)
            for row in granted_rows
            if row.owner_user_id and int(row.owner_user_id) != int(owner.id)
        }
        owners_by_id = {}
        if owner_ids:
            owners_by_id = {
                user.id: user
                for user in db.query(User).filter(User.id.in_(owner_ids)).all()
            }

        payloads = [
            *[_template_payload(row, source="system") for row in system_rows],
            *[_template_payload(row, source="own", grants=grant_map.get(row.id, [])) for row in own_rows],
            *[
                _template_payload(row, owner=owners_by_id.get(row.owner_user_id), source="granted")
                for row in granted_rows
                if row.owner_user_id != owner.id
            ],
        ]
        return _store_workflow_template_payloads(key, payloads)


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


def _is_system_catalog_template(row: Optional[H5WorkflowTemplate]) -> bool:
    if row is None or row.owner_user_id is None or int(row.owner_user_id) != _SYSTEM_WORKFLOW_OWNER_ID:
        return False
    meta = row.meta if isinstance(row.meta, dict) else {}
    return (
        _clean_text(meta.get("source"), 64) == _SYSTEM_WORKFLOW_CATALOG_SOURCE
        and _clean_text(meta.get("system_template_key"), 128) in _ENABLED_SYSTEM_WORKFLOW_KEYS
    )


def _accessible_template(db: Session, template_id: int, owner_user_id: int) -> H5WorkflowTemplate:
    row = db.query(H5WorkflowTemplate).filter(H5WorkflowTemplate.id == template_id, H5WorkflowTemplate.status == "active").first()
    if not row:
        raise HTTPException(status_code=404, detail="模板不存在")
    if _is_system_catalog_template(row):
        return row
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
    """Start a workflow node immediately when activation is inside today's window.

    Daily-times scheduling normally chooses the next clock time strictly after
    activation. For an employee enabled after a node's start but before its
    end, that would incorrectly defer the first run until tomorrow. The first
    activation should instead enter the current window; later recurring runs
    continue to use the node's configured daily time.
    """
    if task_kind != "client_workflow":
        return False
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
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
                # Template resources are live personal-settings references.
                # The node context identifies the workflow only; execution
                # resolves the current template instead of this activation.
                "template_source": "personal_current",
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
    return {
        "ok": True,
        "templates": _workflow_template_payloads(db, owner, iid),
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
            _clear_workflow_template_cache()
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
    _clear_workflow_template_cache()
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
    body_iid = _clean_text(body.installation_id, 128)
    header_iid = _clean_text(x_installation_id, 128)
    if body_iid and header_iid and body_iid != header_iid:
        raise HTTPException(status_code=409, detail="员工槽位参数与当前设备不一致，请刷新后重试")
    requested_iid = body_iid or header_iid
    bound_iid = _clean_text(row.installation_id, 128)
    if bound_iid and requested_iid and bound_iid != requested_iid:
        raise HTTPException(status_code=409, detail="该员工已绑定其他设备槽位，请切换到原设备后编辑")
    meta: Optional[dict[str, Any]] = None
    if requested_iid and not bound_iid:
        row.installation_id = requested_iid
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
    _clear_workflow_template_cache()
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
    _clear_workflow_template_cache()
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
    _clear_workflow_template_cache()
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
    # A granted template belongs to the granting account's template namespace,
    # but it must run on the recipient's own device. Only enforce the bound
    # slot for templates owned by the account activating them.
    is_granted_template = int(template.owner_user_id) != int(owner.id)
    if bound_iid and bound_iid != iid and not is_granted_template:
        raise HTTPException(status_code=409, detail="该员工已绑定其他设备槽位，请从当前设备的员工列表进入")
    if not bound_iid and template.owner_user_id == owner.id:
        template.installation_id = iid
        template.updated_at = datetime.utcnow()
        db.commit()
    nodes = _clean_nodes(template.nodes or [])
    _assert_workflow_feature_permissions(db, owner.id, nodes)
    template_meta = template.meta if isinstance(template.meta, dict) else {}
    system_template_key = _clean_text(template_meta.get("system_template_key"), 128)
    is_system_catalog = _is_system_catalog_template(template)
    snapshot_extra = (
        {"source": "system"}
        if is_system_catalog
        else ({"source": "granted"} if is_granted_template else None)
    )
    if system_template_key in _ENABLED_SYSTEM_WORKFLOW_KEYS:
        snapshot_extra = {
            **(snapshot_extra or {}),
            "template_key": system_template_key,
            "source": "system" if is_system_catalog else ("granted" if is_granted_template else "own"),
        }
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
    _assert_workflow_feature_permissions(db, owner.id, nodes)
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
