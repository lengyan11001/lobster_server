"""速推相关：出站 HTTP 与积分流水审计日志（[sutui-audit] 前缀，便于 grep）。"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Dict, Optional

logger = logging.getLogger("sutui_audit")

_AUDITED_LEDGER_ENTRY_TYPES = frozenset(
    {
        "sutui_chat",
        "pre_deduct",
        "settle",
        "refund",
        "direct_charge",
        "unit_charge",
        "recharge",
    }
)


def _json_clip(obj: Any, max_len: int = 12000) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    if len(s) > max_len:
        return s[:max_len] + f"... [truncated total_len={len(s)}]"
    return s


def log_xskill_http(
    *,
    phase: str,
    method: str,
    url: str,
    http_status: int,
    capability_or_model: str = "",
    billing_snapshot: Optional[Dict[str, Any]] = None,
    error_message: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """记录每一次发往 xskill 的 HTTP（或等价）调用结果与计费快照。"""
    logger.info(
        "[sutui-audit] http phase=%s %s %s http=%s cap_or_model=%s billing=%s err=%s extra=%s",
        phase,
        method,
        url,
        http_status,
        capability_or_model or "-",
        _json_clip(billing_snapshot) if billing_snapshot else "-",
        (error_message or "-")[:3000],
        _json_clip(extra, 4000) if extra else "-",
    )


def maybe_log_credit_ledger_append(
    *,
    user_id: int,
    entry_type: str,
    delta: Decimal,
    balance_after: Decimal,
    ref_type: Optional[str],
    ref_id: Optional[str],
    description: str,
    meta: Optional[Dict[str, Any]],
) -> None:
    """能力与速推相关流水入库时打印（含 meta，便于对账）。"""
    et = (entry_type or "").strip()
    rt = (ref_type or "").strip()
    if et not in _AUDITED_LEDGER_ENTRY_TYPES:
        if rt not in ("capability_call_log", "sutui_chat") and not (rt == "capability" and et == "pre_deduct"):
            return
    logger.info(
        "[sutui-audit] db credit_ledger user_id=%s entry_type=%s delta=%s balance_after=%s "
        "ref_type=%s ref_id=%s description=%s meta=%s",
        user_id,
        et,
        delta,
        balance_after,
        (ref_type or "")[:32] or "-",
        (ref_id or "")[:128] or "-",
        (description or "")[:300],
        _json_clip(meta, 8000) if meta else "-",
    )


def log_capability_call_log_persisted(
    *,
    log_id: int,
    user_id: int,
    capability_id: str,
    credits_charged: Any,
    success: bool,
    source: str = "",
    request_summary: Any = None,
    error_message: Optional[str] = None,
) -> None:
    logger.info(
        "[sutui-audit] db capability_call_log id=%s user_id=%s capability_id=%s credits_charged=%s "
        "success=%s source=%s err=%s request=%s",
        log_id,
        user_id,
        capability_id,
        credits_charged,
        success,
        (source or "-")[:64],
        (error_message or "-")[:500],
        _json_clip(request_summary, 6000) if request_summary is not None else "-",
    )
