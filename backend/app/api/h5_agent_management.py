from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    H5AgentMemoryGrant,
    H5AgentTemplateGrant,
    H5WorkflowTemplate,
    H5WorkflowTemplateGrant,
    IPContentScheduleTemplate,
    OpenClawMemoryDocument,
    User,
)
from .admin import _agent_sub_user_ids
from .auth import get_current_user
from .h5_workflows import _template_payload as _workflow_template_payload
from .ip_content_studio import _template_payload as _ip_template_payload
from .mobile_identity import online_user_for_mobile_user
from .openclaw_memory_cloud import _doc_summary

router = APIRouter()


class AgentGrantBody(BaseModel):
    workflow_template_ids: list[int] = Field(default_factory=list)
    ip_template_ids: list[int] = Field(default_factory=list)
    memory_doc_ids: list[str] = Field(default_factory=list)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def _require_agent(current_user: User) -> None:
    if not getattr(current_user, "is_agent", False):
        raise HTTPException(status_code=403, detail="仅代理商可用")


def _user_payload(row: User) -> dict[str, Any]:
    return {
        "id": row.id,
        "email": row.email,
        "is_agent": bool(row.is_agent),
        "agent_level": int(row.agent_level or 0),
        "credits": float(row.credits or 0),
        "created_at": _iso(row.created_at),
    }


def _target_sub_user(db: Session, agent: User, user_id: int) -> User:
    allowed = set(_agent_sub_user_ids(db, int(agent.id)))
    if int(user_id or 0) not in allowed:
        raise HTTPException(status_code=404, detail="下级用户不存在")
    row = db.query(User).filter(User.id == int(user_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail="下级用户不存在")
    return row


def _clean_int_ids(values: list[int]) -> list[int]:
    out: list[int] = []
    for raw in values or []:
        try:
            val = int(raw)
        except Exception:
            continue
        if val > 0 and val not in out:
            out.append(val)
    return out[:500]


def _clean_doc_ids(values: list[str]) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        val = "".join(ch for ch in str(raw or "").strip() if ch.isalnum() or ch in "_-")[:64]
        if val and val not in out:
            out.append(val)
    return out[:500]


def _sync_int_grants(
    db: Session,
    *,
    model: Any,
    id_attr: str,
    owner_user_id: int,
    target_user_id: int,
    requested_ids: list[int],
) -> list[int]:
    now = datetime.utcnow()
    requested = set(requested_ids)
    existing = (
        db.query(model)
        .filter(model.owner_user_id == owner_user_id, model.target_user_id == target_user_id)
        .all()
    )
    remaining = set(requested)
    for grant in existing:
        item_id = int(getattr(grant, id_attr) or 0)
        grant.status = "active" if item_id in requested else "revoked"
        grant.updated_at = now
        remaining.discard(item_id)
    for item_id in remaining:
        db.add(
            model(
                **{
                    id_attr: item_id,
                    "owner_user_id": owner_user_id,
                    "target_user_id": target_user_id,
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                }
            )
        )
    return sorted(requested)


def _sync_memory_grants(
    db: Session,
    *,
    owner_user_id: int,
    target_user_id: int,
    requested_doc_ids: list[str],
) -> list[str]:
    now = datetime.utcnow()
    requested = set(requested_doc_ids)
    existing = (
        db.query(H5AgentMemoryGrant)
        .filter(H5AgentMemoryGrant.owner_user_id == owner_user_id, H5AgentMemoryGrant.target_user_id == target_user_id)
        .all()
    )
    remaining = set(requested)
    for grant in existing:
        doc_id = str(grant.memory_doc_id or "")
        grant.status = "active" if doc_id in requested else "revoked"
        grant.updated_at = now
        remaining.discard(doc_id)
    for doc_id in remaining:
        db.add(
            H5AgentMemoryGrant(
                memory_doc_id=doc_id,
                owner_user_id=owner_user_id,
                target_user_id=target_user_id,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
    return sorted(requested)


@router.get("/api/h5-agent/summary")
def h5_agent_summary(current_user: User = Depends(get_current_user)):
    return {
        "ok": True,
        "can_manage": bool(getattr(current_user, "is_agent", False)),
        "agent": _user_payload(current_user),
    }


@router.get("/api/h5-agent/sub-users")
def h5_agent_sub_users(
    q: str = Query("", max_length=120),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_agent(current_user)
    ids = _agent_sub_user_ids(db, int(current_user.id))
    if not ids:
        return {"ok": True, "items": [], "total": 0, "limit": limit, "offset": offset}
    query = db.query(User).filter(User.id.in_(ids))
    term = (q or "").strip()
    if term:
        filters = [User.email.ilike(f"%{term}%")]
        if term.isdigit():
            filters.append(User.id == int(term))
        query = query.filter(or_(*filters))
    total = int(query.with_entities(func.count(User.id)).scalar() or 0)
    rows = query.order_by(User.created_at.desc(), User.id.desc()).offset(offset).limit(limit).all()
    return {"ok": True, "items": [_user_payload(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/api/h5-agent/resources")
def h5_agent_resources(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_agent(current_user)
    workflow_owner = online_user_for_mobile_user(db, current_user)
    workflow_rows = (
        db.query(H5WorkflowTemplate)
        .filter(H5WorkflowTemplate.owner_user_id == workflow_owner.id, H5WorkflowTemplate.status == "active")
        .order_by(H5WorkflowTemplate.updated_at.desc(), H5WorkflowTemplate.id.desc())
        .limit(300)
        .all()
    )
    ip_rows = (
        db.query(IPContentScheduleTemplate)
        .filter(IPContentScheduleTemplate.user_id == current_user.id, IPContentScheduleTemplate.status == "active")
        .order_by(IPContentScheduleTemplate.updated_at.desc(), IPContentScheduleTemplate.id.desc())
        .limit(300)
        .all()
    )
    memory_rows = (
        db.query(OpenClawMemoryDocument)
        .filter(OpenClawMemoryDocument.target_user_id == current_user.id, OpenClawMemoryDocument.status == "active")
        .order_by(OpenClawMemoryDocument.updated_at.desc(), OpenClawMemoryDocument.id.desc())
        .limit(300)
        .all()
    )
    return {
        "ok": True,
        "workflow_templates": [_workflow_template_payload(row, source="own") for row in workflow_rows],
        "ip_templates": [_ip_template_payload(row) for row in ip_rows],
        "memory_docs": [_doc_summary(row, include_content=False) for row in memory_rows],
    }


@router.get("/api/h5-agent/sub-users/{user_id}/grants")
def h5_agent_user_grants(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_agent(current_user)
    target = _target_sub_user(db, current_user, user_id)
    workflow_owner = online_user_for_mobile_user(db, current_user)
    workflow_ids = [
        int(row.template_id)
        for row in db.query(H5WorkflowTemplateGrant)
        .filter(
            H5WorkflowTemplateGrant.owner_user_id == workflow_owner.id,
            H5WorkflowTemplateGrant.target_user_id == target.id,
            H5WorkflowTemplateGrant.status == "active",
        )
        .all()
    ]
    ip_ids = [
        int(row.template_id)
        for row in db.query(H5AgentTemplateGrant)
        .filter(
            H5AgentTemplateGrant.owner_user_id == current_user.id,
            H5AgentTemplateGrant.target_user_id == target.id,
            H5AgentTemplateGrant.status == "active",
        )
        .all()
    ]
    memory_ids = [
        str(row.memory_doc_id)
        for row in db.query(H5AgentMemoryGrant)
        .filter(
            H5AgentMemoryGrant.owner_user_id == current_user.id,
            H5AgentMemoryGrant.target_user_id == target.id,
            H5AgentMemoryGrant.status == "active",
        )
        .all()
    ]
    return {
        "ok": True,
        "user": _user_payload(target),
        "workflow_template_ids": workflow_ids,
        "ip_template_ids": ip_ids,
        "memory_doc_ids": memory_ids,
    }


@router.post("/api/h5-agent/sub-users/{user_id}/grants")
def h5_agent_save_user_grants(
    user_id: int,
    body: AgentGrantBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_agent(current_user)
    target = _target_sub_user(db, current_user, user_id)
    workflow_owner = online_user_for_mobile_user(db, current_user)

    workflow_requested = _clean_int_ids(body.workflow_template_ids)
    if workflow_requested:
        owned_workflow_ids = {
            int(row.id)
            for row in db.query(H5WorkflowTemplate.id)
            .filter(
                H5WorkflowTemplate.owner_user_id == workflow_owner.id,
                H5WorkflowTemplate.status == "active",
                H5WorkflowTemplate.id.in_(workflow_requested),
            )
            .all()
        }
        workflow_requested = [item for item in workflow_requested if item in owned_workflow_ids]

    ip_requested = _clean_int_ids(body.ip_template_ids)
    if ip_requested:
        owned_ip_ids = {
            int(row.id)
            for row in db.query(IPContentScheduleTemplate.id)
            .filter(
                IPContentScheduleTemplate.user_id == current_user.id,
                IPContentScheduleTemplate.status == "active",
                IPContentScheduleTemplate.id.in_(ip_requested),
            )
            .all()
        }
        ip_requested = [item for item in ip_requested if item in owned_ip_ids]

    memory_requested = _clean_doc_ids(body.memory_doc_ids)
    if memory_requested:
        owned_doc_ids = {
            str(row.doc_id)
            for row in db.query(OpenClawMemoryDocument.doc_id)
            .filter(
                OpenClawMemoryDocument.target_user_id == current_user.id,
                OpenClawMemoryDocument.status == "active",
                OpenClawMemoryDocument.doc_id.in_(memory_requested),
            )
            .all()
        }
        memory_requested = [item for item in memory_requested if item in owned_doc_ids]

    workflow_ids = _sync_int_grants(
        db,
        model=H5WorkflowTemplateGrant,
        id_attr="template_id",
        owner_user_id=workflow_owner.id,
        target_user_id=target.id,
        requested_ids=workflow_requested,
    )
    ip_ids = _sync_int_grants(
        db,
        model=H5AgentTemplateGrant,
        id_attr="template_id",
        owner_user_id=current_user.id,
        target_user_id=target.id,
        requested_ids=ip_requested,
    )
    memory_ids = _sync_memory_grants(
        db,
        owner_user_id=current_user.id,
        target_user_id=target.id,
        requested_doc_ids=memory_requested,
    )
    db.commit()
    return {
        "ok": True,
        "user": _user_payload(target),
        "workflow_template_ids": workflow_ids,
        "ip_template_ids": ip_ids,
        "memory_doc_ids": memory_ids,
    }
