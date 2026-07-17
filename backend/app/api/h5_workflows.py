from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    H5WorkflowActivation,
    H5WorkflowTemplate,
    H5WorkflowTemplateGrant,
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
    _serialize_task,
)

router = APIRouter()

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


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


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def _clean_time(value: Any) -> str:
    text = str(value or "").strip()
    if not _TIME_RE.match(text):
        raise HTTPException(status_code=400, detail="节点时间格式应为 HH:MM")
    return text


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
        if not task_kind:
            raise HTTPException(status_code=400, detail=f"{title} 缺少任务类型")
        if task_kind == "client_workflow" and not str(payload.get("action") or "").strip():
            raise HTTPException(status_code=400, detail=f"{title} 缺少客户端动作")
        if task_kind == "capability" and not str(payload.get("capability_id") or "").strip():
            raise HTTPException(status_code=400, detail=f"{title} 缺少能力 ID")
        cleaned.append(
            {
                "id": str(raw.get("id") or f"node_{len(cleaned) + 1}")[:64],
                "time": _clean_time(raw.get("time")),
                "ability_key": str(raw.get("ability_key") or raw.get("abilityKey") or "").strip()[:128],
                "ability_label": str(raw.get("ability_label") or raw.get("abilityLabel") or raw.get("label") or title).strip()[:160],
                "department_id": str(raw.get("department_id") or raw.get("departmentId") or "").strip()[:64],
                "department_name": str(raw.get("department_name") or raw.get("departmentName") or "").strip()[:80],
                "note": str(raw.get("note") or "").strip()[:2000],
                "param_configured": bool(raw.get("param_configured")),
                "plan": {
                    "title": title,
                    "task_kind": task_kind,
                    "content": content,
                    "payload": payload,
                },
            }
        )
    if not cleaned:
        raise HTTPException(status_code=400, detail="请至少添加一个工作流节点")
    cleaned.sort(key=lambda item: item["time"])
    return cleaned[:48]


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
    now = datetime.utcnow()
    stopped_ids = _stop_active_for_device(db, owner.id, iid, now)
    db.commit()
    created_task_ids: list[int] = []
    try:
        for node in nodes:
            plan = node.get("plan") or {}
            task_kind = str(plan.get("task_kind") or "").strip().lower()
            payload = dict(plan.get("payload") or {})
            payload["h5_context"] = {
                **(payload.get("h5_context") if isinstance(payload.get("h5_context"), dict) else {}),
                "workflow_template_id": template.id,
                "workflow_template_name": template.name,
                "workflow_node_id": node.get("id"),
                "workflow_node_time": node.get("time"),
                "ability_key": node.get("ability_key"),
                "ability_label": node.get("ability_label"),
                "department_id": node.get("department_id"),
                "department_name": node.get("department_name"),
            }
            scheduled = _create_task_row(
                db,
                ScheduledTaskCreate(
                    title=str(plan.get("title") or node.get("ability_label") or template.name),
                    task_kind=task_kind,
                    content=str(plan.get("content") or f"H5 工作流：{node.get('ability_label') or template.name}"),
                    payload=payload,
                    schedule_type="daily_times",
                    daily_times=[node["time"]],
                    timezone_offset_minutes=body.timezone_offset_minutes if body.timezone_offset_minutes is not None else 480,
                    installation_ids=[] if task_kind in _SERVER_SIDE_TASK_KINDS else [iid],
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
    activation = H5WorkflowActivation(
        user_id=owner.id,
        installation_id=iid,
        template_id=template.id,
        template_owner_user_id=template.owner_user_id,
        status="active",
        scheduled_task_ids=created_task_ids,
        template_snapshot={"name": template.name, "nodes": nodes},
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(activation)
    db.commit()
    db.refresh(activation)
    tasks = db.query(ScheduledTask).filter(ScheduledTask.id.in_(created_task_ids)).all() if created_task_ids else []
    return {
        "ok": True,
        "activation": _activation_payload(activation, template),
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
