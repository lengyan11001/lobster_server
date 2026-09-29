"""剧查查超级工厂（canvas_web）接入：模型/任务全部走服务器 apiz key。

- 入口鉴权 = **online 账号登录态**（与站内其它接口同一套 JWT，见 api/auth.get_current_user），
  不再需要单独的画布 key；
- 上游 apiz 用服务器已配置的**速推 key 池**（mcp.sutui_tokens），真 key 不下发到浏览器；
- 默认放行画布的全部业务接口（模型/任务/素材/项目模板/上传…），
  只拦「账号 + 钱 + Key 管理 + 后台」这几类（见 _BLOCKED_PREFIXES）：
  画布的登录/扫码/注册、余额/订单/充值/提现/优惠券/激活码、apiz Key 管理、admin 后台一律 403；
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

_BLOCKED_PREFIXES: Tuple[str, ...] = (
    # 登录 / 注册 / 验证码：画布自带账号体系一律不用（登录态由服务器侧 JWT + 服务器 key 提供）
    "api/login",
    "api/register2",
    "api/unregister",
    "api/get_qrcode",
    "api/check_qrcode_status",
    "api/get_message_code",
    "api/user_info",
    "api/update_user_info",
    "api/update_user_token",
    # 钱：余额 / 订单 / 充值 / 提现 / 优惠券 / 激活码 / 合同
    "api/get_user_money",
    "api/deduct_user_money",
    "api/create_wx_order_info",
    "api/create_alipay_order_info",
    "api/get_order_info",
    "api/get_pay_info_list",
    "api/get_user_coupons",
    "api/grant_daily_coupons",
    "api/consume_activation_code",
    "api/get_withdraw_record_list",
    "api/submit_withdraw_record",
    "api/create_invite_code",
    "api/get_contract_info",
    "api/submit_contract",
    "api/courses/purchase",
    # 资产库 / 上传：暂时挡上，改由**我们自己的服务器**实现（apiz 是共享账号，
    # 放行会让不同用户互相看到对方的上传；sutui key 只留给"生成"类调用）
    "api/get_file_list",
    "api/user_oss_upload",
    "api/upload-token",
    "api/get_cf_r2_token",
    "api/get_sts_token",
    # Key 管理 / 站点账号安全
    "api/get_api_token",
    "api/reset_api_token",
    "api/v3/keys",
    "api/v3/account/pay",
    "api/v3/account/packages",
    "api/v3/apikeys",
    # 后台
    "api/admin",
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


def _path_blocked(path: str) -> bool:
    for blocked in _BLOCKED_PREFIXES:
        if path == blocked or path.startswith(blocked):
            return True
    return False


@router.api_route(
    "/canvas-api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def canvas_proxy(path: str, request: Request, user: User = Depends(get_current_user)) -> Response:
    normalized = _normalize_path(path)
    if not normalized or "://" in normalized or ".." in normalized.split("/"):
        raise HTTPException(status_code=404, detail="缺少接口路径")

    # 画布会拿 sk- 调 V3 模型：不把真 key 发到浏览器，只回占位值，转发时再注入速推 key
    if normalized.startswith("api/v3/apikeys"):
        return JSONResponse({"code": 200, "data": {"items": [{"status": "active", "key": "sk-lobster-canvas-proxy"}]}})

    if _path_blocked(normalized):
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
