from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import httpx

from ..api.provider_balances import collect_provider_balances

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SEC = 3600.0
_DEFAULT_LOW_THRESHOLD = 50.0
_DEFAULT_TIMEOUT_SEC = 30.0


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("[provider-balance-monitor] invalid float env %s=%r", name, raw)
        return default


def _webhook_url() -> str:
    return (
        os.environ.get("PROVIDER_BALANCE_FEISHU_WEBHOOK")
        or os.environ.get("LOBSTER_PROVIDER_BALANCE_FEISHU_WEBHOOK")
        or ""
    ).strip()


def is_provider_balance_monitor_enabled() -> bool:
    webhook = _webhook_url()
    if not webhook:
        return False
    return _env_bool("PROVIDER_BALANCE_MONITOR_ENABLED", True)


def _fmt_number(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _numeric_balance(item: Dict[str, Any]) -> Optional[float]:
    value = item.get("balance")
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_low_balance(item: Dict[str, Any], threshold: float) -> bool:
    bal = _numeric_balance(item)
    return bal is not None and bal < threshold


def _line_for_provider(item: Dict[str, Any], threshold: float) -> str:
    provider = str(item.get("provider") or "unknown")
    name = str(item.get("name") or provider)
    unit = str(item.get("balance_unit") or "")
    ok = bool(item.get("ok"))
    balance = item.get("balance")
    balance_text = "N/A" if balance is None else _fmt_number(balance)
    label = f"{name} ({provider})"
    status = "OK" if ok else "ERROR"
    if not ok:
        err = str(item.get("error") or item.get("message") or "unknown error")
        return f"- <font color='red'>{label}: {status} - {err[:180]}</font>"
    content = f"{label}: {balance_text}"
    if unit:
        content += f" {unit}"
    if _is_low_balance(item, threshold):
        return f"- <font color='red'>{content}</font>"
    return f"- {content}"


def _summary_lines(providers: Iterable[Dict[str, Any]], threshold: float) -> List[str]:
    return [_line_for_provider(item, threshold) for item in providers]


def build_feishu_card(data: Dict[str, Any], *, threshold: float) -> Dict[str, Any]:
    providers = data.get("providers") if isinstance(data.get("providers"), list) else []
    low_count = sum(1 for item in providers if isinstance(item, dict) and _is_low_balance(item, threshold))
    error_count = sum(1 for item in providers if isinstance(item, dict) and not bool(item.get("ok")))
    title = "上游余额监控"
    header_color = "red" if low_count or error_count or not bool(data.get("ok")) else "green"
    checked_at = str(data.get("checked_at") or datetime.now(timezone.utc).isoformat())
    lines = _summary_lines([p for p in providers if isinstance(p, dict)], threshold)
    if not lines:
        lines = ["- <font color='red'>没有查询到任何上游余额数据</font>"]
    subtitle = f"检查时间: {checked_at}\n低余额阈值: {threshold:g}\n\n" + "\n".join(lines)
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": header_color,
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": subtitle},
                }
            ],
        },
    }


async def post_provider_balance_to_feishu(data: Dict[str, Any], *, webhook: str, threshold: float) -> Dict[str, Any]:
    payload = build_feishu_card(data, threshold=threshold)
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SEC, trust_env=False) as client:
        resp = await client.post(webhook, json=payload)
    text = resp.text or ""
    try:
        body = resp.json() if resp.content else {}
    except Exception:
        body = {"raw": text[:500]}
    if resp.status_code >= 400:
        raise RuntimeError(f"Feishu webhook HTTP {resp.status_code}: {text[:500]}")
    if isinstance(body, dict):
        code = body.get("code")
        if code not in (None, 0):
            raise RuntimeError(f"Feishu webhook returned code={code}: {str(body)[:500]}")
    return body if isinstance(body, dict) else {"raw": str(body)[:500]}


async def provider_balance_monitor_tick() -> Dict[str, Any]:
    webhook = _webhook_url()
    if not webhook:
        raise RuntimeError("PROVIDER_BALANCE_FEISHU_WEBHOOK is not configured")
    threshold = _env_float("PROVIDER_BALANCE_LOW_THRESHOLD", _DEFAULT_LOW_THRESHOLD)
    data = await collect_provider_balances()
    feishu_resp = await post_provider_balance_to_feishu(data, webhook=webhook, threshold=threshold)
    logger.info(
        "[provider-balance-monitor] sent provider_count=%s ok=%s threshold=%s",
        len(data.get("providers") or []),
        data.get("ok"),
        threshold,
    )
    return {"ok": True, "data": data, "feishu": feishu_resp}


async def provider_balance_monitor_loop_forever(interval_sec: Optional[float] = None) -> None:
    interval = interval_sec or _env_float("PROVIDER_BALANCE_MONITOR_INTERVAL_SEC", _DEFAULT_INTERVAL_SEC)
    interval = max(60.0, float(interval))
    initial_delay = max(0.0, _env_float("PROVIDER_BALANCE_MONITOR_INITIAL_DELAY_SEC", 10.0))
    if initial_delay:
        await asyncio.sleep(initial_delay)
    while True:
        try:
            await provider_balance_monitor_tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[provider-balance-monitor] tick failed")
        await asyncio.sleep(interval)
