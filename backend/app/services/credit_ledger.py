"""积分流水：每次余额变动写入 credit_ledger，便于对账（预扣/结算/退款/充值等）。"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional, Union

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import CreditLedger, User
from .credits_amount import quantize_credits, quantize_credits_signed

logger = logging.getLogger(__name__)

# 仅入库、供运营对账；用户侧接口应剥掉后再返回
_LEDGER_RECON_KEY = "_recon"
_PUBLIC_LEDGER_META_HIDDEN_KEYS = {_LEDGER_RECON_KEY, "upstream", "sutui_pool", "sutui_token_ref"}


def apply_credit_delta(db: Session, user_id: int, delta: Union[int, float, Decimal, str]) -> Decimal:
    """原子加减余额，返回变动后的余额。

    2026-10-21 事故：结算/退款用"启动时读到的旧余额 ± 金额"回写 users.credits，
    把用户刚充进来的 1000 覆盖回充值前的值（账上有充值流水，余额却是旧值）。
    所有余额变更必须走这里，SQL 层 credits = credits + delta，不做快照赋值。
    """
    amount = quantize_credits_signed(delta)
    new_balance = db.execute(
        update(User)
        .where(User.id == int(user_id))
        .values(credits=User.credits + amount)
        .returning(User.credits)
    ).scalar_one()
    return quantize_credits(new_balance)

def public_ledger_meta(meta: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """从流水 meta 中移除站内对账字段，避免通过 API 暴露给用户。"""
    if not meta:
        return meta
    out = {k: v for k, v in meta.items() if k not in _PUBLIC_LEDGER_META_HIDDEN_KEYS}
    return out or None


def append_credit_ledger(
    db: Session,
    user_id: int,
    delta: Union[int, float, Decimal, str],
    entry_type: str,
    balance_after: Union[int, float, Decimal, str],
    *,
    description: str = "",
    ref_type: Optional[str] = None,
    ref_id: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> CreditLedger:
    """delta：正数为入账，负数为出账；balance_after 为变动后 users.credits 快照（最多 4 位小数，非负）。"""
    d = quantize_credits_signed(delta)
    bal = quantize_credits(balance_after)
    row = CreditLedger(
        user_id=user_id,
        delta=d,
        balance_after=bal,
        entry_type=(entry_type or "unknown")[:32],
        description=(description or "")[:512] or None,
        ref_type=(ref_type or "")[:32] or None,
        ref_id=(ref_id or "")[:128] or None,
        meta=meta,
    )
    db.add(row)
    # 2026-10-21：流水里的 balance_after 必须和 users.credits 实际值一致。
    # 不一致说明有别的路径用旧快照覆盖了余额（充值被覆盖事故），
    # 直接抛错回滚，宁可这笔业务失败也不能让账实不符。
    try:
        db.flush()
        actual = db.execute(select(User.credits).where(User.id == int(user_id))).scalar_one_or_none()
        if actual is not None and abs(quantize_credits(actual) - bal) > Decimal("0.0001"):
            logger.error(
                "[credit-ledger] balance mismatch user=%s entry=%s delta=%s ledger_bal=%s actual=%s",
                user_id, row.entry_type, d, bal, quantize_credits(actual),
            )
            raise RuntimeError(
                f"积分流水与余额不一致 user={user_id} ledger={bal} actual={quantize_credits(actual)}"
            )
    except RuntimeError:
        raise
    except Exception:
        logger.debug("[credit-ledger] balance verify skipped", exc_info=True)
    try:
        from .sutui_api_audit import maybe_log_credit_ledger_append

        maybe_log_credit_ledger_append(
            user_id=user_id,
            entry_type=row.entry_type or "",
            delta=d,
            balance_after=bal,
            ref_type=ref_type,
            ref_id=ref_id,
            description=description or "",
            meta=meta,
        )
    except Exception:
        pass
    return row
