from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Dict, Iterable, Set

from fastapi import HTTPException
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from ..models import (
    H5ChatDevicePresence,
    H5ChatEvent,
    H5ChatMessage,
    H5WorkflowActivation,
    H5WorkflowTemplate,
    H5MountedAccountDefault,
    DouyinDashboardDeviceState,
    OpenClawMemoryDocument,
    RecorderAudioRecord,
    InstallationSlotOwner,
    ScheduledTask,
    ScheduledTaskRun,
    User,
    UserInstallation,
)
from .brand_context import scoped_installation_id, user_brand_mark

_INSTALLATION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,128}$")
_ACTIVE_STATUSES = {"pending", "processing"}
_PENDING_SLOT_STATUSES = {"pending", "queued", "waiting", "claimed"}
_SLOT_TRANSFER_REASON = "该槽位已被后登录来源接管，先前来源任务已停止"


def _normalize_installation_id(value: str) -> str:
    installation_id = str(value or "").strip()
    return installation_id if _INSTALLATION_ID_RE.fullmatch(installation_id) else ""


def _owner_record_id(user_id: int, installation_id: str) -> str:
    """Store slot ownership by user+slot so packaged devices sharing an ID do not collide."""
    digest = hashlib.sha1(installation_id.encode("utf-8")).hexdigest()
    return f"u{int(user_id)}-{digest}"


def _lock_installation_slot(db: Session, installation_id: str) -> None:
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:installation_id))"),
            {"installation_id": installation_id},
        )


def _slot_ids_for_user(db: Session, user_id: int, raw_installation_id: str) -> Set[str]:
    ids = {raw_installation_id}
    user = db.query(User).filter(User.id == user_id).first()
    if user is not None:
        scoped = scoped_installation_id(raw_installation_id, user_brand_mark(user))
        if scoped:
            ids.add(scoped)
    historical = (
        db.query(UserInstallation.installation_id)
        .filter(UserInstallation.user_id == user_id)
        .all()
    )
    suffix = f"--{raw_installation_id}"
    for (installation_id,) in historical:
        value = str(installation_id or "").strip()
        if value == raw_installation_id or value.endswith(suffix):
            ids.add(value)
    return ids


def _task_targets_slot(task: ScheduledTask, slot_ids: Set[str]) -> bool:
    targets = task.target_installation_ids
    if not isinstance(targets, list):
        return False
    return bool(slot_ids.intersection(str(item or "").strip() for item in targets))


def migrate_installation_slot_references(
    db: Session,
    *,
    user_id: int,
    previous_installation_id: str,
    installation_id: str,
    now: datetime | None = None,
) -> Dict[str, int]:
    """Move pending workflow references when a client receives a new slot id."""
    old = _normalize_installation_id(previous_installation_id)
    new = _normalize_installation_id(installation_id)
    if not old or not new or old == new:
        return {}
    now = now or datetime.utcnow()
    stats: Dict[str, int] = {
        "tasks": 0,
        "runs": 0,
        "messages": 0,
        "activations": 0,
        "templates": 0,
        "mounted_defaults": 0,
        "douyin_states": 0,
        "memory_docs": 0,
        "recorder_rows": 0,
        "presence": 0,
    }
    user = db.query(User).filter(User.id == user_id).first()
    old_ids = {old}
    if user is not None:
        scoped = scoped_installation_id(old, user_brand_mark(user))
        if scoped:
            old_ids.add(scoped)

    for task in db.query(ScheduledTask).filter(ScheduledTask.user_id == user_id).all():
        targets = task.target_installation_ids
        if not isinstance(targets, list):
            continue
        changed = False
        replaced: list[str] = []
        for item in targets:
            value = str(item or "").strip()
            if value in old_ids:
                value = new
                changed = True
            if value and value not in replaced:
                replaced.append(value[:128])
        if changed:
            task.target_installation_ids = replaced
            task.updated_at = now
            stats["tasks"] += 1

    runs = (
        db.query(ScheduledTaskRun)
        .filter(
            ScheduledTaskRun.user_id == user_id,
            ScheduledTaskRun.status.in_(tuple(_PENDING_SLOT_STATUSES)),
        )
        .all()
    )
    for run in runs:
        changed = False
        if str(run.installation_id or "").strip() in old_ids:
            run.installation_id = new
            changed = True
        if str(run.claimed_by_installation_id or "").strip() in old_ids:
            run.claimed_by_installation_id = None
            changed = True
        if not changed:
            continue
        run.updated_at = now
        stats["runs"] += 1
        if run.h5_message_id:
            message = db.query(H5ChatMessage).filter(H5ChatMessage.id == run.h5_message_id).first()
            if message is not None:
                if str(message.installation_id or "").strip() in old_ids:
                    message.installation_id = new
                if str(message.claimed_by_installation_id or "").strip() in old_ids:
                    message.claimed_by_installation_id = None
                message.updated_at = now
                stats["messages"] += 1

    for model, field, stat_key, user_field in (
        (H5WorkflowActivation, "installation_id", "activations", "user_id"),
        (H5WorkflowTemplate, "installation_id", "templates", "owner_user_id"),
        (H5MountedAccountDefault, "installation_id", "mounted_defaults", "user_id"),
        (DouyinDashboardDeviceState, "installation_id", "douyin_states", "user_id"),
        (OpenClawMemoryDocument, "installation_id", "memory_docs", "target_user_id"),
        (RecorderAudioRecord, "installation_id", "recorder_rows", "user_id"),
    ):
        for row in db.query(model).filter(getattr(model, user_field) == user_id).all():
            if str(getattr(row, field, "") or "").strip() not in old_ids:
                continue
            setattr(row, field, new)
            if hasattr(row, "updated_at"):
                row.updated_at = now
            stats[stat_key] += 1

    stats["presence"] = int(
        db.query(H5ChatDevicePresence)
        .filter(
            H5ChatDevicePresence.user_id == user_id,
            H5ChatDevicePresence.installation_id.in_(tuple(old_ids)),
        )
        .delete(synchronize_session=False)
        or 0
    )
    return stats


def _add_cancel_event(
    db: Session,
    *,
    message_id: str,
    user_id: int,
    previous_user_id: int,
    new_user_id: int,
    installation_id: str,
) -> None:
    db.add(
        H5ChatEvent(
            message_id=message_id,
            user_id=user_id,
            event_type="cancelled",
            payload={
                "reason": "slot_owner_changed",
                "detail": _SLOT_TRANSFER_REASON,
                "installation_id": installation_id,
                "previous_user_id": previous_user_id,
                "new_user_id": new_user_id,
            },
            created_at=datetime.utcnow(),
        )
    )


def _cancel_message(
    db: Session,
    message: H5ChatMessage,
    *,
    previous_user_id: int,
    new_user_id: int,
    installation_id: str,
    now: datetime,
) -> bool:
    if message.status not in _ACTIVE_STATUSES:
        return False
    message.status = "cancelled"
    message.error = _SLOT_TRANSFER_REASON
    message.finished_at = now
    message.updated_at = now
    _add_cancel_event(
        db,
        message_id=message.id,
        user_id=message.user_id,
        previous_user_id=previous_user_id,
        new_user_id=new_user_id,
        installation_id=installation_id,
    )
    return True


def _finish_cancelled_parents(db: Session, parent_ids: Iterable[str], now: datetime) -> int:
    finished = 0
    for parent_id in set(str(item or "").strip() for item in parent_ids if item):
        parent = db.query(H5ChatMessage).filter(H5ChatMessage.id == parent_id).first()
        if parent is None or parent.status not in _ACTIVE_STATUSES:
            continue
        unfinished = (
            db.query(H5ChatMessage.id)
            .filter(
                H5ChatMessage.parent_message_id == parent_id,
                H5ChatMessage.status.in_(tuple(_ACTIVE_STATUSES)),
            )
            .first()
        )
        if unfinished is not None:
            continue
        parent.status = "cancelled"
        parent.error = _SLOT_TRANSFER_REASON
        parent.finished_at = now
        parent.updated_at = now
        db.add(
            H5ChatEvent(
                message_id=parent.id,
                user_id=parent.user_id,
                event_type="cancelled",
                payload={"reason": "slot_owner_changed", "detail": _SLOT_TRANSFER_REASON},
                created_at=now,
            )
        )
        finished += 1
    return finished


def _stop_user_work_for_slot(
    db: Session,
    *,
    previous_user_id: int,
    new_user_id: int,
    raw_installation_id: str,
    now: datetime,
) -> Dict[str, int]:
    slot_ids = _slot_ids_for_user(db, previous_user_id, raw_installation_id)
    stats = {
        "activations_stopped": 0,
        "tasks_paused": 0,
        "runs_cancelled": 0,
        "messages_cancelled": 0,
        "parents_cancelled": 0,
        "presence_removed": 0,
    }
    task_ids: Set[int] = set()
    parent_ids: Set[str] = set()

    activations = (
        db.query(H5WorkflowActivation)
        .filter(
            H5WorkflowActivation.user_id == previous_user_id,
            H5WorkflowActivation.status == "active",
            H5WorkflowActivation.installation_id.in_(tuple(slot_ids)),
        )
        .all()
    )
    for activation in activations:
        activation.status = "stopped"
        activation.stopped_at = now
        activation.updated_at = now
        for task_id in activation.scheduled_task_ids or []:
            try:
                task_ids.add(int(task_id))
            except (TypeError, ValueError):
                continue
        stats["activations_stopped"] += 1

    active_tasks = (
        db.query(ScheduledTask)
        .filter(ScheduledTask.user_id == previous_user_id, ScheduledTask.status == "active")
        .all()
    )
    for task in active_tasks:
        if task.id not in task_ids and not _task_targets_slot(task, slot_ids):
            continue
        task_ids.add(task.id)
        task.status = "paused"
        task.last_error = _SLOT_TRANSFER_REASON
        task.updated_at = now
        stats["tasks_paused"] += 1

    active_runs = (
        db.query(ScheduledTaskRun)
        .filter(
            ScheduledTaskRun.user_id == previous_user_id,
            ScheduledTaskRun.status.in_(tuple(_ACTIVE_STATUSES)),
        )
        .all()
    )
    for run in active_runs:
        belongs_to_slot = (
            (run.task_id is not None and run.task_id in task_ids)
            or str(run.installation_id or "").strip() in slot_ids
            or str(run.claimed_by_installation_id or "").strip() in slot_ids
        )
        if not belongs_to_slot:
            continue
        run.status = "cancelled"
        run.error = _SLOT_TRANSFER_REASON
        run.finished_at = now
        run.updated_at = now
        stats["runs_cancelled"] += 1
        if run.h5_message_id:
            message = db.query(H5ChatMessage).filter(H5ChatMessage.id == run.h5_message_id).first()
            if message is not None and _cancel_message(
                db,
                message,
                previous_user_id=previous_user_id,
                new_user_id=new_user_id,
                installation_id=raw_installation_id,
                now=now,
            ):
                stats["messages_cancelled"] += 1
                if message.parent_message_id:
                    parent_ids.add(message.parent_message_id)

    direct_messages = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.user_id == previous_user_id,
            H5ChatMessage.status.in_(tuple(_ACTIVE_STATUSES)),
            H5ChatMessage.mode.in_(("direct", "client_command")),
        )
        .all()
    )
    for message in direct_messages:
        belongs_to_slot = (
            str(message.installation_id or "").strip() in slot_ids
            or str(message.claimed_by_installation_id or "").strip() in slot_ids
        )
        if not belongs_to_slot:
            continue
        if _cancel_message(
            db,
            message,
            previous_user_id=previous_user_id,
            new_user_id=new_user_id,
            installation_id=raw_installation_id,
            now=now,
        ):
            stats["messages_cancelled"] += 1
            if message.parent_message_id:
                parent_ids.add(message.parent_message_id)

    stats["parents_cancelled"] = _finish_cancelled_parents(db, parent_ids, now)
    stats["presence_removed"] = (
        db.query(H5ChatDevicePresence)
        .filter(
            H5ChatDevicePresence.user_id == previous_user_id,
            H5ChatDevicePresence.installation_id == raw_installation_id,
        )
        .delete(synchronize_session=False)
    )
    return stats


def _legacy_conflicting_user_ids(db: Session, user_id: int, installation_id: str) -> Set[int]:
    user_ids = {
        int(row[0])
        for row in (
            db.query(H5ChatDevicePresence.user_id)
            .filter(
                H5ChatDevicePresence.installation_id == installation_id,
                H5ChatDevicePresence.user_id != user_id,
            )
            .distinct()
            .all()
        )
    }
    suffix = f"%--{installation_id}"
    for (conflicting_user_id,) in (
        db.query(H5WorkflowActivation.user_id)
        .filter(
            H5WorkflowActivation.user_id != user_id,
            H5WorkflowActivation.status == "active",
            or_(
                H5WorkflowActivation.installation_id == installation_id,
                H5WorkflowActivation.installation_id.like(suffix),
            ),
        )
        .distinct()
        .all()
    ):
        user_ids.add(int(conflicting_user_id))
    for (conflicting_user_id,) in (
        db.query(ScheduledTaskRun.user_id)
        .filter(
            ScheduledTaskRun.user_id != user_id,
            ScheduledTaskRun.status.in_(tuple(_ACTIVE_STATUSES)),
            or_(
                ScheduledTaskRun.installation_id == installation_id,
                ScheduledTaskRun.claimed_by_installation_id == installation_id,
                ScheduledTaskRun.installation_id.like(suffix),
                ScheduledTaskRun.claimed_by_installation_id.like(suffix),
            ),
        )
        .distinct()
        .all()
    ):
        user_ids.add(int(conflicting_user_id))
    return user_ids


def claim_installation_slot(
    db: Session,
    *,
    user_id: int,
    installation_id: str,
    brand_mark: str = "bihuo",
    auth_session_id: str = "",
) -> Dict[str, Any]:
    raw_installation_id = _normalize_installation_id(installation_id)
    if not raw_installation_id:
        return {"ok": False, "claimed": False, "reason": "invalid_installation_id"}

    now = datetime.utcnow()
    session_id = str(auth_session_id or "").strip()[:128]
    owner_record_id = _owner_record_id(user_id, raw_installation_id)
    _lock_installation_slot(db, owner_record_id)
    owner = (
        db.query(InstallationSlotOwner)
        .filter(InstallationSlotOwner.installation_id == owner_record_id)
        .with_for_update()
        .first()
    )
    previous_user_ids: Set[int] = set()
    if owner is None:
        owner = InstallationSlotOwner(
            installation_id=owner_record_id,
            user_id=user_id,
            brand_mark=str(brand_mark or "bihuo")[:64],
            auth_session_id=session_id or None,
            lease_version=1,
            claimed_at=now,
            updated_at=now,
        )
        db.add(owner)
    elif owner.user_id != user_id:
        previous_user_ids.add(int(owner.user_id))
        previous_user_ids.update(_legacy_conflicting_user_ids(db, user_id, raw_installation_id))
        owner.user_id = user_id
        owner.brand_mark = str(brand_mark or "bihuo")[:64]
        owner.auth_session_id = session_id or None
        owner.lease_version = int(owner.lease_version or 0) + 1
        owner.claimed_at = now
        owner.updated_at = now
    else:
        owner.brand_mark = str(brand_mark or owner.brand_mark or "bihuo")[:64]
        if session_id:
            owner.auth_session_id = session_id
        owner.updated_at = now

    totals: Dict[str, int] = {}
    for previous_user_id in sorted(previous_user_ids):
        stopped = _stop_user_work_for_slot(
            db,
            previous_user_id=previous_user_id,
            new_user_id=user_id,
            raw_installation_id=raw_installation_id,
            now=now,
        )
        for key, value in stopped.items():
            totals[key] = totals.get(key, 0) + int(value)
    db.commit()
    return {
        "ok": True,
        "claimed": True,
        "transferred": bool(previous_user_ids),
        "installation_id": raw_installation_id,
        "user_id": user_id,
        "previous_user_ids": sorted(previous_user_ids),
        "stopped": totals,
    }


def installation_slot_owned_by(db: Session, *, user_id: int, installation_id: str) -> bool:
    raw_installation_id = _normalize_installation_id(installation_id)
    if not raw_installation_id:
        return False
    owner = (
        db.query(InstallationSlotOwner)
        .filter(InstallationSlotOwner.installation_id == _owner_record_id(user_id, raw_installation_id))
        .first()
    )
    return owner is not None and int(owner.user_id) == int(user_id)


def assert_installation_slot_owner(
    db: Session,
    *,
    user_id: int,
    installation_id: str,
    claim_if_unowned: bool = False,
    brand_mark: str = "bihuo",
    auth_session_id: str = "",
) -> None:
    raw_installation_id = _normalize_installation_id(installation_id)
    if not raw_installation_id:
        raise HTTPException(status_code=400, detail="缺少或无效的 X-Installation-Id")
    owner = (
        db.query(InstallationSlotOwner)
        .filter(InstallationSlotOwner.installation_id == _owner_record_id(user_id, raw_installation_id))
        .first()
    )
    if owner is None and claim_if_unowned:
        claim_installation_slot(
            db,
            user_id=user_id,
            installation_id=raw_installation_id,
            brand_mark=brand_mark,
            auth_session_id=auth_session_id,
        )
        return
    if owner is None or int(owner.user_id) != int(user_id):
        raise HTTPException(
            status_code=409,
            detail="该槽位已被后登录来源接管，当前登录不能继续下发任务",
        )
