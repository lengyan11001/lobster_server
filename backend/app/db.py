import contextvars
import logging
import os
import time

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from .core.config import settings

logger = logging.getLogger("db.pool")
_pool_debug_enabled = (os.environ.get("DB_POOL_DEBUG") or "").strip().lower() in {"1", "true", "yes", "on"}
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
    def _log_pool_checkout(dbapi_connection, connection_record, connection_proxy):
        info = connection_record.info
        info["checkout_ts"] = time.monotonic()
        ctx = _request_context.get({})
        info["checkout_ctx"] = dict(ctx)
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
    def _log_pool_checkin(dbapi_connection, connection_record):
        info = connection_record.info
        checkout_ts = info.pop("checkout_ts", None)
        ctx = info.pop("checkout_ctx", {}) or {}
        held_ms = int((time.monotonic() - checkout_ts) * 1000) if checkout_ts else -1
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
