from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import models  # noqa: F401
from .api.auth import router as auth_router
from .api.assets import router as assets_router
from .api.douyin_dashboard_h5 import router as douyin_dashboard_h5_router
from .api.global_leads import router as global_leads_router
from .api.h5_chat import router as h5_chat_router
from .api.h5_agent_management import router as h5_agent_management_router
from .api.h5_personal_settings import router as h5_personal_settings_router
from .api.h5_voice import router as h5_voice_router
from .api.h5_workflows import router as h5_workflows_router
from .api.hifly_assets import router as hifly_assets_router
from .api.ip_content_studio import router as ip_content_studio_router
from .api.linkedin_mining import router as linkedin_mining_router
from .api.social_leads import router as social_leads_router
from .api.lead_collection_templates import router as lead_collection_templates_router
from .api.scheduled_tasks import router as scheduled_tasks_router
from .api.skills import router as skills_router
from .api.wechat_channels_transcript import router as wechat_channels_transcript_router
from .core.config import settings
from .db import Base, engine

logger = logging.getLogger(__name__)


def create_h5_app() -> FastAPI:
    """Dedicated H5 app: auth, remote chat, scheduled tasks, and lightweight HiFly resources."""
    logger.info("[H5] create_h5_app start")
    Base.metadata.create_all(bind=engine)
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

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("[H5] unhandled error path=%s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    app.include_router(auth_router, prefix="/auth")
    app.include_router(assets_router, prefix="")
    app.include_router(douyin_dashboard_h5_router, prefix="")
    app.include_router(global_leads_router, prefix="")
    app.include_router(h5_chat_router, prefix="")
    app.include_router(h5_agent_management_router, prefix="")
    app.include_router(h5_personal_settings_router, prefix="")
    app.include_router(h5_voice_router, prefix="")
    app.include_router(h5_workflows_router, prefix="")
    app.include_router(hifly_assets_router, prefix="")
    app.include_router(scheduled_tasks_router, prefix="")
    app.include_router(ip_content_studio_router, prefix="")
    app.include_router(linkedin_mining_router, prefix="")
    app.include_router(social_leads_router, prefix="")
    app.include_router(lead_collection_templates_router, prefix="")
    app.include_router(wechat_channels_transcript_router, prefix="")
    app.include_router(skills_router, prefix="")

    miniprogram_static_dir = Path(__file__).resolve().parent.parent.parent / "client_static" / "miniprogram"
    miniprogram_static_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/client/miniprogram",
        StaticFiles(directory=str(miniprogram_static_dir)),
        name="client_miniprogram",
    )
    return app


app = create_h5_app()
