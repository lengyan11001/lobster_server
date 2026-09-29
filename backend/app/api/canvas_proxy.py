"""剧查查超级工厂（canvas_web）接入：模型/任务全部走服务器 apiz key。

- 入口鉴权 = **online 账号登录态**（与站内其它接口同一套 JWT，见 api/auth.get_current_user），
  不再需要单独的画布 key；
- 上游 apiz 用服务器已配置的**速推 key 池**（mcp.sutui_tokens），真 key 不下发到浏览器；
- 只放行模型/任务/上传类接口；站点账号类接口（登录、Key 管理、充值、支付）一律 403；
- 浏览器里画布自带的 token 一律忽略，转发时统一换成服务器上的速推 key；
- 不做上游兜底：apiz 返回什么就原样返回什么。
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..models import User
from .auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_PREFIXES: Tuple[str, ...] = (
    "api/v3/tasks",
    "api/v3/mcp/models",
    "api/v3/models/",
    "api/v3/voices",
    "api/v3/captioning",
    "api/v3/tools",
    "api/v3/skills",
    "api/v3/uploads",
    "api/v3/account/balance",
    "api/fal/",
    "api/upload",
    "v1/models",
)

_BLOCKED_PREFIXES: Tuple[str, ...] = (
    "api/v3/keys",
    "api/v3/account/pay",
    "api/v3/account/packages",
    "api/user_info",
    "api/login",
    "api/login_message",
    "api/register2",
    "api/get_qrcode",
    "api/check_qrcode_status",
    "api/unregister",
    "api/create_wx_order_info",
    "api/create_alipay_order_info",
    "api/get_order_info",
    "api/get_pay_info_list",
    "api/reset_api_token",
    "api/get_user_money",
)

_DEFAULT_TIMEOUT = 120.0
_UPLOAD_TIMEOUT = 600.0


def _apiz_base() -> str:
    """画布固定走 apiz 模型平台（不要跟着 SUTUI_API_BASE 走，避免走到别的站点）。"""
    base = os.environ.get("CANVAS_APIZ_BASE_URL") or os.environ.get("APIZ_BASE_URL") or "https://api.apiz.ai"
    return str(base or "https://api.apiz.ai").strip().rstrip("/")


async def _apiz_headers() -> Dict[str, str]:
    """服务器配置的速推 key（池），没有单独的画布 key。"""
    from mcp.sutui_tokens import next_sutui_server_token_with_pool

    token, pool_key = await next_sutui_server_token_with_pool()
    if not token:
        raise HTTPException(status_code=503, detail=f"速推 key 未配置（pool={pool_key or 'none'}）")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _normalize_path(path: str) -> str:
    return str(path or "").strip().lstrip("/")


def _path_allowed(path: str) -> bool:
    for blocked in _BLOCKED_PREFIXES:
        if path == blocked or path.startswith(blocked):
            return False
    return any(path == p.rstrip("/") or path.startswith(p) for p in _ALLOWED_PREFIXES)


@router.api_route(
    "/canvas-api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def canvas_proxy(path: str, request: Request, user: User = Depends(get_current_user)) -> Response:
    normalized = _normalize_path(path)
    if not normalized:
        raise HTTPException(status_code=404, detail="缺少接口路径")

    # 画布会拿 sk- 调 V3 模型：不把真 key 发到浏览器，只回占位值，转发时再注入速推 key
    if normalized.startswith("api/v3/apikeys"):
        return JSONResponse({"code": 200, "data": {"items": [{"status": "active", "key": "sk-lobster-canvas-proxy"}]}})

    if not _path_allowed(normalized):
        logger.warning("[canvas] blocked path=%s user=%s", normalized, getattr(user, "id", ""))
        raise HTTPException(status_code=403, detail="该接口不在画布允许范围内")

    body = await request.body()
    headers = await _apiz_headers()
    for name in ("content-type", "accept", "accept-language"):
        value = request.headers.get(name)
        if value:
            headers[name] = value

    timeout = _UPLOAD_TIMEOUT if "upload" in normalized else _DEFAULT_TIMEOUT
    url = f"{_apiz_base()}/{normalized}"
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            upstream = await client.request(
                request.method,
                url,
                content=body or None,
                headers=headers,
                params=dict(request.query_params),
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"apiz 请求超时：{exc}") from exc
    except httpx.TransportError as exc:
        raise HTTPException(status_code=502, detail=f"apiz 连接失败：{exc}") from exc

    logger.info("[canvas] user=%s %s %s -> %s", getattr(user, "id", ""), request.method, normalized, upstream.status_code)
    media_type = upstream.headers.get("content-type") or "application/json"
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=media_type)


# 还没做（下一轮）：按消耗扣算力。现在只做 key 中转，用户不扣积分、apiz 成本由服务器承担。
# 建议做法：任务在 apiz 侧完成后，响应里会带本次消耗（services/sutui_pricing.extract_upstream_reported_credits），
# 以 task_id 幂等结算一次（services/credit_ledger.append_credit_ledger），失败/取消要回滚。
