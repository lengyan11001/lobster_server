from __future__ import annotations

from fastapi import APIRouter

from ..services.runtime_monitor import collect_runtime_monitor_snapshot

router = APIRouter()


@router.get("/api/runtime-monitor/health", summary="Query runtime monitor snapshot")
async def runtime_monitor_health():
    return await collect_runtime_monitor_snapshot()
