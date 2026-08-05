from __future__ import annotations

from datetime import datetime
from typing import Optional


DEVICE_ONLINE_TTL_SECONDS = 90


def is_device_online(
    last_seen_at: Optional[datetime],
    *,
    now: Optional[datetime] = None,
    ttl_seconds: float = DEVICE_ONLINE_TTL_SECONDS,
) -> bool:
    if last_seen_at is None:
        return False
    current = now or datetime.utcnow()
    return (current - last_seen_at).total_seconds() <= ttl_seconds
