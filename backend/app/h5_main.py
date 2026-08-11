from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import models  # noqa: F401
from .api.auth import router as auth_router
from .api.assets import ensure_asset_library_indexes, router as assets_router
from .api.branding import router as branding_router
from .api.content_records import router as content_records_router
from .api.douyin_dashboard_h5 import router as douyin_dashboard_h5_router
from .api.global_leads import router as global_leads_router
from .api.health import router as health_router
from .api.h5_chat import router as h5_chat_router
from .api.mastra_chat import router as mastra_chat_router
from .api.h5_home import router as h5_home_router
from .api.h5_agent_management import router as h5_agent_management_router
from .api.h5_personal_settings import router as h5_personal_settings_router
from .api.h5_recorder import mark_interrupted_recordings_failed, router as h5_recorder_router
from .api.h5_voice import router as h5_voice_router
from .api.h5_workflows import router as h5_workflows_router
from .api.hifly_assets import router as hifly_assets_router
from .api.ip_content_studio import router as ip_content_studio_router
from .api.linkedin_mining import router as linkedin_mining_router
from .api.social_leads import router as social_leads_router
from .api.lead_collection_templates import router as lead_collection_templates_router
from .api.scheduled_tasks import router as scheduled_tasks_router
from .api.shanjian_digital_human import router as shanjian_digital_human_router
from .api.shanjian_smart_clip import router as shanjian_smart_clip_router
from .api.skills import router as skills_router
from .api.wechat_channels_transcript import router as wechat_channels_transcript_router
from .api.wechat_intelligence import router as wechat_intelligence_router
from .core.config import settings
from .db import Base, SessionLocal, engine, reset_db_request_context, set_db_request_context
from .services.brand_context import ensure_user_brand_schema, seed_brand_configs
from .services.workload_guard import install_workload_guard

logger = logging.getLogger(__name__)


def _ensure_h5_chat_mastra_columns() -> None:
    """Keep standalone H5 startup compatible with pre-Mastra databases."""
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        with engine.begin() as connection:
            if inspector.has_table("h5_chat_messages"):
                columns = {column["name"] for column in inspector.get_columns("h5_chat_messages")}
                if "parent_message_id" not in columns:
                    connection.execute(text("ALTER TABLE h5_chat_messages ADD COLUMN parent_message_id VARCHAR(64)"))
                if "attachments" not in columns:
                    connection.execute(text("ALTER TABLE h5_chat_messages ADD COLUMN attachments JSON"))
                if "session_id" not in columns:
                    connection.execute(text("ALTER TABLE h5_chat_messages ADD COLUMN session_id VARCHAR(64)"))
                if "queue_mode" not in columns:
                    connection.execute(
                        text("ALTER TABLE h5_chat_messages ADD COLUMN queue_mode VARCHAR(16) NOT NULL DEFAULT 'normal'")
                    )
                if "queue_priority" not in columns:
                    connection.execute(
                        text("ALTER TABLE h5_chat_messages ADD COLUMN queue_priority INTEGER NOT NULL DEFAULT 0")
                    )
                if "target_message_id" not in columns:
                    connection.execute(text("ALTER TABLE h5_chat_messages ADD COLUMN target_message_id VARCHAR(64)"))
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_h5_chat_messages_parent_message_id "
                        "ON h5_chat_messages (parent_message_id)"
                    )
                )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_h5_chat_messages_session_id "
                        "ON h5_chat_messages (session_id)"
                    )
                )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_h5_chat_messages_target_message_id "
                        "ON h5_chat_messages (target_message_id)"
                    )
                )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_h5_chat_mode_status_priority_created "
                        "ON h5_chat_messages (mode, status, queue_priority, created_at)"
                    )
                )
            if inspector.has_table("h5_chat_sessions"):
                session_columns = {column["name"] for column in inspector.get_columns("h5_chat_sessions")}
                if "summary_text" not in session_columns:
                    connection.execute(text("ALTER TABLE h5_chat_sessions ADD COLUMN summary_text TEXT"))
                if "summary_through_message_id" not in session_columns:
                    connection.execute(text("ALTER TABLE h5_chat_sessions ADD COLUMN summary_through_message_id VARCHAR(64)"))
                if "summary_updated_at" not in session_columns:
                    connection.execute(text("ALTER TABLE h5_chat_sessions ADD COLUMN summary_updated_at TIMESTAMP"))
    except Exception as exc:
        logger.warning("H5 Mastra column migration skipped: %s", exc)


def _ensure_recorder_audio_columns() -> None:
    """Keep the standalone H5 process compatible with existing recorder tables."""
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        if not inspector.has_table("recorder_audio_records"):
            return
        columns = {column["name"] for column in inspector.get_columns("recorder_audio_records")}
        with engine.begin() as connection:
            if "source_type" not in columns:
                connection.execute(text("ALTER TABLE recorder_audio_records ADD COLUMN source_type VARCHAR(32) NOT NULL DEFAULT 'device'"))
            if "source_doc_id" not in columns:
                connection.execute(text("ALTER TABLE recorder_audio_records ADD COLUMN source_doc_id VARCHAR(64)"))
    except Exception as exc:
        logger.warning("H5 recorder column migration skipped: %s", exc)


def _ensure_h5_home_preference_columns() -> None:
    """Keep standalone H5 startup compatible with existing preference rows."""
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        if not inspector.has_table("h5_home_preferences"):
            return
        columns = {column["name"] for column in inspector.get_columns("h5_home_preferences")}
        with engine.begin() as connection:
            if "speech_enabled" not in columns:
                default = "1" if engine.dialect.name == "sqlite" else "TRUE"
                connection.execute(
                    text(
                        "ALTER TABLE h5_home_preferences ADD COLUMN "
                        f"speech_enabled BOOLEAN NOT NULL DEFAULT {default}"
                    )
                )
            if "speech_voice_uri" not in columns:
                connection.execute(
                    text("ALTER TABLE h5_home_preferences ADD COLUMN speech_voice_uri VARCHAR(255)")
                )
    except Exception as exc:
        logger.warning("H5 home preference column migration skipped: %s", exc)


def create_h5_app() -> FastAPI:
    """Dedicated H5 app: auth, remote chat, scheduled tasks, and lightweight HiFly resources."""
    logger.info("[H5] create_h5_app start")
    Base.metadata.create_all(bind=engine)
    ensure_asset_library_indexes(engine)
    _ensure_h5_chat_mastra_columns()
    _ensure_recorder_audio_columns()
    _ensure_h5_home_preference_columns()
    ensure_user_brand_schema(engine)
    seed_brand_configs(SessionLocal)
    interrupted_recordings = mark_interrupted_recordings_failed()
    if interrupted_recordings:
        logger.warning("[H5] marked interrupted recorder jobs retryable count=%s", interrupted_recordings)
    app = FastAPI(
        title="Lobster H5 Chat",
        version="0.1.0",
        description="Remote H5 chat entry for local lobster_online clients.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_workload_guard(app)

    @app.middleware("http")
    async def db_pool_request_context(request: Request, call_next):
        token = set_db_request_context(
            method=request.method,
            path=request.url.path,
            request_id=request.headers.get("x-request-id") or request.headers.get("x-trace-id") or "",
            client=request.client.host if request.client else "",
        )
        try:
            return await call_next(request)
        finally:
            reset_db_request_context(token)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("[H5] unhandled error path=%s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    app.include_router(health_router, prefix="")
    app.include_router(auth_router, prefix="/auth")
    app.include_router(branding_router, prefix="")
    app.include_router(assets_router, prefix="")
    app.include_router(content_records_router, prefix="")
    app.include_router(douyin_dashboard_h5_router, prefix="")
    app.include_router(global_leads_router, prefix="")
    app.include_router(h5_chat_router, prefix="")
    app.include_router(mastra_chat_router, prefix="")
    app.include_router(h5_home_router, prefix="")
    app.include_router(h5_agent_management_router, prefix="")
    app.include_router(h5_personal_settings_router, prefix="")
    app.include_router(h5_recorder_router, prefix="")
    app.include_router(h5_voice_router, prefix="")
    app.include_router(h5_workflows_router, prefix="")
    app.include_router(hifly_assets_router, prefix="")
    app.include_router(scheduled_tasks_router, prefix="")
    app.include_router(shanjian_smart_clip_router, prefix="")
    app.include_router(shanjian_digital_human_router, prefix="")
    app.include_router(ip_content_studio_router, prefix="")
    app.include_router(linkedin_mining_router, prefix="")
    app.include_router(social_leads_router, prefix="")
    app.include_router(lead_collection_templates_router, prefix="")
    app.include_router(wechat_channels_transcript_router, prefix="")
    app.include_router(wechat_intelligence_router, prefix="")
    app.include_router(skills_router, prefix="")

    miniprogram_static_dir = Path(__file__).resolve().parent.parent.parent / "client_static" / "miniprogram"
    miniprogram_static_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/client/miniprogram",
        StaticFiles(directory=str(miniprogram_static_dir)),
        name="client_miniprogram",
    )
    oem_static_dir = Path(__file__).resolve().parent.parent.parent / "client_static" / "oem"
    oem_static_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/client/oem",
        StaticFiles(directory=str(oem_static_dir)),
        name="client_oem",
    )
    return app


app = create_h5_app()
