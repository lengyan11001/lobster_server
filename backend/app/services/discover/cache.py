"""极简 TTL 缓存（进程内，够用于热搜/资讯短周期缓存）。"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional, Tuple

_CACHE: dict[str, Tuple[float, Any]] = {}
_LOCK = threading.Lock()


def get(key: str) -> Optional[Any]:
    with _LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at < time.time():
            _CACHE.pop(key, None)
            return None
        return value


def set(key: str, value: Any, ttl_seconds: int) -> None:  # noqa: A001
    with _LOCK:
        _CACHE[key] = (time.time() + max(1, ttl_seconds), value)


def clear() -> None:
    with _LOCK:
        _CACHE.clear()
