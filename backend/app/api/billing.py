"""软件收费模式配置与展示：技能解锁价格、算力套餐（积分兑换比例）；自有充值订单；自建微信支付。"""
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config import settings, get_effective_public_base_url
from ..db import get_db
from .auth import get_current_user
from ..models import RechargeOrder, User

logger = logging.getLogger(__name__)


def _wechat_pay_configured() -> bool:
    mch_id = (getattr(settings, "wechat_mch_id", None) or "").strip()
    key = (getattr(settings, "wechat_pay_apiv3_key", None) or "").strip()
    serial = (getattr(settings, "wechat_pay_serial_no", None) or "").strip()
    key_path = (getattr(settings, "wechat_pay_private_key_path", None) or "").strip()
    app_id = (getattr(settings, "wechat_app_id", None) or "").strip()
    return bool(mch_id and key and serial and key_path and app_id)


def _get_public_base_url() -> str:
    """微信支付回调等用。未配置 PUBLIC_BASE_URL 时用本机 IP:PORT。"""
    return get_effective_public_base_url()

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_CUSTOM_CONFIGS_FILE = _BASE_DIR / "custom_configs.json"

# 默认收费模式（可被 custom_configs.json 中 BILLING_PRICING 覆盖）
_DEFAULT_SKILL_UNLOCK = {"min_yuan": 98, "max_yuan": 198}
_DEFAULT_CREDIT_PACKAGES = [
    {"price_yuan": 198, "credits": 2000, "label": "198元 - 2000积分"},
    {"price_yuan": 498, "credits": 5000, "label": "498元 - 5000积分"},
    {"price_yuan": 998, "credits": 12000, "label": "998元 - 12000积分"},
]


def _get_billing_pricing() -> dict[str, Any]:
    """从 custom_configs.json 读取 BILLING_PRICING；缺失时返回默认。"""
    if not _CUSTOM_CONFIGS_FILE.exists():
        return {
            "skill_unlock": _DEFAULT_SKILL_UNLOCK,
            "credit_packages": _DEFAULT_CREDIT_PACKAGES,
        }
    try:
        data = json.loads(_CUSTOM_CONFIGS_FILE.read_text(encoding="utf-8"))
        cfg = (data.get("configs") or {}).get("BILLING_PRICING")
        if not isinstance(cfg, dict):
            return {
                "skill_unlock": _DEFAULT_SKILL_UNLOCK,
                "credit_packages": _DEFAULT_CREDIT_PACKAGES,
            }
        skill = cfg.get("skill_unlock")
        if isinstance(skill, dict):
            min_yuan = skill.get("min_yuan")
            max_yuan = skill.get("max_yuan")
            skill_unlock = {
                "min_yuan": int(min_yuan) if min_yuan is not None else _DEFAULT_SKILL_UNLOCK["min_yuan"],
                "max_yuan": int(max_yuan) if max_yuan is not None else _DEFAULT_SKILL_UNLOCK["max_yuan"],
            }
        else:
            skill_unlock = _DEFAULT_SKILL_UNLOCK

        packages = cfg.get("credit_packages")
        if isinstance(packages, list) and packages:
            out = []
            for p in packages:
                if not isinstance(p, dict):
                    continue
                price = p.get("price_yuan") or p.get("price")
                credits = p.get("credits")
                if price is not None and credits is not None:
                    label = (p.get("label") or "").strip() or f"{int(price)}元 - {int(credits)}积分"
                    out.append({
                        "price_yuan": int(price),
                        "credits": int(credits),
                        "label": label,
                    })
            if out:
                credit_packages = out
            else:
                credit_packages = _DEFAULT_CREDIT_PACKAGES
        else:
            credit_packages = _DEFAULT_CREDIT_PACKAGES

        return {"skill_unlock": skill_unlock, "credit_packages": credit_packages}
    except Exception as e:
        logger.debug("BILLING_PRICING read failed: %s", e)
        return {
            "skill_unlock": _DEFAULT_SKILL_UNLOCK,
            "credit_packages": _DEFAULT_CREDIT_PACKAGES,
        }


@router.get("/api/billing/pricing", summary="软件收费模式（技能解锁价格 + 算力套餐）")
def get_billing_pricing(current_user: User = Depends(get_current_user)):
    """返回技能解锁价格区间与算力套餐列表，供前端展示。可在 custom_configs.json 的 configs.BILLING_PRICING 中覆盖。"""
    return _get_billing_pricing()


# ── 自有充值（独立于速推）────────────────────────────────────────────────────

def _use_independent_recharge() -> bool:
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    return edition == "online" and getattr(settings, "lobster_independent_auth", True)


@router.get("/api/recharge/packages", summary="充值套餐列表（自有）")
def get_recharge_packages(current_user: User = Depends(get_current_user)):
    if not _use_independent_recharge():
        raise HTTPException(status_code=400, detail="当前未启用自有充值")
    pricing = _get_billing_pricing()
    return {"packages": pricing.get("credit_packages", _DEFAULT_CREDIT_PACKAGES)}


class RechargeCreateBody(BaseModel):
    package_index: Optional[int] = None
    price_yuan: Optional[int] = None
    credits: Optional[int] = None


@router.post("/api/recharge/create", summary="创建充值订单（自有）")
def create_recharge_order(
    body: RechargeCreateBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _use_independent_recharge():
        raise HTTPException(status_code=400, detail="当前未启用自有充值")
    pricing = _get_billing_pricing()
    packages = pricing.get("credit_packages", _DEFAULT_CREDIT_PACKAGES)
    if body.package_index is not None:
        idx = int(body.package_index)
        if idx < 0 or idx >= len(packages):
            raise HTTPException(status_code=400, detail="无效套餐")
        p = packages[idx]
        amount_yuan = p["price_yuan"]
        credits = p["credits"]
    elif body.price_yuan is not None and body.credits is not None:
        amount_yuan = int(body.price_yuan)
        credits = int(body.credits)
        if amount_yuan <= 0 or credits <= 0:
            raise HTTPException(status_code=400, detail="金额与积分须为正数")
    else:
        raise HTTPException(status_code=400, detail="请选择套餐或指定 price_yuan + credits")
    out_trade_no = f"R{current_user.id}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    order = RechargeOrder(
        user_id=current_user.id,
        amount_yuan=amount_yuan,
        credits=credits,
        status="pending",
        out_trade_no=out_trade_no,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    payment_hint = getattr(settings, "lobster_recharge_payment_hint", None) or "请通过微信/支付宝转账并联系管理员完成到账，备注订单号。"
    return {
        "order_id": order.id,
        "out_trade_no": order.out_trade_no,
        "amount_yuan": order.amount_yuan,
        "credits": order.credits,
        "status": order.status,
        "payment_info": payment_hint,
        "created_at": order.created_at.isoformat() if order.created_at else "",
    }


class RechargeCompleteBody(BaseModel):
    out_trade_no: Optional[str] = None
    order_id: Optional[int] = None


# ── 自建微信支付（不用速推）────────────────────────────────────────────────

@router.post("/api/recharge/wechat-create", summary="创建充值订单并调微信 Native 下单，返回扫码链接")
def create_wechat_recharge_order(
    body: RechargeCreateBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _use_independent_recharge():
        raise HTTPException(status_code=400, detail="当前未启用自有充值")
    if not _wechat_pay_configured():
        raise HTTPException(status_code=400, detail="未配置自建微信支付（wechat_mch_id/wechat_pay_apiv3_key/wechat_pay_serial_no/wechat_pay_private_key_path/wechat_app_id）")
    pricing = _get_billing_pricing()
    packages = pricing.get("credit_packages", _DEFAULT_CREDIT_PACKAGES)
    if body.package_index is not None:
        idx = int(body.package_index)
        if idx < 0 or idx >= len(packages):
            raise HTTPException(status_code=400, detail="无效套餐")
        p = packages[idx]
        amount_yuan = p["price_yuan"]
        credits = p["credits"]
    elif body.price_yuan is not None and body.credits is not None:
        amount_yuan = int(body.price_yuan)
        credits = int(body.credits)
        if amount_yuan <= 0 or credits <= 0:
            raise HTTPException(status_code=400, detail="金额与积分须为正数")
    else:
        raise HTTPException(status_code=400, detail="请选择套餐或指定 price_yuan + credits")
    out_trade_no = f"R{current_user.id}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    order = RechargeOrder(
        user_id=current_user.id,
        amount_yuan=amount_yuan,
        credits=credits,
        status="pending",
        out_trade_no=out_trade_no,
        payment_method="wechat",
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    base_url = _get_public_base_url()
    notify_url = f"{base_url}/api/recharge/wechat-notify"
    key_path = Path((getattr(settings, "wechat_pay_private_key_path", None) or "").strip())
    if not key_path.is_absolute():
        key_path = _BASE_DIR / key_path
    try:
        private_key = key_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("wechat pay private key read failed: %s", e)
        raise HTTPException(status_code=500, detail="微信支付商户私钥读取失败")
    try:
        from wechatpayv3 import WeChatPay, WeChatPayType
        wxpay = WeChatPay(
            wechatpay_type=WeChatPayType.NATIVE,
            mchid=(getattr(settings, "wechat_mch_id", None) or "").strip(),
            private_key=private_key,
            cert_serial_no=(getattr(settings, "wechat_pay_serial_no", None) or "").strip(),
            apiv3_key=(getattr(settings, "wechat_pay_apiv3_key", None) or "").strip(),
            appid=(getattr(settings, "wechat_app_id", None) or "").strip(),
        )
        code_url, _ = wxpay.pay(
            description=f"龙虾积分充值-{credits}积分",
            out_trade_no=out_trade_no,
            amount={"total": amount_yuan * 100},
            notify_url=notify_url,
        )
    except Exception as e:
        logger.exception("wechat pay create failed: %s", e)
        raise HTTPException(status_code=502, detail="微信下单失败，请稍后重试")
    return {
        "order_id": order.id,
        "out_trade_no": order.out_trade_no,
        "amount_yuan": order.amount_yuan,
        "credits": order.credits,
        "code_url": code_url,
        "status": order.status,
    }


@router.post("/api/recharge/wechat-notify", summary="微信支付异步回调（验签解密后完成订单加积分）")
async def wechat_pay_notify(request: Request, db: Session = Depends(get_db)):
    if not _wechat_pay_configured():
        return PlainTextResponse("fail", status_code=500)
    raw = await request.body()
    headers = dict(request.headers)
    key_path = Path((getattr(settings, "wechat_pay_private_key_path", None) or "").strip())
    if not key_path.is_absolute():
        key_path = _BASE_DIR / key_path
    try:
        private_key = key_path.read_text(encoding="utf-8")
    except Exception:
        return PlainTextResponse("fail", status_code=500)
    try:
        from wechatpayv3 import WeChatPay, WeChatPayType
        wxpay = WeChatPay(
            wechatpay_type=WeChatPayType.NATIVE,
            mchid=(getattr(settings, "wechat_mch_id", None) or "").strip(),
            private_key=private_key,
            cert_serial_no=(getattr(settings, "wechat_pay_serial_no", None) or "").strip(),
            apiv3_key=(getattr(settings, "wechat_pay_apiv3_key", None) or "").strip(),
            appid=(getattr(settings, "wechat_app_id", None) or "").strip(),
        )
        result = wxpay.callback(headers, raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except Exception as e:
        logger.warning("wechat notify callback verify failed: %s", e)
        return PlainTextResponse("fail", status_code=400)
    event_type = result.get("event_type") if isinstance(result, dict) else None
    if not result or event_type != "TRANSACTION.SUCCESS":
        return PlainTextResponse("success")
    res = result.get("resource") if isinstance(result.get("resource"), dict) else result
    out_trade_no = (res.get("out_trade_no") or result.get("out_trade_no") or "").strip()
    if not out_trade_no:
        return PlainTextResponse("success")
    order = db.query(RechargeOrder).filter(RechargeOrder.out_trade_no == out_trade_no).first()
    if not order or order.status == "paid":
        return PlainTextResponse("success")
    user = db.query(User).filter(User.id == order.user_id).first()
    if user:
        user.credits = (user.credits or 0) + order.credits
    order.status = "paid"
    from datetime import datetime
    order.paid_at = datetime.utcnow()
    db.commit()
    return PlainTextResponse("success")


@router.post("/api/recharge/complete", summary="完成充值（管理员/回调：到账加积分）")
def complete_recharge(
    body: RechargeCompleteBody,
    x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret"),
    db: Session = Depends(get_db),
):
    secret = (getattr(settings, "lobster_recharge_admin_secret", None) or "").strip()
    if not secret or (x_admin_secret or "").strip() != secret:
        raise HTTPException(status_code=403, detail="需要管理员密钥")
    if body.out_trade_no:
        order = db.query(RechargeOrder).filter(RechargeOrder.out_trade_no == body.out_trade_no.strip()).first()
    elif body.order_id is not None:
        order = db.query(RechargeOrder).filter(RechargeOrder.id == body.order_id).first()
    else:
        raise HTTPException(status_code=400, detail="请提供 out_trade_no 或 order_id")
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status == "paid":
        return {"ok": True, "message": "订单已支付过", "order_id": order.id}
    user = db.query(User).filter(User.id == order.user_id).first()
    if not user:
        raise HTTPException(status_code=500, detail="用户不存在")
    user.credits = (user.credits or 0) + order.credits
    order.status = "paid"
    from datetime import datetime
    order.paid_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "message": f"已到账 {order.credits} 积分", "order_id": order.id}
