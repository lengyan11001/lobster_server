"""在线版：本机直连 LLM 对话完成后按速推定价扣积分（与 sutui_pricing 一致）。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from ..models import User
from ..services.sutui_pricing import estimate_pre_deduct_credits
from .auth import get_current_user

router = APIRouter()


def _should_deduct_credits() -> bool:
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    return edition == "online" and getattr(settings, "lobster_independent_auth", True)


class DeductAfterLlmIn(BaseModel):
    """model 为 OpenAI 风格 id（如 deepseek/deepseek-chat）；有 usage 时按 token 计费。"""
    model: str = Field(..., min_length=1)
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None


@router.post("/api/chat/deduct-after-llm", summary="对话完成后按模型用量扣积分（在线版）")
def deduct_after_llm(
    body: DeductAfterLlmIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _should_deduct_credits():
        return {"credits_charged": 0}
    mid = (body.model or "").strip()
    params: dict = {}
    if body.prompt_tokens is not None:
        params["prompt_tokens"] = int(body.prompt_tokens)
    if body.completion_tokens is not None:
        params["completion_tokens"] = int(body.completion_tokens)
    est, err = estimate_pre_deduct_credits(mid, params if params else None)
    if err:
        raise HTTPException(status_code=400, detail=err)
    if est <= 0:
        raise HTTPException(status_code=400, detail="无法计算本次对话扣分数")
    db.refresh(current_user)
    if (current_user.credits or 0) < est:
        raise HTTPException(
            status_code=402,
            detail=f"积分不足：本次对话需 {est} 积分，当前余额 {current_user.credits or 0}。请先充值。",
        )
    current_user.credits = (current_user.credits or 0) - est
    db.commit()
    return {"credits_charged": est}
