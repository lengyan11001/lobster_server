from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import or_

from ..api.scheduled_tasks import (
    _cleanup_recurring_pending_backlog,
    _expire_workflow_node_runs,
    _fail_abandoned_client_runs,
)
from ..db import SessionLocal
from ..models import CreativeGenerationJob, H5ChatApproval, H5ChatEvent, H5ChatMessage

logger = logging.getLogger(__name__)

_FINAL_MESSAGE_STATUSES = {"completed", "failed", "cancelled"}
_ACTIVE_APPROVAL_STATUSES = {"pending", "approved", "executing"}
_ACTIVE_CREATIVE_STATUSES = {"pending", "processing", "running"}


def fail_client_runs_on_startup_sync(now: datetime | None = None) -> int:
    """Expire workflow nodes and fail client work that was already abandoned before startup."""
    now = now or datetime.utcnow()
    db = SessionLocal()
    try:
        expired = _expire_workflow_node_runs(db, now)
        failed = _fail_abandoned_client_runs(db, now)
        db.commit()
        return expired + failed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _env_hours(name: str, default: int, *, minimum: int = 1, maximum: int = 24 * 30) -> int:
    try:
        return max(minimum, min(maximum, int(os.environ.get(name) or default)))
    except (TypeError, ValueError):
        return default


def cleanup_runtime_state_sync(now: datetime | None = None) -> dict[str, int]:
    """Repair terminal-state drift and bound durable queues without deleting audit rows."""
    now = now or datetime.utcnow()
    result = {
        "recurring_runs_cancelled": 0,
        "workflow_node_runs_expired": 0,
        "abandoned_runs_failed": 0,
        "chat_messages_expired": 0,
        "approvals_repaired": 0,
        "creative_jobs_expired": 0,
    }
    db = SessionLocal()
    try:
        result["recurring_runs_cancelled"] = _cleanup_recurring_pending_backlog(db, now)
        result["workflow_node_runs_expired"] = _expire_workflow_node_runs(db, now)
        result["abandoned_runs_failed"] = _fail_abandoned_client_runs(db, now)

        terminal_approvals = (
            db.query(H5ChatApproval, H5ChatMessage.status)
            .join(H5ChatMessage, H5ChatMessage.id == H5ChatApproval.message_id)
            .filter(
                H5ChatApproval.status.in_(_ACTIVE_APPROVAL_STATUSES),
                H5ChatMessage.status.in_(_FINAL_MESSAGE_STATUSES),
            )
            .with_for_update(of=H5ChatApproval, skip_locked=True)
            .limit(500)
            .all()
        )
        for approval, message_status in terminal_approvals:
            approval.status = "completed" if message_status == "completed" else message_status
            approval.finished_at = approval.finished_at or now
            approval.updated_at = now
        result["approvals_repaired"] = len(terminal_approvals)

        chat_cutoff = now - timedelta(hours=_env_hours("LOBSTER_PENDING_CHAT_EXPIRY_HOURS", 72))
        stale_messages = (
            db.query(H5ChatMessage)
            .filter(
                H5ChatMessage.parent_message_id.is_(None),
                H5ChatMessage.mode.in_(("direct", "client_command")),
                H5ChatMessage.status == "pending",
                H5ChatMessage.created_at < chat_cutoff,
            )
            .order_by(H5ChatMessage.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(500)
            .all()
        )
        for message in stale_messages:
            text = "任务等待设备执行时间过长，已自动过期；如仍需要，请重新发起。"
            message.status = "cancelled"
            message.error = text
            message.finished_at = now
            message.updated_at = now
            db.add(
                H5ChatEvent(
                    message_id=message.id,
                    user_id=message.user_id,
                    event_type="cancelled",
                    payload={"reason": "pending_chat_expired", "text": text},
                    created_at=now,
                )
            )
        result["chat_messages_expired"] = len(stale_messages)

        no_provider_cutoff = now - timedelta(hours=_env_hours("LOBSTER_CREATIVE_JOB_NO_PROVIDER_EXPIRY_HOURS", 24))
        provider_cutoff = now - timedelta(hours=_env_hours("LOBSTER_CREATIVE_JOB_PROVIDER_EXPIRY_HOURS", 24 * 7))
        stale_jobs = (
            db.query(CreativeGenerationJob)
            .filter(
                CreativeGenerationJob.deleted_at.is_(None),
                CreativeGenerationJob.status.in_(_ACTIVE_CREATIVE_STATUSES),
                or_(
                    (
                        CreativeGenerationJob.provider_task_id.is_(None)
                        & (CreativeGenerationJob.updated_at < no_provider_cutoff)
                    ),
                    CreativeGenerationJob.updated_at < provider_cutoff,
                ),
            )
            .order_by(CreativeGenerationJob.updated_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1000)
            .all()
        )
        for job in stale_jobs:
            text = "生成任务长时间没有进度，已自动标记为过期。"
            job.status = "failed"
            job.stage = "expired"
            job.error = job.error or text
            job.completed_at = now
            job.updated_at = now
            meta = dict(job.meta or {})
            meta.update({"expired_at": now.isoformat(), "expiry_reason": "stale_generation_state"})
            job.meta = meta
        result["creative_jobs_expired"] = len(stale_jobs)

        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def runtime_state_maintenance_loop() -> None:
    interval_seconds = max(
        60,
        min(6 * 60 * 60, int(os.environ.get("LOBSTER_RUNTIME_STATE_MAINTENANCE_SECONDS") or "900")),
    )
    while True:
        try:
            result = await asyncio.to_thread(cleanup_runtime_state_sync)
            if any(result.values()):
                logger.warning("[runtime-state] repaired=%s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[runtime-state] maintenance failed")
        await asyncio.sleep(interval_seconds)
