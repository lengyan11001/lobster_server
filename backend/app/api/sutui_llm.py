"""速推 LLM 探测结果只读接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..models import User
from ..services.sutui_llm_probe import read_sutui_llm_snapshot
from .auth import get_current_user

router = APIRouter()


@router.get("/api/sutui-llm/models", summary="上次探测的速推 LLM 列表与推荐模型（需登录）")
def get_sutui_llm_models(current_user: User = Depends(get_current_user)):
    snap = read_sutui_llm_snapshot()
    models = snap.get("models") if isinstance(snap.get("models"), list) else []
    return {
        "probed_at": snap.get("probed_at"),
        "recommended": snap.get("recommended"),
        "models": models,
        "error": snap.get("error"),
        "category_filter": snap.get("category_filter"),
    }
