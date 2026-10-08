"""投稿素材采纳计费（2026-10-08）。

规则（用户暂定，可用环境变量覆盖）：
- 商家「采纳」一条投稿素材 → 商家扣积分、投稿人加同样的积分；
- 图片 100 积分、视频 300 积分、音频 100 积分，其它按 100；
- 幂等：同一条投稿只扣一次（重复采纳不再扣费）；
- 余额不足直接 402，不落任何扣费。

环境变量：SHOP_SUBMISSION_IMAGE_CREDITS / SHOP_SUBMISSION_VIDEO_CREDITS /
SHOP_SUBMISSION_AUDIO_CREDITS / SHOP_SUBMISSION_DEFAULT_CREDITS
"""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Dict

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .credit_ledger import append_credit_ledger
from .credits_amount import credits_json_float, quantize_credits, user_balance_decimal

_DEFAULTS = {
    "image": Decimal("100"),
    "video": Decimal("300"),
    "audio": Decimal("100"),
    "default": Decimal("100"),
}
_ENV_KEYS = {
    "image": "SHOP_SUBMISSION_IMAGE_CREDITS",
    "video": "SHOP_SUBMISSION_VIDEO_CREDITS",
    "audio": "SHOP_SUBMISSION_AUDIO_CREDITS",
    "default": "SHOP_SUBMISSION_DEFAULT_CREDITS",
}


def normalize_kind(media_type: Any) -> str:
    text = str(media_type or "").strip().lower()
    if "video" in text:
        return "video"
    if "image" in text:
        return "image"
    if "audio" in text:
        return "audio"
    return "default"


def price_credits(media_type: Any) -> Decimal:
    kind = normalize_kind(media_type)
    raw = (os.environ.get(_ENV_KEYS[kind]) or "").strip()
    if raw:
        try:
            value = Decimal(raw)
            if value >= 0:
                return quantize_credits(value)
        except Exception:  # noqa: BLE001
            pass
    return quantize_credits(_DEFAULTS[kind])


def pricing_table() -> Dict[str, float]:
    return {kind: credits_json_float(price_credits(kind)) for kind in ("image", "video", "audio", "default")}


def balance_of(user: Any) -> Decimal:
    return user_balance_decimal(user)


def ensure_balance(user: Any, credits: Decimal) -> None:
    need = quantize_credits(credits or 0)
    if need <= 0:
        return
    balance = balance_of(user)
    if balance < need:
        raise HTTPException(
            status_code=402,
            detail=(
                "算力不足：采纳这条素材需要 %s 积分，当前余额 %s。请先充值再来采纳。"
                % (credits_json_float(need), credits_json_float(balance))
            ),
        )


def charge(
    db: Session,
    payer: Any,
    credits: Decimal,
    *,
    ref_id: str,
    description: str,
) -> Decimal:
    """商家扣积分（只改内存 + 写流水，事务由调用方提交）。"""
    amount = quantize_credits(credits or 0)
    if amount <= 0:
        return Decimal("0")
    balance = balance_of(payer)
    if balance < amount:
        raise HTTPException(
            status_code=402,
            detail=(
                "算力不足：采纳这条素材需要 %s 积分，当前余额 %s。请先充值再来采纳。"
                % (credits_json_float(amount), credits_json_float(balance))
            ),
        )
    payer.credits = quantize_credits(balance - amount)
    append_credit_ledger(
        db,
        int(getattr(payer, "id", 0) or 0),
        -amount,
        "deduct",
        quantize_credits(payer.credits),
        description=description or "采纳投稿素材",
        ref_type="shop_submission",
        ref_id=str(ref_id),
    )
    return amount


def reward(
    db: Session,
    payee: Any,
    credits: Decimal,
    *,
    ref_id: str,
    description: str,
) -> Decimal:
    """投稿人加积分（只改内存 + 写流水，事务由调用方提交）。"""
    amount = quantize_credits(credits or 0)
    if amount <= 0 or payee is None:
        return Decimal("0")
    balance = balance_of(payee)
    payee.credits = quantize_credits(balance + amount)
    append_credit_ledger(
        db,
        int(getattr(payee, "id", 0) or 0),
        amount,
        "reward",
        quantize_credits(payee.credits),
        description=description or "投稿素材被采纳",
        ref_type="shop_submission",
        ref_id=str(ref_id),
    )
    return amount
