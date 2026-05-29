from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import CreditLedger, UserDailyCreditLimit
from .credits_amount import credits_json_float, ledger_display_delta, quantize_credits, quantize_credits_signed

_BJ = ZoneInfo("Asia/Shanghai")


def _today_bounds_utc_naive() -> tuple[datetime, datetime]:
    now_bj = datetime.now(_BJ)
    start_bj = datetime.combine(now_bj.date(), time.min, tzinfo=_BJ)
    end_bj = datetime.combine(now_bj.date(), time.max, tzinfo=_BJ)
    return (
        start_bj.astimezone(timezone.utc).replace(tzinfo=None),
        end_bj.astimezone(timezone.utc).replace(tzinfo=None),
    )


def get_user_daily_limit_row(db: Session, user_id: int) -> Optional[UserDailyCreditLimit]:
    return db.query(UserDailyCreditLimit).filter(UserDailyCreditLimit.user_id == user_id).first()


def get_user_daily_limit(db: Session, user_id: int) -> Decimal:
    row = get_user_daily_limit_row(db, user_id)
    if row is None or row.daily_limit is None:
        return Decimal("0")
    return quantize_credits(row.daily_limit)


def set_user_daily_limit(db: Session, user_id: int, value: Any) -> UserDailyCreditLimit:
    limit = quantize_credits(value)
    row = get_user_daily_limit_row(db, user_id)
    if row is None:
        row = UserDailyCreditLimit(user_id=user_id, daily_limit=limit if limit > 0 else None)
        db.add(row)
    else:
        row.daily_limit = limit if limit > 0 else None
    db.commit()
    db.refresh(row)
    return row


def today_credit_usage(db: Session, user_id: int) -> Decimal:
    start_utc, end_utc = _today_bounds_utc_naive()
    rows = (
        db.query(CreditLedger)
        .filter(
            CreditLedger.user_id == user_id,
            CreditLedger.created_at >= start_utc,
            CreditLedger.created_at <= end_utc,
        )
        .all()
    )
    spent = Decimal("0")
    for row in rows:
        delta = quantize_credits_signed(ledger_display_delta(row))
        if delta < 0:
            spent += -delta
        elif delta > 0 and (row.entry_type or "").strip().lower() in {"refund", "publish_refund", "settle"}:
            spent -= delta
    if spent < 0:
        spent = Decimal("0")
    return quantize_credits(spent)


def daily_limit_status(db: Session, user_id: int) -> Dict[str, Any]:
    limit = get_user_daily_limit(db, user_id)
    used = today_credit_usage(db, user_id)
    remaining = Decimal("0") if limit > 0 and used >= limit else (limit - used if limit > 0 else Decimal("0"))
    return {
        "enabled": limit > 0,
        "daily_limit": credits_json_float(limit),
        "used_today": credits_json_float(used),
        "remaining_today": credits_json_float(remaining),
        "timezone": "Asia/Shanghai",
    }


def assert_daily_limit_allows(db: Session, user_id: int, amount: Any, *, action_label: str = "本次操作") -> None:
    need = quantize_credits(amount)
    if need <= 0:
        return
    limit = get_user_daily_limit(db, user_id)
    if limit <= 0:
        return
    used = today_credit_usage(db, user_id)
    if used + need <= limit:
        return
    raise HTTPException(
        status_code=429,
        detail=(
            f"已超过每日算力消耗上限：今日已用 {credits_json_float(used)}，"
            f"{action_label}预计需 {credits_json_float(need)}，每日上限 {credits_json_float(limit)}。"
            "请调高每日上限或明天再试。"
        ),
    )

