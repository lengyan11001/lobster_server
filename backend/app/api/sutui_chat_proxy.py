"""鉴权后按用户是否管理员选择速推 Token 池，转发 OpenAI 兼容 chat/completions 至 api.xskill.ai。"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from mcp.sutui_tokens import next_sutui_server_token

from ..core.config import settings
from ..models import User
from .auth import get_current_user
from .skills import _skill_store_admin

logger = logging.getLogger(__name__)

router = APIRouter()


def _api_base() -> str:
    return (getattr(settings, "sutui_api_base", None) or "https://api.xskill.ai").rstrip("/")


@router.post("/api/sutui-chat/completions", summary="速推 LLM 对话代理（需登录）")
async def sutui_chat_completions(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体须为 JSON")

    admin = _skill_store_admin(current_user)
    token = await next_sutui_server_token(is_admin=admin)
    if not token:
        raise HTTPException(
            status_code=503,
            detail="服务器未配置速推 Token 池（用户池/管理员池均为空）",
        )

    stream = bool(body.get("stream"))
    url = f"{_api_base()}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    if not stream:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, json=body, headers=headers)
        try:
            data = r.json()
        except Exception:
            raise HTTPException(status_code=502, detail=(r.text or "")[:2000])

        return JSONResponse(content=data, status_code=r.status_code)

    async def gen() -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code >= 400:
                    txt = (await resp.aread()).decode("utf-8", errors="replace")
                    err = json.dumps({"error": {"message": txt[:2000], "status": resp.status_code}}, ensure_ascii=False)
                    yield f"data: {err}\n\n".encode("utf-8")
                    return
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream")
