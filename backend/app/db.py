import contextvars
import logging
import os
import time

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from .core.config import settings

logger = logging.getLogger("db.pool")
_pool_debug_enabled = (os.environ.get("DB_POOL_DEBUG") or "").strip().lower() in {"1", "true", "yes", "on"}
try:
    _slow_checkout_seconds = max(1.0, float(os.environ.get("DB_POOL_SLOW_CHECKOUT_SECONDS") or "15"))
except (TypeError, ValueError):
    _slow_checkout_seconds = 15.0
_request_context: contextvars.ContextVar[dict] = contextvars.ContextVar("db_request_context", default={})


def set_db_request_context(**values):
    return _request_context.set({k: v for k, v in values.items() if v is not None})


def reset_db_request_context(token) -> None:
    try:
        _request_context.reset(token)
    except Exception:
        pass


_db_url = (settings.database_url or "").strip()
if "sqlite" in _db_url.lower():
    engine = create_engine(
        _db_url,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_engine(
        _db_url,
        pool_pre_ping=True,
        pool_size=max(1, int(settings.db_pool_size)),
        max_overflow=max(0, int(settings.db_max_overflow)),
        pool_timeout=max(30, int(settings.db_pool_timeout)),
        pool_recycle=max(60, int(settings.db_pool_recycle)),
        echo_pool="debug" if _pool_debug_enabled else None,
    )

if _pool_debug_enabled:
    logging.getLogger("sqlalchemy.pool").setLevel(logging.DEBUG)


@event.listens_for(engine, "checkout")
def _track_pool_checkout(dbapi_connection, connection_record, connection_proxy):
    info = connection_record.info
    info["checkout_ts"] = time.monotonic()
    ctx = _request_context.get({})
    info["checkout_ctx"] = dict(ctx)
    if _pool_debug_enabled:
        logger.info(
            "checkout conn=%s record=%s pid=%s method=%s path=%s request_id=%s client=%s",
            id(dbapi_connection),
            id(connection_record),
            os.getpid(),
            ctx.get("method", "-"),
            ctx.get("path", "-"),
            ctx.get("request_id", "-"),
            ctx.get("client", "-"),
        )


@event.listens_for(engine, "checkin")
def _track_pool_checkin(dbapi_connection, connection_record):
    info = connection_record.info
    checkout_ts = info.pop("checkout_ts", None)
    ctx = info.pop("checkout_ctx", {}) or {}
    held_seconds = (time.monotonic() - checkout_ts) if checkout_ts else -1.0
    held_ms = int(held_seconds * 1000) if held_seconds >= 0 else -1
    if _pool_debug_enabled:
        logger.info(
            "checkin conn=%s record=%s pid=%s held_ms=%s method=%s path=%s request_id=%s client=%s",
            id(dbapi_connection),
            id(connection_record),
            os.getpid(),
            held_ms,
            ctx.get("method", "-"),
            ctx.get("path", "-"),
            ctx.get("request_id", "-"),
            ctx.get("client", "-"),
        )
    elif held_seconds >= _slow_checkout_seconds:
        logger.warning(
            "slow checkin conn=%s record=%s pid=%s held_ms=%s method=%s path=%s request_id=%s client=%s",
            id(dbapi_connection),
            id(connection_record),
            os.getpid(),
            held_ms,
            ctx.get("method", "-"),
            ctx.get("path", "-"),
            ctx.get("request_id", "-"),
            ctx.get("client", "-"),
        )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        try:
            if db.in_transaction():
                db.rollback()
        except Exception:
            pass
        db.close()
