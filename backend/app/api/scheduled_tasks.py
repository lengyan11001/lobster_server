from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    H5ChatDevicePresence,
    H5ChatEvent,
    H5ChatMessage,
    ScheduledTask,
    ScheduledTaskRun,
    User,
)
from .admin import AdminContext, _agent_sub_user_ids, _verify_admin_token
from .auth import get_current_user
from .installation_slots import ensure_installation_slot

router = APIRouter()

_TASK_KINDS = {"openclaw_message", "chat_message", "capability"}
_SCHEDULE_TYPES = {"once", "interval"}
_FINAL_STATUSES = {"completed", "failed", "cancelled"}
_MAX_TARGET_DEVICES = 20


class ScheduledTaskCreate(BaseModel):
    user_id: Optional[int] = None
    title: str = Field("", max_length=160)
    task_kind: str = "openclaw_message"
    content: str = Field("", max_length=12000)
    payload: Dict[str, Any] = Field(default_factory=dict)
    schedule_type: str = "once"
    interval_seconds: Optional[int] = None
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


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _header_installation_id(request: Request) -> str:
    return (
        request.headers.get("X-Installation-Id")
        or request.headers.get("x-installation-id")
        or ""
    ).strip()


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


def _normalize_schedule_type(value: str) -> str:
    schedule_type = (value or "once").strip().lower()
    if schedule_type not in _SCHEDULE_TYPES:
        raise HTTPException(status_code=400, detail="不支持的调度类型")
    return schedule_type


def _task_title(body: ScheduledTaskCreate, task_kind: str) -> str:
    title = (body.title or "").strip()
    if title:
        return title[:160]
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


def _create_run_for_target(db: Session, task: ScheduledTask, installation_id: Optional[str], now: datetime) -> ScheduledTaskRun:
    run_id = uuid.uuid4().hex
    message_id = f"task_{run_id}"[:64]
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
    db.add(run)
    db.add(h5)
    _add_h5_event(db, message_id, task.user_id, "queued", {"task_id": task.id, "run_id": run_id, "title": task.title})
    task.run_count = int(task.run_count or 0) + 1
    task.last_run_at = now
    task.last_run_id = run_id
    task.updated_at = now
    return run


def _enqueue_task(db: Session, task: ScheduledTask, now: Optional[datetime] = None) -> List[ScheduledTaskRun]:
    now = now or datetime.utcnow()
    targets = _clean_installation_ids(task.target_installation_ids or [])
    if not targets:
        targets = [""]
    runs = [_create_run_for_target(db, task, target or None, now) for target in targets]
    if task.schedule_type == "once":
        task.status = "completed"
        task.next_run_at = None
    else:
        interval = max(60, int(task.interval_seconds or 3600))
        task.next_run_at = now + timedelta(seconds=interval)
    return runs


def _enqueue_due_tasks(db: Session, user_id: Optional[int] = None) -> int:
    now = datetime.utcnow()
    q = (
        db.query(ScheduledTask)
        .filter(
            ScheduledTask.status == "active",
            ScheduledTask.schedule_type == "interval",
            ScheduledTask.next_run_at.isnot(None),
            ScheduledTask.next_run_at <= now,
        )
        .order_by(ScheduledTask.next_run_at.asc())
        .limit(50)
    )
    if user_id is not None:
        q = q.filter(ScheduledTask.user_id == user_id)
    count = 0
    for task in q.all():
        _enqueue_task(db, task, now)
        count += 1
    if count:
        db.commit()
    return count


def _assert_user_task_access(row_user_id: int, current_user: User) -> None:
    if int(row_user_id) != int(current_user.id):
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
    interval_seconds = None
    now = datetime.utcnow()
    next_run_at = now
    if schedule_type == "interval":
        interval_seconds = max(60, min(int(body.interval_seconds or 3600), 366 * 24 * 3600))
        next_run_at = now
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
    _enqueue_task(db, task, now)
    db.commit()
    db.refresh(task)
    return task


@router.post("/api/scheduled-tasks/tasks", summary="创建定时/一次性任务")
def create_scheduled_task(
    body: ScheduledTaskCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = _create_task_row(
        db,
        body,
        target_user_id=current_user.id,
        created_by_user_id=current_user.id,
        created_by_role="user",
    )
    return {"ok": True, "task": _serialize_task(task)}


@router.get("/api/scheduled-tasks/tasks", summary="任务定义列表")
def list_scheduled_tasks(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ScheduledTask)
        .filter(ScheduledTask.user_id == current_user.id)
        .order_by(ScheduledTask.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"ok": True, "tasks": [_serialize_task(r) for r in rows]}


@router.patch("/api/scheduled-tasks/tasks/{task_id}", summary="更新任务状态")
def patch_scheduled_task(
    task_id: int,
    body: ScheduledTaskPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _assert_user_task_access(task.user_id, current_user)
    if body.status:
        status = body.status.strip().lower()
        if status not in {"active", "paused", "cancelled"}:
            raise HTTPException(status_code=400, detail="不支持的状态")
        task.status = status
        task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    return {"ok": True, "task": _serialize_task(task)}


@router.post("/api/scheduled-tasks/tasks/{task_id}/run-now", summary="立即执行任务")
def run_scheduled_task_now(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _assert_user_task_access(task.user_id, current_user)
    runs = _enqueue_task(db, task, datetime.utcnow())
    if task.schedule_type == "interval" and task.status != "cancelled":
        task.status = "active"
    db.commit()
    return {"ok": True, "runs": [_serialize_run(r) for r in runs]}


@router.get("/api/scheduled-tasks/runs", summary="执行记录列表")
def list_scheduled_task_runs(
    limit: int = Query(80, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _enqueue_due_tasks(db, current_user.id)
    rows = (
        db.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.user_id == current_user.id)
        .order_by(ScheduledTaskRun.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"ok": True, "runs": [_serialize_run(r) for r in rows]}


@router.get("/api/scheduled-tasks/pending", summary="本地 online 领取待执行任务")
def pending_scheduled_task_runs(
    request: Request,
    limit: int = Query(2, ge=1, le=10),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    xi = _header_installation_id(request)
    if xi:
        ensure_installation_slot(db, current_user.id, xi)
    _enqueue_due_tasks(db, current_user.id)

    now = datetime.utcnow()
    stale_cutoff = now - timedelta(minutes=10)
    stale_rows = (
        db.query(ScheduledTaskRun)
        .filter(
            ScheduledTaskRun.user_id == current_user.id,
            ScheduledTaskRun.status == "processing",
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

    rows = (
        db.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.user_id == current_user.id, ScheduledTaskRun.status == "pending")
        .filter(or_(ScheduledTaskRun.installation_id.is_(None), ScheduledTaskRun.installation_id == xi))
        .order_by(ScheduledTaskRun.created_at.asc())
        .limit(limit)
        .all()
    )
    for row in rows:
        row.status = "processing"
        row.claimed_by_installation_id = xi or "unknown"
        row.claimed_at = now
        row.started_at = now
        row.updated_at = now
        if row.h5_message_id:
            msg = db.query(H5ChatMessage).filter(H5ChatMessage.id == row.h5_message_id).first()
            if msg:
                msg.status = "processing"
                msg.claimed_by_installation_id = xi or "unknown"
                msg.claimed_at = now
                msg.updated_at = now
        _add_h5_event(db, row.h5_message_id, row.user_id, "claimed", {"installation_id": xi or ""})
    db.commit()
    return {"ok": True, "items": [_serialize_run(r) for r in rows]}


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
    row.progress = body.payload or {}
    row.updated_at = datetime.utcnow()
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
    error = (body.error or "").strip()
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
