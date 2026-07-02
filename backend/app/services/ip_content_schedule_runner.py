"""Background runner for server-side scheduled tasks."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import update

from ..api.scheduled_tasks import _enqueue_task, _fail_stale_server_side_runs
from ..db import SessionLocal
from ..models import ScheduledTask

logger = logging.getLogger(__name__)


def _tick_once_sync() -> int:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        stale_count = _fail_stale_server_side_runs(db, now)
        if stale_count:
            logger.warning("[server-side-schedule] marked stale processing runs failed=%s", stale_count)
        rows = (
            db.query(ScheduledTask)
            .filter(
                ScheduledTask.task_kind.in_(["ip_content_daily", "lead_collection_templates"]),
                ScheduledTask.status == "active",
                ScheduledTask.next_run_at.isnot(None),
                ScheduledTask.next_run_at <= now,
            )
            .order_by(ScheduledTask.next_run_at.asc())
            .limit(10)
            .all()
        )
        count = 0
        for candidate in rows:
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
            _enqueue_task(db, task, now)
            count += 1
        if count:
            db.commit()
        return count
    finally:
        db.close()


async def ip_content_schedule_background_loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            count = await asyncio.to_thread(_tick_once_sync)
            if count:
                logger.info("[server-side-schedule] executed due tasks=%s", count)
        except Exception:
            logger.exception("[server-side-schedule] tick error")
        await asyncio.sleep(60)
