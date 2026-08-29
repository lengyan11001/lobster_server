"""Background runner for server-side scheduled tasks."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import update

from ..api.scheduled_tasks import (
    _SERVER_SIDE_TASK_KINDS,
    _enqueue_task,
    _advance_task_after_expired_occurrence,
    _expire_workflow_node_runs,
    _fail_stale_server_side_runs,
    _recover_interrupted_server_side_runs,
    _scheduled_occurrence_expired,
)
from ..db import SessionLocal
from ..models import ScheduledTask

logger = logging.getLogger(__name__)


def _recover_once_sync() -> int:
    db = SessionLocal()
    try:
        return _recover_interrupted_server_side_runs(db, datetime.utcnow())
    finally:
        db.close()


def _tick_once_sync() -> int:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        expired_count = _expire_workflow_node_runs(db, now)
        if expired_count:
            logger.warning("[server-side-schedule] expired workflow node runs=%s", expired_count)
        stale_count = _fail_stale_server_side_runs(db, now)
        if stale_count:
            logger.warning("[server-side-schedule] marked stale processing runs failed=%s", stale_count)
        rows = (
            db.query(ScheduledTask)
            .filter(
                ScheduledTask.task_kind.in_(list(_SERVER_SIDE_TASK_KINDS)),
                ScheduledTask.status == "active",
                ScheduledTask.next_run_at.isnot(None),
                ScheduledTask.next_run_at <= now,
            )
            .order_by(ScheduledTask.next_run_at.asc())
            .limit(50)
            .all()
        )
        count = 0
        expired_count = 0
        for candidate in rows:
            scheduled_at = candidate.next_run_at
            if scheduled_at is None:
                continue
            if _scheduled_occurrence_expired(candidate, scheduled_at, now):
                _advance_task_after_expired_occurrence(candidate, scheduled_at, now)
                expired_count += 1
                continue
            result = db.execute(
                update(ScheduledTask)
                .where(
                    ScheduledTask.id == candidate.id,
                    ScheduledTask.status == "active",
                    ScheduledTask.next_run_at.isnot(None),
                    ScheduledTask.next_run_at <= now,
                )
                .values(next_run_at=None, updated_at=now)
            )
            if int(result.rowcount or 0) != 1:
                continue
            task = db.query(ScheduledTask).filter(ScheduledTask.id == candidate.id).first()
            if not task:
                continue
            _enqueue_task(db, task, now, scheduled_at=scheduled_at)
            count += 1
            # One server-side task per tick keeps scheduling behavior
            # consistent with Online installations and avoids building a
            # burst of work after a delayed scheduler wake-up.
            break
        if count or expired_count:
            db.commit()
        return count
    finally:
        db.close()


async def ip_content_schedule_background_loop() -> None:
    await asyncio.sleep(20)
    try:
        recovered = await asyncio.to_thread(_recover_once_sync)
        if recovered:
            logger.warning("[server-side-schedule] resumed interrupted runs=%s", recovered)
    except Exception:
        logger.exception("[server-side-schedule] startup recovery error")
    while True:
        try:
            count = await asyncio.to_thread(_tick_once_sync)
            if count:
                logger.info("[server-side-schedule] executed due tasks=%s", count)
        except Exception:
            logger.exception("[server-side-schedule] tick error")
        await asyncio.sleep(60)
