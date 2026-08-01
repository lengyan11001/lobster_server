from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import select

from ..db import SessionLocal
from ..models import H5ChatApproval, H5ChatEvent, H5ChatMessage, H5ChatSession

logger = logging.getLogger(__name__)

_FINAL_STATUSES = ("completed", "failed", "cancelled")
_TRANSIENT_EVENTS = ("queued", "claimed", "thinking", "delta", "tool_start", "tool_end", "progress")


def _env_days(name: str, default: int, *, minimum: int = 1, maximum: int = 730) -> int:
    try:
        return max(minimum, min(maximum, int(os.environ.get(name) or default)))
    except (TypeError, ValueError):
        return default


def _batch_size() -> int:
    try:
        return max(100, min(10000, int(os.environ.get("LOBSTER_H5_CHAT_RETENTION_BATCH_SIZE") or "2000")))
    except (TypeError, ValueError):
        return 2000


def cleanup_h5_chat_storage_sync(now: datetime | None = None) -> dict[str, int]:
    """Bound chat growth without deleting active conversation audit records."""
    now = now or datetime.utcnow()
    transient_cutoff = now - timedelta(days=_env_days("LOBSTER_H5_CHAT_TRANSIENT_EVENT_DAYS", 7))
    event_cutoff = now - timedelta(days=_env_days("LOBSTER_H5_CHAT_EVENT_DAYS", 90))
    archived_cutoff = now - timedelta(days=_env_days("LOBSTER_H5_CHAT_ARCHIVED_SESSION_DAYS", 90))
    empty_cutoff = now - timedelta(days=_env_days("LOBSTER_H5_CHAT_EMPTY_SESSION_DAYS", 30))
    batch = _batch_size()
    deleted = {"transient_events": 0, "old_events": 0, "messages": 0, "approvals": 0, "sessions": 0}
    db = SessionLocal()
    try:
        terminal_message_ids = select(H5ChatMessage.id).where(H5ChatMessage.status.in_(_FINAL_STATUSES))
        transient_ids = [
            row[0]
            for row in (
                db.query(H5ChatEvent.id)
                .filter(
                    H5ChatEvent.event_type.in_(_TRANSIENT_EVENTS),
                    H5ChatEvent.created_at < transient_cutoff,
                    H5ChatEvent.message_id.in_(terminal_message_ids),
                )
                .order_by(H5ChatEvent.id.asc())
                .limit(batch)
                .all()
            )
        ]
        if transient_ids:
            deleted["transient_events"] = db.query(H5ChatEvent).filter(H5ChatEvent.id.in_(transient_ids)).delete(
                synchronize_session=False
            )

        old_event_ids = [
            row[0]
            for row in (
                db.query(H5ChatEvent.id)
                .filter(
                    H5ChatEvent.created_at < event_cutoff,
                    H5ChatEvent.message_id.in_(terminal_message_ids),
                )
                .order_by(H5ChatEvent.id.asc())
                .limit(batch)
                .all()
            )
        ]
        if old_event_ids:
            deleted["old_events"] = db.query(H5ChatEvent).filter(H5ChatEvent.id.in_(old_event_ids)).delete(
                synchronize_session=False
            )

        archived_ids = [
            row[0]
            for row in (
                db.query(H5ChatSession.id)
                .filter(H5ChatSession.archived_at.isnot(None), H5ChatSession.archived_at < archived_cutoff)
                .order_by(H5ChatSession.archived_at.asc())
                .limit(max(10, batch // 20))
                .all()
            )
        ]
        empty_ids = [
            row[0]
            for row in (
                db.query(H5ChatSession.id)
                .filter(
                    H5ChatSession.archived_at.is_(None),
                    H5ChatSession.updated_at < empty_cutoff,
                    ~H5ChatSession.id.in_(select(H5ChatMessage.session_id).where(H5ChatMessage.session_id.isnot(None))),
                )
                .order_by(H5ChatSession.updated_at.asc())
                .limit(max(10, batch // 20))
                .all()
            )
        ]
        session_ids = list(dict.fromkeys(archived_ids + empty_ids))
        if session_ids:
            message_ids = [
                row[0]
                for row in db.query(H5ChatMessage.id).filter(H5ChatMessage.session_id.in_(session_ids)).all()
            ]
            if message_ids:
                db.query(H5ChatEvent).filter(H5ChatEvent.message_id.in_(message_ids)).delete(synchronize_session=False)
                deleted["approvals"] = db.query(H5ChatApproval).filter(
                    H5ChatApproval.session_id.in_(session_ids)
                ).delete(synchronize_session=False)
                deleted["messages"] = db.query(H5ChatMessage).filter(
                    H5ChatMessage.session_id.in_(session_ids)
                ).delete(synchronize_session=False)
            deleted["sessions"] = db.query(H5ChatSession).filter(H5ChatSession.id.in_(session_ids)).delete(
                synchronize_session=False
            )
        db.commit()
        return deleted
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def h5_chat_retention_background_loop() -> None:
    interval_hours = max(1, min(168, int(os.environ.get("LOBSTER_H5_CHAT_RETENTION_INTERVAL_HOURS") or "24")))
    while True:
        try:
            result = await asyncio.to_thread(cleanup_h5_chat_storage_sync)
            if any(result.values()):
                logger.info("[h5_chat_retention] cleanup=%s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[h5_chat_retention] cleanup failed")
        await asyncio.sleep(interval_hours * 60 * 60)
