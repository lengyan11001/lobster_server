from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..models import H5ChatMessage, H5ChatSession

SYSTEM_TASK_SESSION_PREFIX = "system_tasks_"
SYSTEM_TASK_SESSION_TITLE = "系统任务"
SYSTEM_TASK_MESSAGE_MODES = frozenset({"scheduled_task", "client_command"})


def system_task_session_id(user_id: int) -> str:
    return f"{SYSTEM_TASK_SESSION_PREFIX}{int(user_id)}"


def is_system_task_session_id(session_id: str, user_id: int | None = None) -> bool:
    value = str(session_id or "").strip()
    if user_id is not None:
        return value == system_task_session_id(user_id)
    return value.startswith(SYSTEM_TASK_SESSION_PREFIX)


def is_system_task_message_mode(mode: str) -> bool:
    return str(mode or "").strip().lower() in SYSTEM_TASK_MESSAGE_MODES


def ensure_system_task_session(
    db: Session,
    user_id: int,
    *,
    now: datetime | None = None,
    backfill: bool = True,
) -> H5ChatSession:
    now = now or datetime.utcnow()
    session_id = system_task_session_id(user_id)
    row = (
        db.query(H5ChatSession)
        .filter(H5ChatSession.id == session_id, H5ChatSession.user_id == user_id)
        .first()
    )
    if row is None:
        row = H5ChatSession(
            id=session_id,
            user_id=user_id,
            title=SYSTEM_TASK_SESSION_TITLE,
            permission_mode="confirm",
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
    else:
        row.title = SYSTEM_TASK_SESSION_TITLE
        row.archived_at = None

    if backfill:
        db.query(H5ChatMessage).filter(
            H5ChatMessage.user_id == user_id,
            H5ChatMessage.mode.in_(SYSTEM_TASK_MESSAGE_MODES),
            or_(H5ChatMessage.session_id.is_(None), H5ChatMessage.session_id != session_id),
        ).update({H5ChatMessage.session_id: session_id}, synchronize_session="fetch")

    latest = (
        db.query(func.max(H5ChatMessage.created_at))
        .filter(
            H5ChatMessage.user_id == user_id,
            H5ChatMessage.session_id == session_id,
            H5ChatMessage.parent_message_id.is_(None),
        )
        .scalar()
    )
    if latest and (row.last_message_at is None or latest > row.last_message_at):
        row.last_message_at = latest
    if row.updated_at is None or (row.last_message_at and row.last_message_at > row.updated_at):
        row.updated_at = row.last_message_at or now
    return row


def backfill_system_task_session(db: Session, user_id: int) -> H5ChatSession | None:
    exists = (
        db.query(H5ChatMessage.id)
        .filter(H5ChatMessage.user_id == user_id, H5ChatMessage.mode.in_(SYSTEM_TASK_MESSAGE_MODES))
        .first()
    )
    if not exists:
        return None
    return ensure_system_task_session(db, user_id)


def attach_system_task_message(
    db: Session,
    message: H5ChatMessage,
    *,
    now: datetime | None = None,
) -> H5ChatSession:
    now = now or message.created_at or datetime.utcnow()
    session = ensure_system_task_session(db, message.user_id, now=now, backfill=False)
    message.session_id = session.id
    session.last_message_at = now
    session.updated_at = now
    return session
