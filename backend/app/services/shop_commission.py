"""shop 分佣 / 归因 / 订单状态机 —— 纯逻辑，便于单测（无 DB 依赖）。

金额单位：分；比例单位：基点 bp（300 = 3%）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

BP_DENOMINATOR = 10000
COMMISSION_DEFAULT_BP = 1000  # 商家默认 10%
PLATFORM_FEE_DEFAULT_BP = 300  # 平台抽成 3%
ATTRIBUTION_DAYS_DEFAULT = 30
AUTO_CONFIRM_DAYS = 7  # 确认收货后 T+7 佣金确认
UNPAID_CANCEL_MINUTES = 30

ORDER_STATUSES = ("pending", "paid", "shipped", "completed", "refunding", "refunded", "cancelled")
COMMISSION_STATUSES = ("none", "pending", "confirmed", "settled", "invalid")


@dataclass(frozen=True)
class CommissionPlan:
    base_cents: int
    rate_bp: int
    amount_cents: int
    applied: str  # product | merchant_default | fixed


def effective_rate_bp(
    product_bp: Optional[int],
    merchant_default_bp: int = COMMISSION_DEFAULT_BP,
    *,
    commission_type: str = "percent",
) -> int:
    """商品级比例优先；为 0/None 时用商家默认；固定佣金模式返回 0。"""
    if str(commission_type or "percent").lower() == "fixed":
        return 0
    bp = int(product_bp or 0)
    if bp > 0:
        return min(bp, BP_DENOMINATOR)
    return max(0, min(int(merchant_default_bp or 0), BP_DENOMINATOR))


def compute_commission(
    *,
    pay_amount_cents: int,
    shipping_cents: int = 0,
    product_bp: Optional[int] = 0,
    merchant_default_bp: int = COMMISSION_DEFAULT_BP,
    commission_type: str = "percent",
    commission_fixed_cents: int = 0,
    qty: int = 1,
    cap_cents: int = 0,
    min_cents: int = 0,
) -> CommissionPlan:
    """佣金 = (实付 - 运费) × 比例；固定模式 = 固定额 × 数量；再套封顶/保底。"""
    base = max(0, int(pay_amount_cents or 0) - int(shipping_cents or 0))
    if str(commission_type or "percent").lower() == "fixed":
        amount = max(0, int(commission_fixed_cents or 0)) * max(1, int(qty or 1))
        plan = CommissionPlan(base_cents=base, rate_bp=0, amount_cents=amount, applied="fixed")
    else:
        rate_bp = effective_rate_bp(product_bp, merchant_default_bp, commission_type=commission_type)
        amount = base * rate_bp // BP_DENOMINATOR
        applied = "product" if int(product_bp or 0) > 0 else "merchant_default"
        plan = CommissionPlan(base_cents=base, rate_bp=rate_bp, amount_cents=amount, applied=applied)
    amount = plan.amount_cents
    cap = int(cap_cents or 0)
    if cap > 0:
        amount = min(amount, cap)
    floor_cents = int(min_cents or 0)
    if floor_cents > 0 and plan.amount_cents > 0:
        amount = max(amount, floor_cents)
    if plan.base_cents <= 0:
        amount = 0
    return CommissionPlan(base_cents=plan.base_cents, rate_bp=plan.rate_bp, amount_cents=max(0, amount), applied=plan.applied)


def prorate_commission_on_refund(amount_cents: int, pay_amount_cents: int, refund_cents: int) -> int:
    """部分退款时按比例冲减佣金（向下取整，避免多算）。"""
    paid = max(0, int(pay_amount_cents or 0))
    refund = max(0, min(int(refund_cents or 0), paid))
    if paid <= 0 or refund <= 0:
        return max(0, int(amount_cents or 0))
    remain = paid - refund
    return max(0, int(amount_cents or 0) * remain // paid)


def platform_fee_cents(pay_amount_cents: int, platform_fee_bp: int = PLATFORM_FEE_DEFAULT_BP) -> int:
    bp = max(0, min(int(platform_fee_bp or 0), BP_DENOMINATOR))
    return max(0, int(pay_amount_cents or 0)) * bp // BP_DENOMINATOR


def merchant_income_cents(
    pay_amount_cents: int,
    *,
    platform_fee_bp: int = PLATFORM_FEE_DEFAULT_BP,
    commission_cents: int = 0,
    shipping_cents: int = 0,
) -> int:
    """商家实收 = 实付 − 平台抽成 − 推广佣金（运费归商家，不参与抽成与佣金）。"""
    gross = max(0, int(pay_amount_cents or 0))
    fee = platform_fee_cents(gross, platform_fee_bp)
    return max(0, gross - fee - max(0, int(commission_cents or 0)))


def should_attribute(
    *,
    self_buy: bool = False,
    within_window: bool = True,
    repeated_visitor: bool = False,
    block_self_buy: bool = True,
    block_repeated: bool = False,
) -> tuple[bool, str]:
    """归因闸门：返回 (是否计佣, 原因)。"""
    if self_buy and block_self_buy:
        return False, "self_buy"
    if not within_window:
        return False, "attribution_expired"
    if repeated_visitor and block_repeated:
        return False, "repeated_visitor"
    return True, "ok"


def commission_status_for_order(order_status: str, current: str = "none", *, completed_at: Optional[datetime] = None, now: Optional[datetime] = None) -> str:
    """订单状态 → 佣金状态（pending→confirmed→settled；退款作废）。"""
    status = str(order_status or "").lower()
    if status in ("refunded", "cancelled"):
        return "invalid"
    if status in ("pending",):
        return "none"
    if status in ("paid", "shipped"):
        return "pending"
    if status == "refunding":
        return "pending"
    if status == "completed":
        if current in ("settled",):
            return "settled"
        deadline = confirm_deadline(completed_at, days=AUTO_CONFIRM_DAYS)
        if deadline is not None and now is not None and now >= deadline:
            return "confirmed"
        return "pending" if current in ("none", "pending") else current
    return current


def confirm_deadline(completed_at: Optional[datetime], *, days: int = AUTO_CONFIRM_DAYS) -> Optional[datetime]:
    if completed_at is None:
        return None
    return completed_at + timedelta(days=max(0, int(days or 0)))


def cancel_deadline(created_at: Optional[datetime], *, minutes: int = UNPAID_CANCEL_MINUTES) -> Optional[datetime]:
    if created_at is None:
        return None
    return created_at + timedelta(minutes=max(1, int(minutes or 1)))


def attribution_expire_at(clicked_at: datetime, *, days: int = ATTRIBUTION_DAYS_DEFAULT) -> datetime:
    return clicked_at + timedelta(days=max(1, int(days or 1)))


def referral_link(base_url: str, product_id: int, promoter_user_id: int, code: str = "") -> str:
    """推广链接：/p/{product}?r={promoter}&rf={code}。"""
    root = str(base_url or "").rstrip("/")
    query = f"r={int(promoter_user_id)}"
    if code:
        query += f"&rf={code}"
    return f"{root}/p/{int(product_id)}?{query}"


def make_order_no(merchant_id: int, *, now: Optional[datetime] = None, seq: int = 0) -> str:
    """订单号：SH + 时间 + 商家 + 序号。"""
    stamp = (now or datetime.utcnow()).strftime("%Y%m%d%H%M%S")
    return f"SH{stamp}{int(merchant_id) % 10000:04d}{int(seq or 0) % 100000:05d}"
