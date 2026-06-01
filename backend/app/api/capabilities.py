"""能力注册、调用日志与用户积分变更（与速推同扣）。

动账接口 /capabilities/pre-deduct、record-call、refund 的**唯一实现**在本模块；应由 **实际调用速推的 MCP**
invoke_capability 顺序触发，不在此之外再实现第二套扣费。
"""
import json
import logging
import os
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from .auth import get_current_user, brand_mark_for_jwt_claim
from ..models import BillingIdempotency, CapabilityCallLog, CapabilityConfig, User
from ..services.credit_ledger import append_credit_ledger
from ..services.credits_amount import credits_json_float, quantize_credits, user_balance_decimal
from ..services.daily_credit_limit import assert_daily_limit_allows
from ..services.sutui_api_audit import log_capability_call_log_persisted
from .installation_slots import installation_slots_enabled, parse_installation_id_strict
from .skills import user_can_use_capability

router = APIRouter()

_PRICING_JSON_PATH = Path(__file__).resolve().parent.parent.parent.parent / "comfly_pricing.json"
_HIFLY_TTS_CAPABILITY_ID = "hifly.video.create_by_tts"
_HIFLY_TTS_UNIT_CREDITS = 10
_HIFLY_TTS_CHARS_PER_SECOND = 4
_IMAGE_GENERATE_FIXED_PRICE_CREDITS = 60
_IMAGE_GENERATE_FIXED_PRICE_MODELS = frozenset(
    {
        "openai/gpt-image-2",
        "gpt-image2",
        "gpt-image-2",
        "gpt-image",
        "gptimage2",
    }
)
_GROK_IMAGINE_VIDEO_FIXED_PRICE_CREDITS = 320
_GROK_IMAGINE_VIDEO_MODELS = frozenset(
    {
        "xai/grok-imagine-video/text-to-video",
        "xai/grok-imagine-video/image-to-video",
    }
)
_PIPELINE_TOTAL_PREDEDUCT_CAPABILITIES = frozenset(
    {
        "goal.video.pipeline",
        "create.video.pipeline",
    }
)
_PIPELINE_DEFAULT_IMAGE_MODEL = "openai/gpt-image-2"
_PIPELINE_DEFAULT_GOAL_VIDEO_MODEL = "xai/grok-imagine-video/image-to-video"
_PIPELINE_DEFAULT_CREATE_VIDEO_MODEL = "fal-ai/veo3.1/image-to-video"


def _get_user_price_multiplier() -> float:
    """用户消耗 = 采购价 × 倍率。优先环境变量，其次 comfly_pricing.json，默认 3。"""
    env_val = os.environ.get("USER_PRICE_MULTIPLIER", "").strip()
    if env_val:
        try:
            return float(env_val)
        except ValueError:
            pass
    try:
        if _PRICING_JSON_PATH.exists():
            data = json.loads(_PRICING_JSON_PATH.read_text("utf-8"))
            return float(data.get("user_price_multiplier_default", 3))
    except Exception:
        pass
    return 3.0


def _installation_id_for_capability_checks(x_installation_id: Optional[str]) -> Optional[str]:
    """在线版槽位开启时校验并返回 installation_id；否则返回 None。"""
    if not installation_slots_enabled():
        return None
    return parse_installation_id_strict(x_installation_id)


def _should_deduct_credits() -> bool:
    """是否启用「调用能力时扣积分」（在线版 + 独立认证时）。"""
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    return edition == "online" and getattr(settings, "lobster_independent_auth", True)


def _normalize_model_id(model: Optional[str]) -> str:
    return (model or "").strip().lower()


def _payload_model_id(payload: Optional[dict]) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("model", "model_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _fixed_generate_user_price(
    capability_id: str,
    model: Optional[str],
    params: Optional[dict],
) -> Optional[tuple[Any, dict[str, Any]]]:
    mid = _normalize_model_id(model)
    if capability_id == "image.generate" and mid in _IMAGE_GENERATE_FIXED_PRICE_MODELS:
        meta: dict[str, Any] = {
            "billing_rule": "gpt_image_2_flat_60",
            "fixed_price_credits": _IMAGE_GENERATE_FIXED_PRICE_CREDITS,
        }
        if isinstance(params, dict):
            for key in ("image_size", "size", "resolution", "quality", "num_images", "n"):
                if key in params:
                    meta[f"requested_{key}"] = params.get(key)
        return quantize_credits(_IMAGE_GENERATE_FIXED_PRICE_CREDITS), meta
    if capability_id != "video.generate":
        return None
    if mid not in _GROK_IMAGINE_VIDEO_MODELS:
        return None
    meta: dict[str, Any] = {
        "billing_rule": "grok_imagine_video_flat_320",
        "fixed_price_credits": _GROK_IMAGINE_VIDEO_FIXED_PRICE_CREDITS,
    }
    if isinstance(params, dict):
        for key in ("duration", "duration_sec", "duration_seconds", "length", "video_length"):
            if key in params:
                meta["requested_duration"] = params.get(key)
                break
    return quantize_credits(_GROK_IMAGINE_VIDEO_FIXED_PRICE_CREDITS), meta


def _pipeline_positive_int(params: Optional[dict], key: str, default: int, *, min_value: int = 1, max_value: int = 20) -> int:
    if not isinstance(params, dict):
        return default
    try:
        value = int(params.get(key) or default)
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def _estimate_pipeline_total_user_price(
    capability_id: str,
    params: Optional[dict],
) -> Optional[tuple[Any, dict[str, Any]]]:
    """Estimate and charge the whole local video pipeline once.

    The local runner calls image.generate/video.generate internally with a
    pipeline-precharged marker, so the user-facing charge must be reserved at
    the pipeline entry instead of charging each sub-step.
    """
    if capability_id not in _PIPELINE_TOTAL_PREDEDUCT_CAPABILITIES:
        return None
    params = params if isinstance(params, dict) else {}
    source_mode = str(
        params.get("source_mode")
        or params.get("video_source_mode")
        or params.get("image_source")
        or params.get("first_frame_source")
        or ""
    ).strip().lower()
    uses_existing_image = source_mode in {
        "asset_random",
        "asset",
        "material",
        "reference",
        "reference_image",
        "resume_image",
        "resume_from_image",
        "existing_image",
    }
    if params.get("reference_asset_ids") or params.get("reference_image_urls"):
        uses_existing_image = True
    if params.get("resume_from_image"):
        uses_existing_image = True
    if capability_id == "goal.video.pipeline":
        scene_count = 1
        image_model = str(params.get("image_model") or _PIPELINE_DEFAULT_IMAGE_MODEL).strip()
        video_model = str(params.get("video_model") or _PIPELINE_DEFAULT_GOAL_VIDEO_MODEL).strip()
    else:
        scene_count = _pipeline_positive_int(params, "scene_count", 1, min_value=1, max_value=6)
        image_model = str(params.get("image_model") or _PIPELINE_DEFAULT_IMAGE_MODEL).strip()
        video_model = str(params.get("video_model") or _PIPELINE_DEFAULT_CREATE_VIDEO_MODEL).strip()
        if not uses_existing_image:
            if params.get("reference_asset_ids") or params.get("reference_image_urls"):
                uses_existing_image = True

    image_total = 0
    image_meta: dict[str, Any] = {}
    if not uses_existing_image:
        image_price = _fixed_generate_user_price("image.generate", image_model, params)
        if image_price is not None:
            image_total = int(image_price[0]) * scene_count
            image_meta = image_price[1]
        else:
            image_total = _IMAGE_GENERATE_FIXED_PRICE_CREDITS * scene_count
            image_meta = {"billing_rule": "pipeline_default_image_flat_60"}

    video_price = _fixed_generate_user_price("video.generate", video_model, params)
    if video_price is not None:
        per_video = int(video_price[0])
        video_meta = video_price[1]
    else:
        try:
            from ..services.sutui_pricing import estimate_credits_from_pricing, fetch_model_pricing

            pricing = fetch_model_pricing(video_model)
            if not pricing:
                raise RuntimeError("pricing not found")
            per_video = int(
                quantize_credits(
                    float(estimate_credits_from_pricing(pricing, params))
                    * _get_user_price_multiplier()
                )
            )
            if per_video <= 0:
                raise RuntimeError("pricing estimated zero")
            video_meta = {"billing_rule": "pipeline_video_pricing_docs"}
        except Exception:
            # Keep pipeline entry conservative and deterministic if docs lookup is
            # temporarily unavailable. The older behavior charged later substeps;
            # now we need a single reservation before starting.
            per_video = _GROK_IMAGINE_VIDEO_FIXED_PRICE_CREDITS
            video_meta = {"billing_rule": "pipeline_video_fallback_320"}

    total = quantize_credits(image_total + per_video * scene_count)
    meta = {
        "billing_rule": "pipeline_total_pre_deduct",
        "capability_id": capability_id,
        "scene_count": scene_count,
        "source_mode": source_mode or ("asset_random" if uses_existing_image else "ai_image"),
        "uses_existing_image": uses_existing_image,
        "image_model": image_model,
        "video_model": video_model,
        "image_subtotal": image_total,
        "video_per_call": per_video,
        "video_subtotal": per_video * scene_count,
        "total": credits_json_float(total),
    }
    if image_meta:
        meta["image_billing_rule"] = image_meta.get("billing_rule")
    if video_meta:
        meta["video_billing_rule"] = video_meta.get("billing_rule")
    return total, meta


def _require_sutui_brand_for_billing(user: User, *, upstream: str) -> None:
    """速推上游计费时 JWT 须为 bihuo/yingshi；无品牌或非两池不允许预扣/按次扣费。"""
    if not _should_deduct_credits():
        return
    if (upstream or "").strip() != "sutui":
        return
    bm = brand_mark_for_jwt_claim(getattr(user, "brand_mark", None))
    if bm not in ("bihuo", "yingshi"):
        raise HTTPException(
            status_code=403,
            detail=(
                "账号未绑定必火/影视品牌，无法使用速推算力；无通用兜底。"
                "请使用对应品牌客户端注册或联系管理员补全品牌后重新登录。"
            ),
        )


def _billing_request_may_mutate_balance(request: Request) -> bool:
    """
    仅本机 MCP（直连 Backend 的 127.0.0.1/::1）或携带 X-Lobster-Mcp-Billing 与 LOBSTER_MCP_BILLING_INTERNAL_KEY 一致时，
    对 pre-deduct / record-call / refund 做实质积分变更。
    在线独立认证且需扣费时：不满足上述条件则返回 403，禁止「未扣费即生成」。
    """
    k = (getattr(settings, "lobster_mcp_billing_internal_key", None) or "").strip()
    h = (request.headers.get("X-Lobster-Mcp-Billing") or "").strip()
    ch = getattr(request.client, "host", None) or ""
    loopback = ch in ("127.0.0.1", "::1", "localhost")
    if k:
        return h == k or loopback
    return loopback


_CAPABILITY_CATALOG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "mcp" / "capability_catalog.json"
_CAPABILITY_CATALOG_LOCAL_PATH = Path(__file__).resolve().parent.parent.parent.parent / "mcp" / "capability_catalog.local.json"


def _read_capability_catalog_json() -> dict:
    """Read mcp/capability_catalog.json (+ optional .local overlay), same merge logic as MCP."""
    catalog = {}
    try:
        if _CAPABILITY_CATALOG_PATH.exists():
            catalog = json.loads(_CAPABILITY_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    if _CAPABILITY_CATALOG_LOCAL_PATH.exists():
        try:
            local = json.loads(_CAPABILITY_CATALOG_LOCAL_PATH.read_text(encoding="utf-8"))
            catalog.update(local)
        except Exception:
            pass
    return catalog


@router.get("/api/capability-catalog", summary="完整能力目录（供客户端 MCP 动态拉取，取代本地 capability_catalog.json）")
def get_capability_catalog(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_installation_id: Optional[str] = Header(None, alias="X-Installation-Id"),
):
    """Return the full capability catalog from mcp/capability_catalog.json,
    filtered by user permissions (skill visibility + paid unlock).
    Client MCP should call this endpoint to load capabilities instead of
    reading a local JSON file. Falls back gracefully if the file is missing."""
    iid = _installation_id_for_capability_checks(x_installation_id)
    catalog = _read_capability_catalog_json()
    filtered = {}
    for cap_id, cap_def in catalog.items():
        if not cap_def.get("enabled", True):
            continue
        if not user_can_use_capability(db, current_user.id, cap_id, iid):
            continue
        filtered[cap_id] = cap_def
    return {"capabilities": filtered, "source": "server"}


@router.get("/capabilities/available", summary="当前可用能力列表（含付费技能限制：未解锁的付费技能不会出现在列表中）")
def list_available(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_installation_id: Optional[str] = Header(None, alias="X-Installation-Id"),
):
    iid = _installation_id_for_capability_checks(x_installation_id)
    rows = db.query(CapabilityConfig).filter(CapabilityConfig.enabled.is_(True)).order_by(CapabilityConfig.capability_id).all()
    out = []
    for r in rows:
        if not user_can_use_capability(db, current_user.id, r.capability_id, iid):
            continue
        out.append({
            "capability_id": r.capability_id,
            "description": r.description,
            "upstream": r.upstream,
            "upstream_tool": r.upstream_tool,
            "arg_schema": r.arg_schema,
            "extra_config": getattr(r, "extra_config", None),
            "is_default": r.is_default,
            "unit_credits": r.unit_credits,
        })
    return {"capabilities": out}


@router.get("/capabilities/registry", summary="能力注册列表（需登录）")
def list_registry(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(CapabilityConfig).order_by(CapabilityConfig.capability_id).all()
    return [
        {
            "capability_id": r.capability_id,
            "description": r.description,
            "upstream": r.upstream,
            "upstream_tool": r.upstream_tool,
            "enabled": r.enabled,
            "is_default": r.is_default,
            "unit_credits": r.unit_credits,
            "extra_config": getattr(r, "extra_config", None),
        }
        for r in rows
    ]


class RecordCallIn(BaseModel):
    capability_id: str
    success: bool = True
    latency_ms: Optional[int] = None
    request_payload: Optional[dict] = None
    response_payload: Optional[dict] = None
    error_message: Optional[str] = None
    source: str = "mcp_invoke"
    chat_session_id: Optional[str] = None
    chat_context_id: Optional[str] = None
    """若由 pre-deduct 已扣过，传本次扣费数；pre_deduct_applied=True 时不在本接口再次减余额。"""
    credits_charged: Optional[float] = None
    pre_deduct_applied: bool = False
    """预扣金额（与 credits_charged 在预扣成功时一致）；与 credits_final 联用做差额结算。"""
    credits_pre_deducted: Optional[float] = None
    """速推返回的实际消耗积分；与预扣差额多退少补。"""
    credits_final: Optional[float] = None
    """站内对账：本次上游选用的池与 token 指纹（仅 MCP 本机计费传入；不入用户 API）。"""
    sutui_pool: Optional[str] = None
    sutui_token_ref: Optional[str] = None


class PreDeductIn(BaseModel):
    capability_id: str
    model: Optional[str] = None
    params: Optional[dict] = None
    sutui_pool: Optional[str] = None
    sutui_token_ref: Optional[str] = None
    force_credits: Optional[float] = None
    dry_run: bool = False


def _sutui_recon_for_ledger(
    request: Request,
    *,
    upstream: str,
    sutui_pool: Optional[str],
    sutui_token_ref: Optional[str],
) -> dict:
    """写入 meta._recon；仅本机 MCP 计费且 upstream 为 sutui 时采纳。"""
    if not _billing_request_may_mutate_balance(request):
        return {}
    if (upstream or "").strip() != "sutui":
        return {}
    sp = (sutui_pool or "").strip()
    ref = (sutui_token_ref or "").strip()
    if not sp or not ref:
        return {}
    return {"_recon": {"sutui_pool": sp, "sutui_token_ref": ref}}


def _billing_idempotency_key(request: Request) -> str:
    return (
        (request.headers.get("X-Billing-Idempotency-Key") or request.headers.get("X-Idempotency-Key") or "")
        .strip()[:128]
    )


def _pre_deduct_idempotent_cached(
    db: Session, user_id: int, idem_key: str
) -> Optional[dict]:
    if not idem_key:
        return None
    row = (
        db.query(BillingIdempotency)
        .filter(
            BillingIdempotency.user_id == user_id,
            BillingIdempotency.key == idem_key,
            BillingIdempotency.endpoint == "pre_deduct",
        )
        .first()
    )
    if not row:
        return None
    if datetime.utcnow() - row.created_at > timedelta(minutes=10):
        return None
    try:
        return json.loads(row.response_json)
    except Exception:
        return None


def _pre_deduct_idempotent_store(db: Session, user_id: int, idem_key: str, payload: dict) -> None:
    if not idem_key:
        return
    try:
        db.add(
            BillingIdempotency(
                user_id=user_id,
                key=idem_key,
                endpoint="pre_deduct",
                response_json=json.dumps(payload, ensure_ascii=False),
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
    except Exception:
        db.rollback()


@router.post("/capabilities/pre-deduct", summary="调用能力前预扣积分（不足返回 402）")
def pre_deduct(
    body: PreDeductIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_installation_id: Optional[str] = Header(None, alias="X-Installation-Id"),
):
    idem_key = _billing_idempotency_key(request)
    if body.dry_run:
        iid = (x_installation_id or "").strip() or None
    else:
        iid = _installation_id_for_capability_checks(x_installation_id)
    if not user_can_use_capability(db, current_user.id, body.capability_id, iid):
        raise HTTPException(
            status_code=403,
            detail="该能力属于付费技能，请先在技能商店付费解锁后再使用。",
        )
    if not _should_deduct_credits():
        return {"credits_charged": 0, "message": "未启用积分扣减"}
    if not body.dry_run and not _billing_request_may_mutate_balance(request):
        raise HTTPException(
            status_code=403,
            detail=(
                "计费请求来源未受信任（非本机回环且未携带有效 X-Lobster-Mcp-Billing），"
                "拒绝预扣以免未扣费即调用上游。请为 MCP/网关配置与认证中心一致的 LOBSTER_MCP_BILLING_INTERNAL_KEY，"
                "并在请求认证中心 /capabilities/pre-deduct（及 record-call、refund）时带上请求头 X-Lobster-Mcp-Billing。"
            ),
        )
    if idem_key:
        cached = _pre_deduct_idempotent_cached(db, current_user.id, idem_key)
        if cached is not None:
            return cached
    cap = db.query(CapabilityConfig).filter(CapabilityConfig.capability_id == body.capability_id).first()
    upstream = (cap.upstream or "").strip() if cap else ""
    upstream_tool = (cap.upstream_tool or "").strip() if cap else ""
    _require_sutui_brand_for_billing(current_user, upstream=upstream)

    pipeline_total_price = _estimate_pipeline_total_user_price(body.capability_id, body.params)
    if pipeline_total_price is not None:
        est_d, pipeline_meta = pipeline_total_price
        if body.dry_run:
            return {
                "credits_charged": credits_json_float(est_d),
                "dry_run": True,
                "billing_rule": pipeline_meta.get("billing_rule"),
                "breakdown": pipeline_meta,
            }
        db.refresh(current_user)
        if user_balance_decimal(current_user) < est_d:
            raise HTTPException(
                status_code=402,
                detail=f"积分不足：本次流水线需 {float(est_d)} 积分，当前余额 {float(user_balance_decimal(current_user))}。请先充值。",
            )
        assert_daily_limit_allows(db, current_user.id, est_d, action_label="本次创意成片")
        current_user.credits = user_balance_decimal(current_user) - est_d
        bal = quantize_credits(current_user.credits)
        append_credit_ledger(
            db,
            current_user.id,
            -est_d,
            "pre_deduct",
            bal,
            description="流水线总预扣",
            ref_type="capability",
            meta=pipeline_meta,
        )
        db.commit()
        out = {
            "credits_charged": credits_json_float(est_d),
            "billing_rule": pipeline_meta.get("billing_rule"),
            "breakdown": pipeline_meta,
        }
        _pre_deduct_idempotent_store(db, current_user.id, idem_key, out)
        return out

    # ── force_credits: MCP 已算好金额（Comfly 路由等场景）──
    if (
        body.force_credits is not None
        and body.force_credits > 0
        and _fixed_generate_user_price(
            body.capability_id,
            body.model,
            body.params if isinstance(body.params, dict) else None,
        )
        is None
    ):
        fc = quantize_credits(body.force_credits)
        if body.dry_run:
            return {"credits_charged": credits_json_float(fc), "dry_run": True, "model": body.model or ""}
        db.refresh(current_user)
        if user_balance_decimal(current_user) < fc:
            raise HTTPException(
                status_code=402,
                detail=f"积分不足：本次需 {float(fc)} 积分，当前余额 {float(user_balance_decimal(current_user))}。请先充值。",
            )
        assert_daily_limit_allows(db, current_user.id, fc, action_label="本次生成")
        current_user.credits = user_balance_decimal(current_user) - fc
        bal = quantize_credits(current_user.credits)
        _recon_fc = _sutui_recon_for_ledger(
            request,
            upstream="comfly",
            sutui_pool=body.sutui_pool,
            sutui_token_ref=body.sutui_token_ref,
        )
        append_credit_ledger(
            db,
            current_user.id,
            -fc,
            "pre_deduct",
            bal,
            description="预扣",
            ref_type="capability",
            meta={
                **(_recon_fc or {}),
                "capability_id": body.capability_id,
                "model": body.model or "",
                "pre_estimated": credits_json_float(fc),
                "upstream": "comfly",
            },
        )
        db.commit()
        out = {"credits_charged": credits_json_float(fc)}
        _pre_deduct_idempotent_store(db, current_user.id, idem_key, out)
        return out

    _UNDERSTAND_CAPS = ("image.understand", "video.understand")
    if body.capability_id == _HIFLY_TTS_CAPABILITY_ID:
        params = body.params if isinstance(body.params, dict) else {}
        raw_seconds = params.get("estimated_seconds")
        if raw_seconds in (None, ""):
            text = str(params.get("text") or "")
            raw_seconds = max(1, int(math.ceil(len("".join(text.split())) / _HIFLY_TTS_CHARS_PER_SECOND)))
        try:
            seconds = max(1, int(math.ceil(float(raw_seconds or 1))))
        except (TypeError, ValueError):
            seconds = 1
        est_d = quantize_credits(seconds * _HIFLY_TTS_UNIT_CREDITS)
        if body.dry_run:
            return {
                "credits_charged": credits_json_float(est_d),
                "dry_run": True,
                "model": body.model or "hifly-text-driven",
                "estimated_seconds": seconds,
            }
        db.refresh(current_user)
        if user_balance_decimal(current_user) < est_d:
            raise HTTPException(
                status_code=402,
                detail=f"积分不足：本次需 {float(est_d)} 积分，当前余额 {float(user_balance_decimal(current_user))}。请先充值。",
            )
        assert_daily_limit_allows(db, current_user.id, est_d, action_label="本次生成")
        current_user.credits = user_balance_decimal(current_user) - est_d
        bal = quantize_credits(current_user.credits)
        append_credit_ledger(
            db,
            current_user.id,
            -est_d,
            "pre_deduct",
            bal,
            description="预扣",
            ref_type="capability",
            meta={
                "capability_id": body.capability_id,
                "model": body.model or "hifly-text-driven",
                "estimated_seconds": seconds,
                "unit_credits": _HIFLY_TTS_UNIT_CREDITS,
            },
        )
        db.commit()
        out = {"credits_charged": credits_json_float(est_d)}
        _pre_deduct_idempotent_store(db, current_user.id, idem_key, out)
        return out

    if upstream == "sutui" and upstream_tool == "generate" and body.capability_id not in _UNDERSTAND_CAPS:
        model = (body.model or "").strip()
        if not model:
            raise HTTPException(
                status_code=400,
                detail="调用生成能力时必须提供 model 以按速推定价预扣积分。",
            )
        params = body.params if isinstance(body.params, dict) else None
        fixed_price = _fixed_generate_user_price(body.capability_id, model, params)
        if fixed_price is not None:
            est_d, fixed_meta = fixed_price
            if body.dry_run:
                return {
                    "credits_charged": credits_json_float(est_d),
                    "dry_run": True,
                    "model": model,
                    "billing_rule": fixed_meta.get("billing_rule"),
                }
            db.refresh(current_user)
            if user_balance_decimal(current_user) < est_d:
                raise HTTPException(
                    status_code=402,
                    detail=f"积分不足：本次需 {float(est_d)} 积分，当前余额 {float(user_balance_decimal(current_user))}。请先充值。",
                )
            assert_daily_limit_allows(db, current_user.id, est_d, action_label="本次生成")
            current_user.credits = user_balance_decimal(current_user) - est_d
            bal = quantize_credits(current_user.credits)
            _recon = _sutui_recon_for_ledger(
                request,
                upstream=upstream,
                sutui_pool=body.sutui_pool,
                sutui_token_ref=body.sutui_token_ref,
            )
            append_credit_ledger(
                db,
                current_user.id,
                -est_d,
                "pre_deduct",
                bal,
                description="预扣",
                ref_type="capability",
                meta={
                    **(_recon or {}),
                    **fixed_meta,
                    "capability_id": body.capability_id,
                    "model": model,
                    "pre_estimated": credits_json_float(est_d),
                    "price_multiplier": 1.0,
                },
            )
            db.commit()
            out = {"credits_charged": credits_json_float(est_d), "billing_rule": fixed_meta.get("billing_rule")}
            _pre_deduct_idempotent_store(db, current_user.id, idem_key, out)
            return out

        from ..services.sutui_billing_gate import assert_pricing_pre_deduct_allows_upstream_or_http

        if body.dry_run:
            _multiplier = _get_user_price_multiplier()
            sutui_user_price = None
            try:
                _s = assert_pricing_pre_deduct_allows_upstream_or_http(
                    db, current_user, model, params, action_label="素材生成",
                )
                sutui_user_price = float(_s) * _multiplier
            except Exception:
                pass
            try:
                from mcp.comfly_upstream import estimate_comfly_credits, should_route_to_comfly
                sutui_purchase_price = None
                if sutui_user_price is not None and _multiplier > 0:
                    sutui_purchase_price = float(sutui_user_price) / _multiplier
                if should_route_to_comfly(body.capability_id, model, sutui_price=sutui_purchase_price):
                    comfly_user_price = estimate_comfly_credits(model, body.params or {}, for_user=True)
                    if comfly_user_price is not None and comfly_user_price > 0:
                        est_final = quantize_credits(comfly_user_price)
                        return {"credits_charged": credits_json_float(est_final), "dry_run": True, "model": model}
            except Exception:
                pass
            if sutui_user_price is not None and sutui_user_price > 0:
                est_final = quantize_credits(sutui_user_price)
                return {"credits_charged": credits_json_float(est_final), "dry_run": True, "model": model}
            return {"credits_charged": 0, "dry_run": True, "model": model}

        est_d = assert_pricing_pre_deduct_allows_upstream_or_http(
            db,
            current_user,
            model,
            params,
            action_label="素材生成",
        )
        _multiplier = _get_user_price_multiplier()
        est_d = quantize_credits(float(est_d) * _multiplier)
        db.refresh(current_user)
        if user_balance_decimal(current_user) < est_d:
            raise HTTPException(
                status_code=402,
                detail=f"积分不足：本次需 {float(est_d)} 积分（模型估价×{_multiplier:.0f}），当前余额 {float(user_balance_decimal(current_user))}。请先充值。",
            )
        assert_daily_limit_allows(db, current_user.id, est_d, action_label="本次生成")
        current_user.credits = user_balance_decimal(current_user) - est_d
        bal = quantize_credits(current_user.credits)
        _recon = _sutui_recon_for_ledger(
            request,
            upstream=upstream,
            sutui_pool=body.sutui_pool,
            sutui_token_ref=body.sutui_token_ref,
        )
        append_credit_ledger(
            db,
            current_user.id,
            -est_d,
            "pre_deduct",
            bal,
            description="预扣",
            ref_type="capability",
            meta={
                **(_recon or {}),
                "capability_id": body.capability_id,
                "model": model,
                "pre_estimated": credits_json_float(est_d),
                "price_multiplier": _multiplier,
            },
        )
        db.commit()
        out = {"credits_charged": credits_json_float(est_d)}
        _pre_deduct_idempotent_store(db, current_user.id, idem_key, out)
        return out

    # ── 爆款TVC 整包流水线 dry_run 总价估算（chat 算力提醒用）──
    # 客户端 chat 在调 invoke_capability(comfly.daihuo.pipeline) 前会先做 dry_run，
    # 这里按 N×image_model + N×video_model + 1×chat_model 累加，与实际 pipeline 调用次数一致。
    # 兼容老 capability_id "comfly.veo.daihuo_pipeline" → "comfly.daihuo.pipeline"
    if body.dry_run and body.capability_id in ("comfly.daihuo.pipeline", "comfly.veo.daihuo_pipeline"):
        try:
            from mcp.comfly_upstream import estimate_comfly_credits as _est
            params = body.params if isinstance(body.params, dict) else {}
            try:
                storyboard_count = int(params.get("storyboard_count") or 5)
            except (TypeError, ValueError):
                storyboard_count = 5
            storyboard_count = max(1, min(storyboard_count, 20))  # safety bound
            video_model = (params.get("video_model") or "xai/grok-imagine-video/image-to-video").strip()
            image_model = (params.get("image_model") or "nano-banana-2").strip()
            analysis_model = (params.get("analysis_model") or "gemini-2.5-pro").strip()

            per_image = _est(image_model, {}, for_user=True) or 0
            per_video = _est(video_model, {}, for_user=True) or 0
            chat_est = _est(analysis_model, {}, for_user=True) or 0
            total = storyboard_count * per_image + storyboard_count * per_video + chat_est
            est_d = quantize_credits(int(total))
            return {
                "credits_charged": credits_json_float(est_d),
                "dry_run": True,
                "model": video_model,
                "breakdown": {
                    "storyboard_count": storyboard_count,
                    "image_model": image_model,
                    "image_per_call": per_image,
                    "image_subtotal": storyboard_count * per_image,
                    "video_model": video_model,
                    "video_per_call": per_video,
                    "video_subtotal": storyboard_count * per_video,
                    "analysis_model": analysis_model,
                    "analysis_estimate": chat_est,
                    "total": int(total),
                },
            }
        except Exception as _e:
            logger.warning("[pre-deduct] daihuo_pipeline dry_run 估算失败: %s", _e)

    # ── Comfly 定价查找（dry_run 兜底：MCP 尚未介入时用 comfly_pricing.json 估价）──
    if body.dry_run and (body.model or "").strip():
        try:
            from mcp.comfly_upstream import estimate_comfly_credits, should_route_to_comfly
            _model_for_comfly = (body.model or "").strip()
            _cm_est = (
                estimate_comfly_credits(_model_for_comfly, body.params or {}, for_user=True)
                if should_route_to_comfly(body.capability_id, _model_for_comfly)
                else None
            )
            if _cm_est is not None:
                return {
                    "credits_charged": credits_json_float(quantize_credits(_cm_est)),
                    "dry_run": True,
                    "model": body.model,
                }
        except Exception:
            pass

    unit_credits = int(cap.unit_credits or 0) if cap else 0
    if unit_credits <= 0:
        return {"credits_charged": 0}
    if body.dry_run:
        return {"credits_charged": 0, "dry_run": True, "model": body.model or ""}
    db.refresh(current_user)
    uc = quantize_credits(unit_credits)
    if user_balance_decimal(current_user) < uc:
        raise HTTPException(
            status_code=402,
            detail=f"积分不足：本次需 {unit_credits} 积分，当前余额 {user_balance_decimal(current_user)}。请先充值。",
        )
    assert_daily_limit_allows(db, current_user.id, uc, action_label="本次调用")
    current_user.credits = user_balance_decimal(current_user) - uc
    bal = quantize_credits(current_user.credits)
    _recon_u = _sutui_recon_for_ledger(
        request,
        upstream=upstream,
        sutui_pool=body.sutui_pool,
        sutui_token_ref=body.sutui_token_ref,
    )
    append_credit_ledger(
        db,
        current_user.id,
        -uc,
        "pre_deduct",
        bal,
        description="预扣",
        ref_type="capability",
        meta={
            **(_recon_u or {}),
            "capability_id": body.capability_id,
            "unit_credits": unit_credits,
        },
    )
    db.commit()
    out = {"credits_charged": unit_credits}
    _pre_deduct_idempotent_store(db, current_user.id, idem_key, out)
    return out


class RefundIn(BaseModel):
    capability_id: str
    credits: float
    sutui_pool: Optional[str] = None
    sutui_token_ref: Optional[str] = None


@router.post("/capabilities/refund", summary="调用失败时退还预扣积分")
def refund_credits(
    body: RefundIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _should_deduct_credits() or body.credits <= 0:
        return {"ok": True}
    if not _billing_request_may_mutate_balance(request):
        raise HTTPException(
            status_code=403,
            detail=(
                "退费请求来源未受信任，拒绝执行以免账务不一致。"
                "请配置 LOBSTER_MCP_BILLING_INTERNAL_KEY 并转发 X-Lobster-Mcp-Billing。"
            ),
        )
    db.refresh(current_user)
    refund_amt = quantize_credits(body.credits)
    current_user.credits = user_balance_decimal(current_user) + refund_amt
    bal = quantize_credits(current_user.credits)
    _recon_rf = _sutui_recon_for_ledger(
        request,
        upstream="sutui",
        sutui_pool=body.sutui_pool,
        sutui_token_ref=body.sutui_token_ref,
    )
    append_credit_ledger(
        db,
        current_user.id,
        refund_amt,
        "refund",
        bal,
        description="退款",
        ref_type="capability",
        meta={
            **(_recon_rf or {}),
            "capability_id": body.capability_id,
            "refund_credits": float(refund_amt),
        },
    )
    db.commit()
    return {"ok": True, "refunded": float(refund_amt)}


@router.post("/capabilities/record-call", summary="记录能力调用（独立认证时按 unit_credits 扣积分，或使用 pre-deduct 已扣数量）")
def record_call(
    body: RecordCallIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_installation_id: Optional[str] = Header(None, alias="X-Installation-Id"),
):
    iid = _installation_id_for_capability_checks(x_installation_id)
    if not user_can_use_capability(db, current_user.id, body.capability_id, iid):
        raise HTTPException(
            status_code=403,
            detail="该能力属于付费技能，请先在技能商店付费解锁后再使用。",
        )
    if _should_deduct_credits() and not _billing_request_may_mutate_balance(request):
        raise HTTPException(
            status_code=403,
            detail=(
                "结算请求来源未受信任（非本机回环且未携带有效 X-Lobster-Mcp-Billing），"
                "拒绝记录调用/扣费以免未记账即完成生成。请配置 LOBSTER_MCP_BILLING_INTERNAL_KEY 并转发 X-Lobster-Mcp-Billing。"
            ),
        )
    cap = db.query(CapabilityConfig).filter(CapabilityConfig.capability_id == body.capability_id).first()
    upstream_rc = (getattr(cap, "upstream", None) or "").strip() if cap else ""
    _require_sutui_brand_for_billing(current_user, upstream=upstream_rc)
    unit_credits = int(cap.unit_credits or 0) if cap else 0
    credits_charged_body = quantize_credits(body.credits_charged if body.credits_charged is not None else 0)
    pre_applied = bool(getattr(body, "pre_deduct_applied", False))
    credits_final = getattr(body, "credits_final", None)
    credits_pre_deducted = getattr(body, "credits_pre_deducted", None)
    record_model = _payload_model_id(body.request_payload) or _payload_model_id(body.response_payload)
    fixed_record_price = _fixed_generate_user_price(
        body.capability_id,
        record_model,
        body.request_payload if isinstance(body.request_payload, dict) else None,
    )
    pipeline_record_price = _estimate_pipeline_total_user_price(
        body.capability_id,
        body.request_payload if isinstance(body.request_payload, dict) else None,
    )

    credits_charged = quantize_credits(0)
    db.refresh(current_user)
    balance_before = user_balance_decimal(current_user)
    ledger_kind: Optional[str] = None
    ledger_meta: Optional[dict] = None

    if _should_deduct_credits() and pre_applied and credits_final is not None:
        pre = quantize_credits(credits_pre_deducted if credits_pre_deducted is not None else credits_charged_body)
        upstream_final = quantize_credits(credits_final)
        final = upstream_final
        fixed_meta = None
        if fixed_record_price is not None:
            final, fixed_meta = fixed_record_price
        elif pipeline_record_price is not None:
            final, fixed_meta = pipeline_record_price
        delta = final - pre
        if delta > 0 and user_balance_decimal(current_user) < delta:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"积分不足：速推实际扣费 {final}，预扣 {pre}，需补扣 {delta} 积分，"
                    f"当前余额 {user_balance_decimal(current_user)}。请先充值。"
                ),
            )
        if delta > 0:
            assert_daily_limit_allows(db, current_user.id, delta, action_label="本次结算补扣")
        current_user.credits = user_balance_decimal(current_user) - delta
        credits_charged = final
        ledger_kind = "settle"
        ledger_meta = {
            "capability_id": body.capability_id,
            "pre_deducted": credits_json_float(pre),
            "final": credits_json_float(final),
            "delta_settle": credits_json_float(-delta),
        }
        if fixed_meta is not None:
            ledger_meta.update(fixed_meta)
            ledger_meta["upstream_reported_final"] = credits_json_float(upstream_final)
    elif credits_charged_body > 0 and _should_deduct_credits() and not pre_applied:
        charge_multiplier = 1.0
        if upstream_rc == "sutui" and (cap.upstream_tool if cap else None) == "generate":
            charge_multiplier = _get_user_price_multiplier()
        raw_upstream_charge = credits_charged_body
        charge_to_user = quantize_credits(float(raw_upstream_charge) * charge_multiplier)
        fixed_meta = None
        if fixed_record_price is not None:
            charge_to_user, fixed_meta = fixed_record_price
            charge_multiplier = 1.0
        elif pipeline_record_price is not None:
            charge_to_user, fixed_meta = pipeline_record_price
            charge_multiplier = 1.0
        if user_balance_decimal(current_user) < charge_to_user:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"积分不足：本次需 {charge_to_user} 积分"
                    f"（速推返回消耗 {raw_upstream_charge} * 倍率 {charge_multiplier}），"
                    f"当前余额 {user_balance_decimal(current_user)}。请先充值。"
                ),
            )
        assert_daily_limit_allows(db, current_user.id, charge_to_user, action_label="本次调用")
        current_user.credits = user_balance_decimal(current_user) - charge_to_user
        credits_charged = charge_to_user
        ledger_kind = "direct_charge"
        ledger_meta = {
            "capability_id": body.capability_id,
            "upstream_reported_credits": credits_json_float(raw_upstream_charge),
            "price_multiplier": charge_multiplier,
            "credits_charged": credits_json_float(charge_to_user),
        }
        if fixed_meta is not None:
            ledger_meta.update(fixed_meta)
    elif credits_charged_body == 0 and _should_deduct_credits() and not pre_applied and unit_credits > 0:
        uc = quantize_credits(unit_credits)
        if user_balance_decimal(current_user) < uc:
            raise HTTPException(
                status_code=402,
                detail=f"积分不足：本次需 {unit_credits} 积分，当前余额 {user_balance_decimal(current_user)}。请先充值。",
            )
        assert_daily_limit_allows(db, current_user.id, uc, action_label="本次调用")
        current_user.credits = user_balance_decimal(current_user) - uc
        credits_charged = uc
        ledger_kind = "unit_charge"
        ledger_meta = {"capability_id": body.capability_id, "unit_credits": unit_credits}
    elif pre_applied and credits_final is None and credits_charged_body > 0:
        if fixed_record_price is not None:
            credits_charged = fixed_record_price[0]
        elif pipeline_record_price is not None:
            credits_charged = pipeline_record_price[0]
        else:
            credits_charged = credits_charged_body
    log = CapabilityCallLog(
        user_id=current_user.id,
        capability_id=body.capability_id,
        upstream=cap.upstream if cap else None,
        upstream_tool=cap.upstream_tool if cap else None,
        success=body.success,
        credits_charged=credits_charged,
        latency_ms=body.latency_ms,
        request_payload=body.request_payload,
        response_payload=body.response_payload,
        error_message=(body.error_message or "")[:1000] or None,
        source=body.source,
        chat_session_id=(body.chat_session_id or "")[:128] or None,
        chat_context_id=(body.chat_context_id or "")[:128] or None,
    )
    db.add(log)
    db.flush()
    balance_after = quantize_credits(current_user.credits)
    ldelta = balance_after - balance_before
    if ledger_kind:
        _recon_r = _sutui_recon_for_ledger(
            request,
            upstream=upstream_rc,
            sutui_pool=body.sutui_pool,
            sutui_token_ref=body.sutui_token_ref,
        )
        append_credit_ledger(
            db,
            current_user.id,
            ldelta,
            ledger_kind,
            balance_after,
            description="结算" if ledger_kind == "settle" else "扣费",
            ref_type="capability_call_log",
            ref_id=str(log.id),
            meta={
                **(_recon_r or {}),
                **(ledger_meta or {}),
                "success": body.success,
                "credits_charged": credits_json_float(credits_charged),
            },
        )
    db.commit()
    db.refresh(log)
    try:
        req_sum = body.request_payload
        if isinstance(req_sum, dict) and len(json.dumps(req_sum, default=str)) > 8000:
            req_sum = {k: req_sum.get(k) for k in ("capability_id", "model", "task_id", "payload") if k in req_sum}
        log_capability_call_log_persisted(
            log_id=log.id,
            user_id=current_user.id,
            capability_id=log.capability_id or "",
            credits_charged=credits_charged,
            success=bool(body.success),
            source=(body.source or "")[:64],
            request_summary=req_sum,
            error_message=log.error_message,
        )
    except Exception:
        pass
    return {
        "id": log.id,
        "capability_id": log.capability_id,
        "success": log.success,
        "credits_charged": credits_json_float(credits_charged),
    }


@router.get("/capabilities/my-call-logs", summary="我的能力调用记录")
def my_call_logs(
    capability_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(CapabilityCallLog).filter(CapabilityCallLog.user_id == current_user.id)
    if capability_id:
        q = q.filter(CapabilityCallLog.capability_id == capability_id)
    rows = q.order_by(CapabilityCallLog.created_at.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 200)).all()
    return [
        {
            "id": r.id,
            "capability_id": r.capability_id,
            "success": r.success,
            "credits_charged": credits_json_float(r.credits_charged),
            "latency_ms": r.latency_ms,
            "request_payload": r.request_payload,
            "response_payload": r.response_payload,
            "error_message": r.error_message,
            "source": r.source,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]


@router.get("/capabilities/comfly-pricing", summary="Comfly 定价表（供 lobster_online 算力确认使用）")
def comfly_pricing():
    """返回 comfly_pricing.json 内容，前端可据此判断哪些模型走 Comfly 并展示预估算力。"""
    import json as _json
    from pathlib import Path as _Path
    defaults = {
        "image_generate_model": (
            getattr(settings, "lobster_default_image_generate_model", None) or "openai/gpt-image-2"
        ).strip() or "openai/gpt-image-2",
        "video_generate_model": (
            getattr(settings, "lobster_default_video_generate_model", None) or "xai/grok-imagine-video/text-to-video"
        ).strip() or "xai/grok-imagine-video/text-to-video",
    }
    p = _Path(__file__).resolve().parent.parent.parent.parent / "comfly_pricing.json"
    if not p.exists():
        return {"models": {}, "defaults": defaults}
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            if not isinstance(data.get("defaults"), dict):
                data["defaults"] = {}
            data["defaults"].update(defaults)
            return data
        return {"models": {}, "defaults": defaults}
    except Exception:
        return {"models": {}, "defaults": defaults}


# --------------- 速推模型与定价列表（用户展示用，乘以 user_price_multiplier） ---------------

import math as _math


@router.get("/api/sutui/models", summary="速推模型与定价（展示用，已乘用户倍率）")
def get_sutui_models():
    from ..services.sutui_pricing import (
        fetch_all_media_models,
        estimate_credits_from_pricing,
    )

    try:
        raw_models = fetch_all_media_models()
    except Exception as exc:
        raise HTTPException(502, detail=f"拉取速推模型失败: {exc}")

    multiplier = _get_user_price_multiplier()
    results = []
    for m in raw_models:
        pricing_raw = m.get("pricing")
        pricing_display = None
        if pricing_raw and isinstance(pricing_raw, dict):
            est_raw = estimate_credits_from_pricing(pricing_raw, {})
            est_user = int(_math.ceil(est_raw * multiplier)) if est_raw > 0 else 0
            pricing_display = {
                "default_credits": est_user,
                "price_type": pricing_raw.get("price_type", ""),
                "base_price_user": int(_math.ceil(float(pricing_raw.get("base_price") or 0) * multiplier)) if pricing_raw.get("base_price") else None,
                "examples": [],
            }
            for ex in (pricing_raw.get("examples") or []):
                ex_price = ex.get("price")
                if ex_price is not None:
                    try:
                        pricing_display["examples"].append({
                            "description": ex.get("description", ""),
                            "price": int(_math.ceil(float(ex_price) * multiplier)),
                        })
                    except (TypeError, ValueError):
                        pass

        results.append({
            "id": m["id"],
            "name": m.get("name", ""),
            "category": m.get("category", ""),
            "task_type": m.get("task_type", ""),
            "description": m.get("description", ""),
            "isHot": m.get("isHot", False),
            "isNew": m.get("isNew", False),
            "pricing": pricing_display,
        })

    return {"ok": True, "models": results, "multiplier": multiplier}
