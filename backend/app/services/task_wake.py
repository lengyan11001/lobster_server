"""Cross-worker wake bus for the H5 live-executor SSE stream.

The API runs with multiple uvicorn workers (``BACKEND_WORKERS=4``), so an
in-process ``asyncio.Event`` cannot reach the worker that holds a device's SSE
connection. We use a small append-only table as the shared bus:

* task creation appends a row in the same transaction;
* every worker tails the table once per second and fans out to its local
  subscribers.

No Redis is required at the current fleet size (< a few hundred installations).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Set, Tuple

from sqlalchemy import func

from ..models import TaskWakeEvent

logger = logging.getLogger(__name__)

_POLL_SECONDS = 1.0
_CLEANUP_SECONDS = 300.0
_RETENTION = timedelta(minutes=10)
_MAX_QUEUE = 32

# (user_id, installation_id) -> live queues. installation_id "" is a wildcard
# subscription used by the H5 browser, which watches the whole account.
_subscribers: Dict[Tuple[int, str], Set[asyncio.Queue]] = {}
_last_id = 0
_poller = None


def record(db, *, user_id: int, installation_id: str = "", kind: str = "scheduled_task") -> None:
    """Append a wake row to the caller's transaction (caller commits)."""
    if not user_id:
        return
    db.add(
        TaskWakeEvent(
            user_id=int(user_id),
            installation_id=str(installation_id or "")[:128],
            kind=str(kind or "scheduled_task")[:32],
        )
    )


async def subscribe(user_id: int, installation_id: str) -> asyncio.Queue:
    queue = asyncio.Queue(maxsize=_MAX_QUEUE)
    key = (int(user_id), str(installation_id or ""))
    _subscribers.setdefault(key, set()).add(queue)
    return queue


async def unsubscribe(user_id: int, installation_id: str, queue: asyncio.Queue) -> None:
    key = (int(user_id), str(installation_id or ""))
    bucket = _subscribers.get(key)
    if not bucket:
        return
    bucket.discard(queue)
    if not bucket:
        _subscribers.pop(key, None)


def _dispatch_local(event: dict) -> None:
    user_id = int(event.get("user_id") or 0)
    installation_id = str(event.get("installation_id") or "")
    if not user_id:
        return
    for (sub_user, sub_install), queues in list(_subscribers.items()):
        if sub_user != user_id:
            continue
        if installation_id and sub_install and sub_install != installation_id:
            continue
        for queue in list(queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled browser/client only needs the newest wake event.
                pass


def _read_new_events(db) -> list:
    global _last_id
    rows = (
        db.query(TaskWakeEvent)
        .filter(TaskWakeEvent.id > _last_id)
        .order_by(TaskWakeEvent.id.asc())
        .limit(200)
        .all()
    )
    events = [
        {
            "id": int(row.id),
            "user_id": int(row.user_id),
            "installation_id": str(row.installation_id or ""),
            "kind": str(row.kind or ""),
        }
        for row in rows
    ]
    if events:
        _last_id = int(events[-1]["id"])
    return events


async def _poll_forever() -> None:
    global _last_id
    from ..db import SessionLocal

    next_cleanup = 0.0
    # Skip rows written before this worker started; the client still has its
    # normal poll fallback, so replaying history only causes noise.
    try:
        with SessionLocal() as db:
            current_max = db.query(func.max(TaskWakeEvent.id)).scalar()
            _last_id = int(current_max or 0)
    except Exception:
        logger.exception("[TASK-WAKE] cursor init failed")

    while True:
        try:
            events = []
            with SessionLocal() as db:
                events = _read_new_events(db)
                now_mono = time.monotonic()
                if now_mono >= next_cleanup:
                    next_cleanup = now_mono + _CLEANUP_SECONDS
                    db.query(TaskWakeEvent).filter(
                        TaskWakeEvent.created_at < datetime.utcnow() - _RETENTION
                    ).delete(synchronize_session=False)
                    db.commit()
            for event in events:
                _dispatch_local(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[TASK-WAKE] poll failed")
            await asyncio.sleep(2.0)
        await asyncio.sleep(_POLL_SECONDS)


def start_background_poller() -> None:
    global _poller
    if _poller is not None and not _poller.done():
        return
    loop = asyncio.get_running_loop()
    _poller = loop.create_task(_poll_forever(), name="task-wake-poller")
    logger.info("[TASK-WAKE] poller started")


def reset_state_for_tests() -> None:
    global _poller, _last_id
    _subscribers.clear()
    _poller = None
    _last_id = 0
