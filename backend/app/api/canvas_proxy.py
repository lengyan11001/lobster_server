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
# 只中转「生成」类（这些才是真正花钱调用模型能力的）
_RELAY_PREFIXES: Tuple[str, ...] = (
    "api/v3/tasks",
    "api/v3/captioning",
    "api/v3/tools",
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
)

# 公开目录类（模型/音色/技能）：本地缓存 + 我们的价，不转发
_CATALOG_PATHS = {
    "api/v3/mcp/models": "/api/v3/mcp/models?lang=zh-CN",
    "api/v3/models": "/api/v3/mcp/models?lang=zh-CN",
    "api/v3/models/list": "/api/v3/mcp/models?lang=zh-CN",
    "api/v3/voices": "/api/v3/voices",
    "api/v3/skills": "/api/v3/skills",
}

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


# 只有「真正下单」的接口才结算；报价/估价/列表/查询一律不扣（曾被 tasks/quote 误扣 4 分/次）
_SETTLE_PATH_HINTS = ("/create", "/submit", "create_", "text_gen_video", "storyboard/submit_")


def _should_settle(path: str) -> bool:
    return any(hint in path for hint in _SETTLE_PATH_HINTS)


# 画布加价系数：定价表算不出来的计价类型（composite_media_price / char_based），
# 先按表里写明的单价 ×1.5 收（2026-09-29 用户口径）
CANVAS_PRICE_MARKUP = os.environ.get("CANVAS_PRICE_MARKUP", "1.5")
# 文档口径：1 元 = 100 积分
CREDITS_PER_YUAN = 100.0


# 零毛利模型（我们的表价 = apiz 采购价）：统一 ×1.5（2026-09-29 口径）
_CANVAS_MARKUP_MODELS = {
    "apiz/gpt-image-2.5-flare": 1.5,
    "apiz/gpt-image-2.5-sunburst": 1.5,
    "fal-ai/nano-banana-2": 1.5,
    "fal-ai/nano-banana-pro": 1.5,
    "xai/grok-imagine-image-2.0/text-to-image": 1.5,
    "xai/grok-imagine-image-2.0/edit": 1.5,
    "apiz/seedream-5.0-pro": 1.5,
    "kapon/gemini-3-pro-image-preview": 1.5,
    "fal-ai/kling-video/v3/standard/motion-control": 1.5,
    "fal-ai/kling-video/v3/pro/motion-control": 1.5,
    "fal-ai/kling-video/v2.6/standard/motion-control": 1.5,
    "leonardo/seed-audio-1.0": 1.5,
    "volcengine/speech-to-text/bigmodel-v2": 1.5,
    "volcengine/captioning/ata-speech": 1.5,
    "volcengine/captioning/ata-singing": 1.5,
    "minimax/voice-design": 1.5,
    "minimax/voice-clone": 1.5,
    "minimax/t2a": 1.5,
    # seedance 这类 token 结算的模型本来就贵，按 1.2 收（2026-09-29 口径）
    "bytedance/seedance-2.5": 1.2,
    "apiz/seedance-2.5": 1.2,
    "doubao-seedance-2-0-fast-260128": 1.2,
    "doubao-seedance-2-0-260128-betydance": 1.2,
}

# token 后结算、且表里没写「积分/秒」的模型：先按同族秒价估（避免算不出价直接白送）
_TOKEN_MODEL_RATE_FALLBACK = 100.0


# 会员等级 -> 定价表里的 vip_discount 倍率（0=普通用户 2.0、1=一级 1.5、2=二级 1.0）
CANVAS_VIP_LEVEL = os.environ.get("CANVAS_VIP_LEVEL", "0")


# 按版本定价的模型：请求里带哪个版本就用哪个采购价（apiz 页面口径）
# minimax/music-gen：music-2.5 = 100 积分/首、music-2.0 = 25 积分/首
_VERSION_PRICE_OVERRIDES = {
    ("minimax/music-gen", "2.0"): 25.0,
    ("minimax/music-gen", "2.5"): 100.0,
}


def version_base_price(model: str, body: Dict[str, Any]) -> "object":
    """请求里指明了版本时，用该版本的采购价（作为表价基数）。"""
    import json as _json
    from decimal import Decimal

    if not any(key[0] == model for key in _VERSION_PRICE_OVERRIDES):
        return None
    blob = _json.dumps(body, ensure_ascii=False).lower()
    for (m, version), price in _VERSION_PRICE_OVERRIDES.items():
        if m != model:
            continue
        if version in blob:
            return Decimal(str(price))
    return None


def apply_vip_discount(price: Dict[str, Any], amount: "object") -> "object":
    """定价表里写了 vip_discount 的模型：按会员等级倍率收（普通用户 2 倍）。"""
    from decimal import Decimal

    table = (price or {}).get("vip_discount")
    if not isinstance(table, dict) or amount is None:
        return amount
    entry = table.get(str(CANVAS_VIP_LEVEL)) or table.get(int(CANVAS_VIP_LEVEL)) if CANVAS_VIP_LEVEL.isdigit() else None
    if isinstance(entry, dict):
        multiplier = entry.get("multiplier")
    else:
        multiplier = None
    if multiplier in (None, 0, ""):
        return amount
    return Decimal(str(amount)) * Decimal(str(multiplier))


def apply_canvas_markup(model: str, amount: "object") -> "object":
    """零毛利模型统一加价（×1.5），其余按原价。"""
    from decimal import Decimal

    factor = _CANVAS_MARKUP_MODELS.get(model)
    if not factor or amount is None:
        return amount
    return Decimal(str(amount)) * Decimal(str(factor))


def fallback_price_from_table(price: Dict[str, Any], body: Dict[str, Any]) -> "object":
    """定价表里有价、但我们的估算函数不认的计价类型（复合媒体价 / 按字符），按描述里的单价算。"""
    import re
    from decimal import Decimal

    desc = str(price.get("price_description") or "")
    ptype = str(price.get("price_type") or "")
    try:
        if ptype == "composite_media_price":
            duration = float(body.get("duration") or body.get("video_length") or body.get("seconds") or 5)
            resolution = str(body.get("resolution") or body.get("quality") or "768P")
            rate = None
            for pattern in (rf"{re.escape(resolution)}\s*(\d+(?:\.\d+)?)\s*积分/秒",
                            r"(\d+(?:\.\d+)?)\s*积分/秒"):
                m = re.search(pattern, desc)
                if m:
                    rate = float(m.group(1))
                    break
            if rate is None:
                return Decimal("0")
            return Decimal(str(duration * rate))
        if ptype == "char_based":
            text = str(body.get("text") or body.get("prompt") or "")
            m = re.search(r"(\d+(?:\.\d+)?)\s*元/万字符", desc)
            if not m or not text:
                return Decimal("0")
            per_10k_yuan = float(m.group(1))
            return Decimal(str(len(text) / 10000.0 * per_10k_yuan * CREDITS_PER_YUAN))
    except Exception as exc:  # noqa: BLE001
        logger.info("[canvas] 兜底算价失败: %s", exc)
    return Decimal("0")


# 界面里一律显示我们的价：报价/估价响应里的这些价格字段，一律改写成我们的价
_PRICE_KEYS = {"credits", "credit", "estimated_credits", "total_credits", "estimate_credits",
               "points", "money", "price", "cost", "fee", "amount", "estimated_price", "total_price"}


def our_price_for_display(model: str, body: Dict[str, Any]) -> "object":
    """只用定价表算价（不查余额），供「界面显示」用；算不出返回 None。"""
    from decimal import Decimal

    from ..services.sutui_pricing import estimate_credits_from_pricing, fetch_model_pricing

    try:
        price = fetch_model_pricing(model)
    except Exception as exc:  # noqa: BLE001 取价失败不能让报价接口 500
        logger.info("[canvas] 取定价失败: %s", exc)
        return None
    if not price:
        return None
    version_price = version_base_price(model, body)
    if version_price is not None:
        return apply_vip_discount(price, version_price) if price.get("vip_discount") else version_price
    estimate = estimate_credits_from_pricing(price, body or {})
    if estimate and Decimal(str(estimate)) > 0:
        if price.get("vip_discount"):
            return apply_vip_discount(price, estimate)
        return apply_canvas_markup(model, estimate)
    base = fallback_price_from_table(price, body or {})
    if base and Decimal(str(base)) > 0:
        if price.get("vip_discount"):
            return apply_vip_discount(price, base)
        return base * Decimal(str(CANVAS_PRICE_MARKUP))
    return None


def rewrite_prices(node: Any, amount: "object") -> Any:
    """把报价响应里的价格字段递归改写成我们的价（其它字段不动）。"""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in _PRICE_KEYS and isinstance(value, (int, float)):
                out[key] = float(amount)
            else:
                out[key] = rewrite_prices(value, amount)
        return out
    if isinstance(node, list):
        return [rewrite_prices(item, amount) for item in node]
    return node



async def _catalog_response(normalized: str, db: Session) -> Response:
    """模型/音色/技能目录：读我们库里的缓存（必要时从 apiz 拉一次），价格字段换成我们的价。"""
    payload = await canvas_hub.cached_remote_json(db, normalized, _CATALOG_PATHS[normalized])
    if payload is None:
        return JSONResponse({"code": 200, "data": {"models": []}})

    data = payload.get("data") if isinstance(payload, dict) else None
    models = None
    if isinstance(data, dict) and isinstance(data.get("models"), list):
        models = data["models"]
    elif isinstance(payload, dict) and isinstance(payload.get("models"), list):
        models = payload["models"]

    if isinstance(models, list) and normalized != "api/v3/voices":
        for item in models:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "")
            ours = our_price_for_display(model_id, item) if model_id else None
            if ours:
                amount = float(ours)
                item["pricing_summary"] = {"from_price": amount, "price_label": "%s 积分" % amount}
                for key in ("price", "credits", "money"):
                    if key in item:
                        item[key] = amount
        logger.info("[canvas] 模型目录按我们定价返回（%d 个）", len(models))
    return JSONResponse(content=payload)



def _is_quote_path(path: str) -> bool:
    """报价/估价/预价类：一律由我们自己算，绝不请求 apiz。"""
    if "create" in path or "submit" in path:
        return False
    return any(hint in path for hint in ("quote", "estimate", "price", "pre_deduct", "precheck"))


def _local_quote_response(path: str, body: Dict[str, Any]) -> JSONResponse:
    """本地报价：按我们自己的定价表算，返回我们自己的价。"""
    model = _payload_model(body)
    price = our_price_for_display(model, body) if model else None
    amount = float(price) if price else 0.0
    logger.info("[canvas] 本地报价 %s model=%s -> %s", path, model, amount)
    inner: Dict[str, Any] = {
        "quote_available": True,
        "billing_mode": "prepaid",
        "currency": "points",
        "estimated_points": amount,
        "estimated_price": amount,
        "estimated_credits": amount,
        "total_credits": amount,
        "price": amount,
        "credits": amount,
        "money": amount,
        "points": amount,
        "model": model,
        "unit": "credits",
    }
    payload: Dict[str, Any] = {"code": 200, "msg": "ok", "quote_available": True,
                               "billing_mode": "prepaid", "currency": "points",
                               "estimated_points": amount, "price": amount,
                               "credits": amount, "points": amount, "data": inner}
    return JSONResponse(payload)


def estimate_our_price(db: Session, user: User, model: str, body: Dict[str, Any]) -> "object":
    """按**我们自己的定价表**估算这次要扣多少积分（余额不足直接 402）。

    定价来源：services/sutui_pricing（我们的定价表/文档表）+ 参数估算。
    没有定价的模型返回 0 —— 不猜价、不按上游报价扣。
    """
    from decimal import Decimal

    from ..services.sutui_billing_gate import assert_pricing_pre_deduct_allows_upstream_or_http

    from ..services.sutui_pricing import fetch_model_pricing

    try:
        estimate = assert_pricing_pre_deduct_allows_upstream_or_http(db, user, model, body, action_label="画布生成")
    except HTTPException as exc:
        if getattr(exc, "status_code", 0) == 402:
            raise  # 余额不足：明确挡住
        logger.info("[canvas] 该模型没有我们的定价，先不扣费: %s (%s)", model, getattr(exc, "detail", ""))
        return Decimal("0")

    price = fetch_model_pricing(model)
    version_price = version_base_price(model, body)
    if version_price is not None:
        charged = apply_vip_discount(price, version_price) if price else version_price
        logger.info("[canvas] %s 按版本价: base=%s -> 收 %s", model, version_price, charged)
        return charged
    if estimate and Decimal(str(estimate)) > 0:
        if price and price.get("vip_discount"):
            return apply_vip_discount(price, estimate)
        return apply_canvas_markup(model, estimate)

    # 估算不出价（复合媒体价 / 按字符）：按表里写明的单价 ×加价系数收
    if price:
        base = fallback_price_from_table(price, body)
        if base > 0:
            if price.get("vip_discount"):
                return apply_vip_discount(price, base)
            marked = base * Decimal(str(CANVAS_PRICE_MARKUP))
            logger.info("[canvas] %s 按表内单价兜底计价: base=%s ×%s = %s", model, base, CANVAS_PRICE_MARKUP, marked)
            return marked
    # token 后结算类（seedance 等）：按描述里的「x 积分/秒」× 时长 ×1.5 先收，不亏
    desc = str((price or {}).get("price_description") or "")
    import re as _re

    per_second = [float(x) for x in _re.findall(r"(\d+(?:\.\d+)?)\s*积分/秒", desc)]
    if per_second or (price or {}).get("price_type") == "token_postcharge":
        duration = float(body.get("duration") or body.get("video_length") or body.get("seconds") or 5)
        rate = max(per_second) if per_second else _TOKEN_MODEL_RATE_FALLBACK
        base = rate * duration
        factor = _CANVAS_MARKUP_MODELS.get(model, float(CANVAS_PRICE_MARKUP))
        logger.info("[canvas] %s token 类按秒价估算: %s 积分/秒 × %ss × %s = %s",
                    model, rate, duration, factor, Decimal(str(base)) * Decimal(str(factor)))
        return Decimal(str(base)) * Decimal(str(factor))
        logger.info("[canvas] %s 连兜底也算不出价（type=%s），本次不扣", model, price.get("price_type"))
    return Decimal("0")


def pre_deduct_canvas(db: Session, user: User, amount: "object", model: str, path: str) -> "object":
    """按我们的定价预扣（写 pre_deduct 流水）。任何内部异常都不影响生成，只是不扣。"""
    from decimal import Decimal

    from ..services.credit_ledger import append_credit_ledger
    from ..services.credits_amount import quantize_credits, user_balance_decimal

    try:
        amount = quantize_credits(amount or 0)
        if amount <= 0:
            return Decimal("0")
        db.refresh(user)
        balance = user_balance_decimal(user)
        if balance < amount:
            raise HTTPException(status_code=402, detail=f"积分不足：本次预扣 {amount}，当前余额 {balance}")
        user.credits = balance - amount
        append_credit_ledger(db, user.id, -amount, "pre_deduct", user.credits,
                             description=f"画布预扣：{model or path}", ref_type="canvas_api", ref_id=path,
                             meta={"model": model, "pre_estimated": float(amount)})
        db.commit()
        logger.info("[canvas] 预扣 user=%s model=%s 扣=%s 余额=%s", getattr(user, "id", ""), model, amount, user.credits)
        return amount
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[canvas] 预扣失败（不影响生成）: %s", exc, exc_info=True)
        return Decimal("0")


def refund_canvas(db: Session, user: User, amount: "object", model: str, path: str, reason: str) -> None:
    """生成失败/被拒：把预扣退回（写 refund 流水）。"""
    from decimal import Decimal

    from ..services.credit_ledger import append_credit_ledger
    from ..services.credits_amount import quantize_credits, user_balance_decimal

    try:
        amount = quantize_credits(amount or 0)
        if amount <= 0:
            return
        db.refresh(user)
        balance = user_balance_decimal(user)
        user.credits = balance + amount
        append_credit_ledger(db, user.id, amount, "refund", user.credits,
                             description=f"画布生成失败退回（{reason}）", ref_type="canvas_api", ref_id=path,
                             meta={"model": model, "refund": float(amount), "reason": reason})
        db.commit()
        logger.info("[canvas] 退回 user=%s model=%s 退=%s 余额=%s", getattr(user, "id", ""), model, amount, user.credits)
    except Exception as exc:
        logger.warning("[canvas] 退回失败: %s", exc, exc_info=True)


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
        # 我们自己的「R2」：上传打相对地址（本机代理带登录态），产物落在我们服务器、公开只读可取
        name = str(body.get("file_name") or request.query_params.get("name") or "file")
        key = hub.new_upload_key(uid, name)
        return JSONResponse({"code": 200, "data": {
            "upload_url": f"/canvas-api/api/upload?key={key}",
            "public_url": f"{hub.public_base()}{hub.PUBLIC_MEDIA_PREFIX}/{key}",
            "file_url": f"{hub.public_base()}{hub.PUBLIC_MEDIA_PREFIX}/{key}",
            "file_key": key,
            "content_type": str(body.get("content_type") or ""),
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

    # 报价/估价类：本地算，绝不发给 apiz（界面显示的就是我们的价）
    if _is_quote_path(normalized):
        return _local_quote_response(normalized, body_json)

    # 模型/音色/技能目录：本地缓存 + 我们的价
    if normalized in _CATALOG_PATHS:
        return await _catalog_response(normalized, db)

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
    pre_charged = None
    if model and _should_settle(normalized):
        # 按**我们自己的定价**预扣（余额不足 402 挡住）；没定价的模型返回 0，不猜价
        pre_charged = pre_deduct_canvas(db, user, estimate_our_price(db, user, model, body_json), model, normalized)

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
        # 成功：价格按我们自己的定价（前面已预扣），不再按 apiz 回报扣一次
        if pre_charged:
            logger.info("[canvas] 生成成功，按我们定价已扣 %s（model=%s）", pre_charged, model)
    if upstream.status_code >= 400 and pre_charged:
        refund_canvas(db, user, pre_charged, model, normalized, f"上游 {upstream.status_code}")
    logger.info("[canvas] user=%s %s %s -> %s", getattr(user, "id", ""), request.method, normalized, upstream.status_code)

    # 报价/估价：给界面看的价格一律换成我们的价（不出现 apiz 原本的价格）
    if (upstream.status_code < 400 and model
            and any(hint in normalized for hint in ("quote", "estimate", "price"))
            and "json" in (upstream.headers.get("content-type") or "")):
        display_price = our_price_for_display(model, body_json)
        if display_price:
            try:
                payload = json.loads(upstream.content.decode("utf-8", "replace"))
                rewritten = rewrite_prices(payload, display_price)
                logger.info("[canvas] 报价改写为我们定价: %s -> %s（%s）", model, display_price, normalized)
                return JSONResponse(content=rewritten, status_code=upstream.status_code)
            except Exception as exc:
                logger.warning("[canvas] 报价改写失败: %s", exc)

    media_type = upstream.headers.get("content-type") or "application/json"
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=media_type)


# 还没做（下一轮）：按消耗扣算力。现在生成类只做 key 中转，用户不扣积分、apiz 成本由服务器承担。
# 建议做法：任务在 apiz 侧完成后，响应里会带本次消耗（services/sutui_pricing.extract_upstream_reported_credits），
# 以 task_id 幂等结算一次（services/credit_ledger.append_credit_ledger），失败/取消要回滚。
