"""One-time compatibility migration for retired OpenClaw scheduled tasks."""

import logging

from sqlalchemy import text

from ..db import engine

logger = logging.getLogger(__name__)


def migrate_openclaw_task_kinds() -> None:
    """Rename legacy task rows so they run through the normal chat executor."""
    try:
        with engine.begin() as connection:
            task_result = connection.execute(
                text("UPDATE scheduled_tasks SET task_kind = 'chat_message' WHERE task_kind = 'openclaw_message'")
            )
            run_result = connection.execute(
                text("UPDATE scheduled_task_runs SET task_kind = 'chat_message' WHERE task_kind = 'openclaw_message'")
            )
        changed = int(task_result.rowcount or 0) + int(run_result.rowcount or 0)
        if changed:
            logger.info("[startup] migrated %s legacy OpenClaw scheduled task row(s) to chat_message", changed)
    except Exception as exc:
        logger.warning("[startup] OpenClaw scheduled-task migration skipped: %s", exc)
