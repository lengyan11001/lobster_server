from __future__ import annotations

import logging
import time
from typing import Any, Optional

try:
    import redis
except Exception:  # pragma: no cover - dependency may be absent during partial deploys
    redis = None  # type: ignore[assignment]

from ..core.config import settings

logger = logging.getLogger(__name__)

_memory_cache: dict[str, tuple[float, str]] = {}
_redis_client: Any = None
_redis_checked_at = 0.0
_redis_disabled_until = 0.0


def _memory_prune(now: Optional[float] = None) -> None:
    if len(_memory_cache) <= 10000:
        return
    ts = now or time.monotonic()
    for key, (expires_at, _) in list(_memory_cache.items())[:3000]:
        if expires_at <= ts:
            _memory_cache.pop(key, None)


def _redis() -> Any:
    global _redis_client, _redis_checked_at, _redis_disabled_until
    url = (getattr(settings, "redis_url", None) or "").strip()
    if not url or redis is None:
        return None
    now = time.monotonic()
    if _redis_disabled_until > now:
        return None
    if _redis_client is not None:
        return _redis_client
    if now - _redis_checked_at < 5.0:
        return None
    _redis_checked_at = now
    try:
        client = redis.Redis.from_url(
            url,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
            decode_responses=True,
        )
        client.ping()
        _redis_client = client
        logger.info("runtime cache using redis")
        return _redis_client
    except Exception as exc:
        _redis_disabled_until = now + 10.0
        logger.warning("runtime cache redis unavailable; fallback to memory: %s", exc)
        return None


def cache_get(key: str) -> Optional[str]:
    global _redis_client, _redis_disabled_until
    if not key:
        return None
    client = _redis()
    if client is not None:
        try:
            value = client.get(key)
            if value is not None:
                return str(value)
        except Exception as exc:
            _redis_client = None
            _redis_disabled_until = time.monotonic() + 10.0
            logger.warning("runtime cache redis get failed; fallback to memory: %s", exc)
    item = _memory_cache.get(key)
    if not item:
        return None
    expires_at, value = item
    now = time.monotonic()
    if expires_at <= now:
        _memory_cache.pop(key, None)
        return None
    return value


def cache_set(key: str, value: str = "1", ttl_seconds: float = 10.0) -> None:
    global _redis_client, _redis_disabled_until
    if not key or ttl_seconds <= 0:
        return
    client = _redis()
    if client is not None:
        try:
            client.setex(key, max(1, int(ttl_seconds)), value)
            return
        except Exception as exc:
            _redis_client = None
            _redis_disabled_until = time.monotonic() + 10.0
            logger.warning("runtime cache redis set failed; fallback to memory: %s", exc)
    now = time.monotonic()
    _memory_cache[key] = (now + float(ttl_seconds), value)
    _memory_prune(now)


def cache_delete(key: str) -> None:
    global _redis_client, _redis_disabled_until
    if not key:
        return
    client = _redis()
    if client is not None:
        try:
            client.delete(key)
        except Exception as exc:
            _redis_client = None
            _redis_disabled_until = time.monotonic() + 10.0
            logger.warning("runtime cache redis delete failed; fallback to memory: %s", exc)
    _memory_cache.pop(key, None)


def cache_delete_prefix(prefix: str) -> int:
    global _redis_client, _redis_disabled_until
    if not prefix:
        return 0
    deleted = 0
    client = _redis()
    if client is not None:
        try:
            for key in client.scan_iter(f"{prefix}*"):
                deleted += int(client.delete(key) or 0)
        except Exception as exc:
            _redis_client = None
            _redis_disabled_until = time.monotonic() + 10.0
            logger.warning("runtime cache redis delete_prefix failed; fallback to memory: %s", exc)
    for key in list(_memory_cache.keys()):
        if key.startswith(prefix):
            _memory_cache.pop(key, None)
            deleted += 1
    return deleted


def cache_flag_recent(key: str) -> bool:
    return cache_get(key) is not None


def cache_mark_flag(key: str, ttl_seconds: float) -> None:
    cache_set(key, "1", ttl_seconds)
