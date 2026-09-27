"""抖音信息台计费：做同款（wan2.7-videoedit）与榜单搜索。

上游价（2026-09-27 取的官方文档 / 价目表原文）：
- 阿里云百炼「万相-视频编辑」：**输入视频和输出视频都计费，按视频秒数**，
  wan2.7-videoedit 720P = 0.6 元/秒、1080P = 1 元/秒（华北2 北京，50 秒免费额度，失败不计费）。
  计费公式：计费秒数 = 输入秒数 + 输出秒数。
- TikHub：我们每天采集的 10 个抖音榜单端点，每个 0.001 USD，合计 0.01 USD/天（≈0.075 元/天）；
  做同款取原视频 fetch_one_video_v2 = 0.001 USD/次；**榜单搜索走本地快照，零上游成本**。

我方定价（都可用环境变量改）：
- 做同款：credits = ceil(输入+输出秒数) × 单价(0.6 元/秒) × 100 算力/元 × 倍率(1.5)
  → 15 秒片 ≈ 2700 算力（27 元），5 秒片 ≈ 900 算力（9 元）。失败全额退（上游也不计费）。
- 搜索：DOUYIN_DESK_SEARCH_CREDITS 默认 1 算力/次（0 = 免费）。
"""
from __future__ import annotations

import os
from decimal import Decimal, ROUND_CEILING
from typing import Any, Dict

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .credit_ledger import append_credit_ledger
from .credits_amount import credits_json_float, quantize_credits, user_balance_decimal

CREDITS_PER_YUAN = Decimal("100")
_DEFAULT_YUAN_PER_SECOND = Decimal("0.6")     # 720P 输入+输出单价
_DEFAULT_BILLABLE_SIDES = Decimal("2")        # 输入 1 + 输出 1
_DEFAULT_MARKUP = Decimal("1.5")              # 与 wan_role 一致的加价倍率
_DEFAULT_SEARCH_CREDITS = Decimal("1")        # 每次搜索


def _decimal_env(name: str, default: Decimal) -> Decimal:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = Decimal(raw)
    except Exception:  # noqa: BLE001
        return default
    return value if value >= 0 else default


def yuan_per_second() -> Decimal:
    return _decimal_env("DOUYIN_VIDEOEDIT_YUAN_PER_SECOND", _DEFAULT_YUAN_PER_SECOND)


def billable_sides() -> Decimal:
    return _decimal_env("DOUYIN_VIDEOEDIT_BILLABLE_SIDES", _DEFAULT_BILLABLE_SIDES) or _DEFAULT_BILLABLE_SIDES


def markup() -> Decimal:
    return _decimal_env("DOUYIN_VIDEOEDIT_MARKUP", _DEFAULT_MARKUP) or Decimal("1")


def search_credits() -> Decimal:
    return quantize_credits(_decimal_env("DOUYIN_DESK_SEARCH_CREDITS", _DEFAULT_SEARCH_CREDITS))


def estimate_imitation(seconds: Any) -> Dict[str, Any]:
    """按「输入+输出都计费」估算一次做同款的成本与扣费。"""
    try:
        value = Decimal(str(seconds or 0))
    except Exception:  # noqa: BLE001
        value = Decimal("0")
    if value <= 0:
        value = Decimal("5")
    billable = (value * billable_sides()).to_integral_value(rounding=ROUND_CEILING)
    cost_yuan = (Decimal(billable) * yuan_per_second()).quantize(Decimal("0.0001"))
    credits = quantize_credits(cost_yuan * CREDITS_PER_YUAN * markup())
    return {
        "seconds": int(value),
        "billable_seconds": int(billable),
        "cost_yuan": float(cost_yuan),
        "credits": credits_json_float(credits),
        "yuan_per_second": float(yuan_per_second()),
        "billable_sides": float(billable_sides()),
        "markup": float(markup()),
    }


def _apply(db: Session, user: Any, credits: Decimal, *, reason: str, refund: bool = False) -> Decimal:
    if credits <= 0:
        return Decimal("0")
    balance = user_balance_decimal(user)
    if not refund and balance < credits:
        raise HTTPException(
            status_code=402,
            detail=f"算力不足：本次预计消耗 {credits_json_float(credits)} 算力，当前余额 {credits_json_float(balance)}。请充值后重试。",
        )
    delta = credits if refund else -credits
    user.credits = quantize_credits(balance + delta)
    append_credit_ledger(db, int(getattr(user, "id", 0) or 0), delta, "refund" if refund else "deduct",
                         quantize_credits(user.credits), meta={"reason": reason})
    db.commit()
    return credits


def deduct(db: Session, user: Any, credits: Decimal, *, reason: str) -> Decimal:
    return _apply(db, user, quantize_credits(credits), reason=reason)


def refund(db: Session, user: Any, credits: Decimal, *, reason: str) -> Decimal:
    return _apply(db, user, quantize_credits(credits), reason=reason, refund=True)
