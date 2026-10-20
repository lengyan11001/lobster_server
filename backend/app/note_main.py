from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import models  # noqa: F401
from .api.auth import router as auth_router
from .api.health import router as health_router
from .api.h5_recorder import router as h5_recorder_router
from .core.config import settings
from .db import Base, engine

logger = logging.getLogger(__name__)

NOTE_STATIC_DIR = Path(__file__).resolve().parents[2] / "note_static"


def create_note_app() -> FastAPI:
    """必火听记录（note.bhzn.top）：登录沿用管理后台 / manage 的账号体系，主体是 H5 的 AI 秘书（录音）。"""
    Base.metadata.create_all(bind=engine)
    app = FastAPI(
        title="必火听记录",
        version="0.1.0",
        description="Bihuo Note — AI secretary recorder, reusing the admin/manage account system.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 登录/续签/用户信息：与管理后台、manage 完全同一套
    app.include_router(health_router, prefix="")
    app.include_router(auth_router, prefix="/auth")

    # AI 秘书（录音整理）：直接复用 H5 的 /api/h5/recorder/*
    app.include_router(h5_recorder_router, prefix="")

    @app.get("/", include_in_schema=False)
    def note_index() -> FileResponse:
        return FileResponse(str(NOTE_STATIC_DIR / "index.html"))

    @app.get("/healthz", include_in_schema=False)
    def note_health() -> dict:
        return {"ok": True, "app": "note"}

    app.mount("/note-static", StaticFiles(directory=str(NOTE_STATIC_DIR)), name="note_static")
    return app


app = create_note_app()
