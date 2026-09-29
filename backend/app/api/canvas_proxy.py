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

import json
import logging
import os
from typing import Dict, Tuple

import httpx
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..db import get_db
from ..models import User
from . import canvas_hub
from .auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

# 只中转「生成」类：模型/任务/语音/字幕/工具/素材转码等（用服务器速推 key）
_RELAY_PREFIXES: Tuple[str, ...] = (
    "api/v3/tasks",
    "api/v3/models",
    "api/v3/mcp/models",
    "api/v3/voices",
    "api/v3/captioning",
    "api/v3/tools",
    "api/v3/skills",
    "api/v3/uploads",
    "api/v3/apikeys",          # 只回占位 sk-（见下方特判）
    "api/v2/tasks",
    "api/fal/",
    "api/jimeng/",
    "api/flux_kontext/",
    "api/cozetask/",
    "api/doubao_task_list",
    "api/create_",
    "api/text_gen_video",
    "api/change_",
    "api/get_dall_task",
    "api/get_video_task",
    "api/get_audio_task",
    "api/get_face_task",
    "api/get_video_gen_task",
    "api/get_change_video_task",
    "api/get_lip_sync_tasks",
    "api/get_video_to_text_task",
    "api/get_video_info_by_id",
    "api/video/",
    "api/voice/",
    "api/scene/",
    "api/storyboard/submit_",
    "api/storyboard/get_voice_list",
    "api/storyboard/multi_voice_tts",
    "api/upload",
)

# 账号 / 钱 / Key / 后台：既不由我们实现，也不许中转（先于中转判断）
_REFUSE_PREFIXES: Tuple[str, ...] = (
    "api/login",
    "api/register2",
    "api/unregister",
    "api/get_qrcode",
    "api/check_qrcode_status",
    "api/get_message_code",
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
    "api/deduct_user_money",
    "api/reset_api_token",
    "api/v3/keys",
    "api/v3/account/pay",
    "api/v3/account/packages",
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


def _path_refused(path: str) -> bool:
    """账号/钱/Key/后台：我们自己不做，也绝不拿共享 key 去替用户操作。"""
    for prefix in _REFUSE_PREFIXES:
        if path == prefix or path.startswith(prefix):
            return True
    return False


def _path_relayable(path: str) -> bool:
    """只有「生成」类才中转 apiz。"""
    return any(path == p.rstrip("/") or path.startswith(p) for p in _RELAY_PREFIXES)


_EMPTY_OK_PATHS = (
    "api/get_draft_template",
    "api/draft_flow_template/list",
    "api/fal/tasks/list",
    "api/task_list",
    "api/tasks/list",
    "api/v3/points-campaigns/active",
    "api/get_activity_banner_list",
    "api/get_official_notification",
)


def _payload_model(body: Dict[str, Any]) -> str:
    """从画布请求体里找模型 id（各家字段名不一样）。"""
    if not isinstance(body, dict):
        return ""
    for key in ("model", "model_id", "modelId", "app_name", "model_name"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    params = body.get("params")
    if isinstance(params, dict):
        for key in ("model", "model_id", "modelId"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def settle_generation_credits(db: Session, user: User, response_body: bytes, model: str, path: str) -> float:
    """生成结束后按上游回报的消耗扣积分（走已有的定价/流水逻辑）。返回实扣数量。"""
    try:
        from ..services.credit_ledger import append_credit_ledger
        from ..services.credits_amount import quantize_credits, user_balance_decimal
        from ..services.sutui_pricing import extract_upstream_reported_credits

        body: Any = response_body
        try:
            body = json.loads(response_body.decode("utf-8", "replace"))
        except Exception:
            pass
        charged = extract_upstream_reported_credits(body)
        charged = quantize_credits(charged or 0)
        if charged <= 0:
            return 0.0
        db.refresh(user)
        balance = user_balance_decimal(user)
        if balance <= 0:
            return 0.0
        real = min(charged, balance)
        user.credits = balance - real
        append_credit_ledger(db, user.id, -real, "canvas_generate", user.credits,
                             description=f"画布生成：{model or path}", ref_type="canvas_api", ref_id=path,
                             meta={"model": model, "reported": float(charged)})
        db.commit()
        logger.info("[canvas] 生成扣费 user=%s model=%s 扣=%s 余额=%s", user.id, model, real, user.credits)
        return float(real)
    except Exception as exc:  # 计费本身不能把生成结果搞丢
        logger.warning("[canvas] 生成扣费失败（不影响生成）: %s", exc, exc_info=True)
        return 0.0


def _ok_list(items=None, total: int = 0, extra: Optional[Dict] = None) -> JSONResponse:
    body = {"code": 200, "projects": items or [], "list": items or [], "items": items or [],
            "total": total, "data": {"projects": items or [], "list": items or [], "total": total}}
    if extra:
        body.update(extra)
    return JSONResponse(body)


async def _hub_route(normalized: str, request: Request, user: User, db: Session) -> Optional[Response]:
    """画布自有数据（会话/项目/资产/上传）：本机自己回答，不打 apiz。"""
    hub = canvas_hub
    hub.ensure_tables(db)
    uid = int(getattr(user, "id", 0) or 0)
    method = request.method.upper()

    body: Dict[str, Any] = {}
    if method in ("POST", "PUT", "PATCH"):
        try:
            parsed = await request.json()
            if isinstance(parsed, dict):
                body = parsed
        except Exception:
            body = {}
    skip = int(request.query_params.get("skip") or 0)
    limit = int(request.query_params.get("limit") or request.query_params.get("page_size") or 50)

    if normalized.startswith("media/"):
        return hub.media_response(normalized[len("media/"):])

    if normalized == "api/user_info":
        from ..services.credits_amount import credits_json_float

        return JSONResponse({
            "code": 200, "token": "lobster-canvas", "id": uid,
            "name": getattr(user, "admin_remark", "") or f"用户 {uid}",
            "email": getattr(user, "email", "") or "", "phone": "",
            "points_balance": credits_json_float(getattr(user, "credits", 0)),
        })
    if normalized == "api/get_user_money":
        from ..services.credits_amount import credits_json_float

        credits = credits_json_float(getattr(user, "credits", 0))
        return JSONResponse({"code": 200, "points_balance": credits, "money": credits,
                             "data": {"points_balance": credits}})
    if normalized == "api/check_admin_permission":
        return JSONResponse({"code": 200, "data": {"is_admin": False}})

    if normalized.startswith("api/v1/projects"):
        rest = normalized[len("api/v1/projects"):].strip("/")
        if rest in ("", "create"):
            project = hub.create_project(db, user_id=uid, name=str(body.get("name") or ""),
                                         description=str(body.get("description") or ""),
                                         is_public=bool(body.get("is_public")))
            return JSONResponse({"code": 200, **project, "project": project, "data": project})
        if rest == "my":
            items = hub.list_projects(db, user_id=uid, only_public=False, skip=skip, limit=limit)
            return _ok_list(items, len(items))
        if rest == "public":
            if not skip:
                await hub.sync_public_templates(db, limit=max(60, limit))
            items = hub.list_projects(db, user_id=None, only_public=True, skip=skip, limit=limit)
            return _ok_list(items, len(items))
        if rest == "search":
            keyword = str(request.query_params.get("q") or "")
            items = hub.list_projects(db, user_id=None, only_public=True, skip=skip, limit=limit, keyword=keyword)
            return _ok_list(items, len(items))
        if rest.startswith("admin/"):
            return JSONResponse({"code": 200, "data": {"list": [], "total": 0}})

        parts = rest.split("/")
        ident = parts[0]
        action = parts[1] if len(parts) > 1 else ""
        if action == "update":
            row = hub.update_project(db, ident, uid, body)
            if row is None:
                raise HTTPException(status_code=404, detail="作品不存在")
            project = hub.project_row_to_json(row)
            return JSONResponse({"code": 200, **project, "project": project, "data": project})
        if action == "delete":
            hub.delete_project(db, ident, uid)
            return JSONResponse({"code": 200, "ok": True})
        if action == "clone":
            row = hub.get_project(db, ident, user_id=uid)
            if row is None:
                raise HTTPException(status_code=404, detail="作品不存在")
            copy = hub.create_project(db, user_id=uid, name=f"{row.name} 副本",
                                      description=row.description or "", is_public=False)
            if row.snapshot:
                hub.save_snapshot(db, copy["uuid"], uid, row.snapshot, row.thumbnail_url or "")
            return JSONResponse({"code": 200, **copy, "project": copy, "data": copy})
        if action == "canvas":
            if len(parts) > 2 and parts[2] == "load":
                row = hub.get_project(db, ident, user_id=uid)
                if row is None:
                    raise HTTPException(status_code=404, detail="作品不存在")
                row = await hub.ensure_snapshot(db, row, uid)
                project = hub.project_row_to_json(row, with_snapshot=True)
                return JSONResponse({"code": 200, "snapshot": project["snapshot"], "canvas": project["canvas"],
                                     "data": {"snapshot": project["snapshot"], "canvas": project["canvas"]}})
            snapshot = body.get("snapshot") if isinstance(body.get("snapshot"), dict) else body.get("canvas")
            row = hub.save_snapshot(db, ident, uid, snapshot or {}, str(body.get("thumbnail_url") or ""))
            if row is None:
                raise HTTPException(status_code=404, detail="作品不存在")
            project = hub.project_row_to_json(row)
            return JSONResponse({"code": 200, "ok": True, "project": project, "data": project})

        row = hub.get_project(db, ident, user_id=uid)
        if row is None:
            raise HTTPException(status_code=404, detail="作品不存在")
        project = hub.project_row_to_json(row, with_snapshot=True)
        return JSONResponse({"code": 200, **project, "project": project, "data": project})

    if normalized == "api/get_file_list":
        items = hub.list_assets(db, uid, skip, int(request.query_params.get("page_size") or 30))
        return JSONResponse({"code": 200, "list": items, "total": len(items),
                             "data": {"list": items, "total": len(items)}})
    if normalized == "api/user_oss_upload":
        asset = hub.add_asset(db, uid, str(body.get("file_url") or ""), str(body.get("file_type") or ""),
                              int(body.get("file_size") or 0), str(body.get("name") or ""))
        return JSONResponse({"code": 200, "ok": True, "data": asset, **asset})
    if normalized == "api/upload-token":
        return JSONResponse({"code": 200, "data": {"upload_url": "/canvas-api/api/upload", "token": "lobster-canvas",
                                                   "expires_in": 3600, "public_base": hub.public_base()}})
    if normalized == "api/get_cf_r2_token":
        name = str(body.get("file_name") or request.query_params.get("name") or "file")
        base = hub.public_base()
        return JSONResponse({"code": 200, "data": {
            "upload_url": f"{base}/canvas-api/api/upload?name={name}",
            "public_url": f"{base}/canvas-api/api/upload?name={name}",
            "file_url": f"{base}/canvas-api/api/upload?name={name}",
        }})
    if normalized in ("api/upload", "api/upload/", "api/user_upload"):
        result = await hub.handle_upload(request, uid)
        hub.add_asset(db, uid, result["url"], result["file_type"], result["file_size"], result["name"])
        return JSONResponse({"code": 200, "ok": True, **result, "data": result})

    if normalized == "api/get_draft_template":
        # Coze 草稿模板：拉进我们自己的库，之后读我们的库
        await hub.sync_draft_templates(db, limit=int(body.get("page_size") or 60))
        items = hub.list_draft_templates(db, limit=int(body.get("page_size") or 60))
        return JSONResponse({"code": 200, "data": items, "list": items, "msg": "获取成功"})

    if normalized in _EMPTY_OK_PATHS:
        return _ok_list([], 0)

    return None


@router.api_route(
    "/canvas-api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def canvas_proxy(
    path: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    normalized = _normalize_path(path)
    if not normalized or "://" in normalized or ".." in normalized.split("/"):
        raise HTTPException(status_code=404, detail="缺少接口路径")

    # 画布会拿 sk- 调 V3 模型：不把真 key 发到浏览器，只回占位值，转发时再注入速推 key
    if normalized.startswith("api/v3/apikeys"):
        return JSONResponse({"code": 200, "data": {"items": [{"status": "active", "key": "sk-lobster-canvas-proxy"}]}})

    # 顺序：先由我们自己的实现回答 -> 账号/钱类拒绝 -> 剩下的只有「生成」中转，其余明确回「未接入」
    hub_response = await _hub_route(normalized, request, user, db)
    if hub_response is not None:
        return hub_response

    if _path_refused(normalized):
        logger.warning("[canvas] refused path=%s user=%s", normalized, getattr(user, "id", ""))
        raise HTTPException(status_code=403, detail="该接口属于账号/资金/Key 管理，画布内不提供")

    if not _path_relayable(normalized):
        logger.info("[canvas] not-implemented path=%s user=%s", normalized, getattr(user, "id", ""))
        return JSONResponse({"code": 0, "msg": "该功能还没接到我们自己的服务器（生成类之外的都在逐步自建）",
                             "data": None, "path": normalized})

    body = await request.body()
    body_json: Dict[str, Any] = {}
    try:
        parsed_body = json.loads(body.decode("utf-8", "replace")) if body else {}
        if isinstance(parsed_body, dict):
            body_json = parsed_body
    except Exception:
        body_json = {}
    # 生成类先按速推定价表预检（余额不足 402 / 无价 400），调用后再按上游回报扣费
    model = _payload_model(body_json)
    if model:
        from ..services.sutui_billing_gate import assert_pricing_pre_deduct_allows_upstream_or_http

        assert_pricing_pre_deduct_allows_upstream_or_http(db, user, model, body_json, action_label="画布生成")

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

    if upstream.status_code < 400 and model:
        settle_generation_credits(db, user, upstream.content, model, normalized)
    logger.info("[canvas] user=%s %s %s -> %s", getattr(user, "id", ""), request.method, normalized, upstream.status_code)
    media_type = upstream.headers.get("content-type") or "application/json"
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=media_type)


# 还没做（下一轮）：按消耗扣算力。现在生成类只做 key 中转，用户不扣积分、apiz 成本由服务器承担。
# 建议做法：任务在 apiz 侧完成后，响应里会带本次消耗（services/sutui_pricing.extract_upstream_reported_credits），
# 以 task_id 幂等结算一次（services/credit_ledger.append_credit_ledger），失败/取消要回滚。
