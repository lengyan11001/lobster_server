from __future__ import annotations

import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, Optional

import httpx
from sqlalchemy import distinct, func, or_
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import (
    Asset,
    CapabilityCallLog,
    ChatTurnLog,
    CreativeGenerationJob,
    CreditLedger,
    GenerationRecord,
    H5ChatDevicePresence,
    H5ChatMessage,
    PublishTask,
    ScheduledTaskRun,
    ToolCallLog,
    UserInstallation,
)
from ..services.model_usage_monitor import collect_model_usage_snapshot

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SEC = 3600.0
_DEFAULT_WINDOW_SEC = 3600.0
_DEFAULT_CURRENT_ONLINE_WINDOW_SEC = 300.0
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
        logger.warning("[runtime-monitor] invalid float env %s=%r", name, raw)
        return default


def _webhook_url() -> str:
    return (
        os.environ.get("RUNTIME_MONITOR_FEISHU_WEBHOOK")
        or os.environ.get("LOBSTER_RUNTIME_MONITOR_FEISHU_WEBHOOK")
        or os.environ.get("PROVIDER_BALANCE_FEISHU_WEBHOOK")
        or os.environ.get("LOBSTER_PROVIDER_BALANCE_FEISHU_WEBHOOK")
        or ""
    ).strip()


def is_runtime_monitor_enabled() -> bool:
    if not _webhook_url():
        return False
    return _env_bool("RUNTIME_MONITOR_ENABLED", True)


def _fmt_number(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


def _count(db: Session, model: Any, *filters: Any) -> int:
    q = db.query(func.count(model.id))
    for f in filters:
        q = q.filter(f)
    return int(q.scalar() or 0)


def _distinct_user_count(db: Session, model: Any, *filters: Any) -> int:
    q = db.query(func.count(distinct(model.user_id)))
    for f in filters:
        q = q.filter(f)
    return int(q.scalar() or 0)


def _distinct_installation_count(db: Session, model: Any, *filters: Any) -> int:
    q = db.query(func.count(distinct(model.installation_id)))
    for f in filters:
        q = q.filter(f)
    return int(q.scalar() or 0)


def _distinct_users_union(db: Session, since: datetime) -> int:
    user_ids: set[int] = set()
    for row in db.query(UserInstallation.user_id).filter(UserInstallation.last_seen_at >= since).distinct().all():
        if row[0] is not None:
            user_ids.add(int(row[0]))
    for row in db.query(H5ChatDevicePresence.user_id).filter(H5ChatDevicePresence.last_seen_at >= since).distinct().all():
        if row[0] is not None:
            user_ids.add(int(row[0]))
    return len(user_ids)


def _sum_negative_credits(db: Session, start: datetime, end: datetime) -> float:
    value = (
        db.query(func.sum(CreditLedger.delta))
        .filter(CreditLedger.created_at >= start, CreditLedger.created_at < end, CreditLedger.delta < 0)
        .scalar()
    )
    if value is None:
        return 0.0
    try:
        return abs(float(value))
    except Exception:
        return 0.0


def _status_count(db: Session, model: Any, statuses: Iterable[str], start: datetime, end: datetime, time_col: Any = None) -> int:
    col = time_col or model.created_at
    return _count(db, model, col >= start, col < end, model.status.in_(list(statuses)))


def _generation_capability_filter() -> Any:
    capability = func.lower(CapabilityCallLog.capability_id)
    upstream_tool = func.lower(CapabilityCallLog.upstream_tool)
    return or_(
        upstream_tool == "generate",
        capability.like("%generate%"),
        capability.like("%.pipeline"),
        capability.in_(("goal.video.pipeline", "create.video.pipeline", "comfly.daihuo.pipeline")),
    )


def _read_cpu_times() -> Optional[tuple[int, int]]:
    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            first = f.readline().strip().split()
        if not first or first[0] != "cpu":
            return None
        vals = [int(x) for x in first[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        return total, idle
    except Exception:
        return None


async def _cpu_percent() -> Optional[float]:
    before = _read_cpu_times()
    if before is None:
        return None
    await asyncio.sleep(0.25)
    after = _read_cpu_times()
    if after is None:
        return None
    total_delta = after[0] - before[0]
    idle_delta = after[1] - before[1]
    if total_delta <= 0:
        return None
    return max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))


def _memory_info() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        meminfo: Dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1]) * 1024
        total = meminfo.get("MemTotal")
        available = meminfo.get("MemAvailable")
        if total and available is not None:
            used = total - available
            out.update(
                {
                    "total_bytes": total,
                    "used_bytes": used,
                    "available_bytes": available,
                    "used_percent": used / total * 100.0,
                }
            )
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _disk_info(path: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"path": path}
    try:
        usage = shutil.disk_usage(path)
        out.update(
            {
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "used_percent": usage.used / usage.total * 100.0 if usage.total else None,
            }
        )
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _bytes_gib(value: Any) -> Optional[float]:
    try:
        return float(value) / (1024.0 ** 3)
    except Exception:
        return None


async def collect_runtime_monitor_snapshot() -> Dict[str, Any]:
    now = datetime.utcnow()
    window_sec = max(60.0, _env_float("RUNTIME_MONITOR_WINDOW_SEC", _DEFAULT_WINDOW_SEC))
    current_online_sec = max(60.0, _env_float("RUNTIME_MONITOR_CURRENT_ONLINE_WINDOW_SEC", _DEFAULT_CURRENT_ONLINE_WINDOW_SEC))
    start = now - timedelta(seconds=window_sec)
    current_since = now - timedelta(seconds=current_online_sec)

    db = SessionLocal()
    try:
        online = {
            "window_users": _distinct_users_union(db, start),
            "current_users": _distinct_users_union(db, current_since),
            "window_installations": _distinct_installation_count(db, UserInstallation, UserInstallation.last_seen_at >= start)
            + _distinct_installation_count(db, H5ChatDevicePresence, H5ChatDevicePresence.last_seen_at >= start),
            "current_installations": _distinct_installation_count(db, UserInstallation, UserInstallation.last_seen_at >= current_since)
            + _distinct_installation_count(db, H5ChatDevicePresence, H5ChatDevicePresence.last_seen_at >= current_since),
        }

        capability_total = _count(db, CapabilityCallLog, CapabilityCallLog.created_at >= start, CapabilityCallLog.created_at < now)
        capability_success = _count(db, CapabilityCallLog, CapabilityCallLog.created_at >= start, CapabilityCallLog.created_at < now, CapabilityCallLog.success.is_(True))
        capability_failed = _count(db, CapabilityCallLog, CapabilityCallLog.created_at >= start, CapabilityCallLog.created_at < now, CapabilityCallLog.success.is_(False))
        generation_capability_total = _count(db, CapabilityCallLog, CapabilityCallLog.created_at >= start, CapabilityCallLog.created_at < now, _generation_capability_filter())
        generation_capability_failed = _count(db, CapabilityCallLog, CapabilityCallLog.created_at >= start, CapabilityCallLog.created_at < now, CapabilityCallLog.success.is_(False), _generation_capability_filter())

        creative_created = _count(db, CreativeGenerationJob, CreativeGenerationJob.created_at >= start, CreativeGenerationJob.created_at < now)
        creative_failed = _status_count(db, CreativeGenerationJob, ("failed", "error", "cancelled"), start, now, CreativeGenerationJob.updated_at)
        scheduled_total = _count(db, ScheduledTaskRun, ScheduledTaskRun.created_at >= start, ScheduledTaskRun.created_at < now)
        scheduled_failed = _status_count(db, ScheduledTaskRun, ("failed", "error", "cancelled"), start, now, ScheduledTaskRun.updated_at)
        h5_total = _count(db, H5ChatMessage, H5ChatMessage.created_at >= start, H5ChatMessage.created_at < now)
        h5_failed = _status_count(db, H5ChatMessage, ("failed", "error", "cancelled"), start, now, H5ChatMessage.updated_at)
        publish_total = _count(db, PublishTask, PublishTask.created_at >= start, PublishTask.created_at < now)
        publish_failed = _status_count(db, PublishTask, ("failed", "error"), start, now, PublishTask.finished_at)
        generated_assets = _count(db, GenerationRecord, GenerationRecord.created_at >= start, GenerationRecord.created_at < now)
        generated_asset_users = _distinct_user_count(db, GenerationRecord, GenerationRecord.created_at >= start, GenerationRecord.created_at < now)
        asset_total = _count(db, Asset, Asset.created_at >= start, Asset.created_at < now)
        chat_turns = _count(db, ChatTurnLog, ChatTurnLog.created_at >= start, ChatTurnLog.created_at < now)
        tool_total = _count(db, ToolCallLog, ToolCallLog.created_at >= start, ToolCallLog.created_at < now)
        tool_failed = _count(db, ToolCallLog, ToolCallLog.created_at >= start, ToolCallLog.created_at < now, ToolCallLog.success.is_(False))
        credits_spent = _sum_negative_credits(db, start, now)
        model_usage = collect_model_usage_snapshot(db, start=start, end=now)
    finally:
        db.close()

    failure_events = capability_failed + creative_failed + scheduled_failed + h5_failed + publish_failed + tool_failed
    generation_events = generation_capability_total + creative_created + scheduled_total

    try:
        loadavg = os.getloadavg()
    except Exception:
        loadavg = None

    resource = {
        "cpu_percent": await _cpu_percent(),
        "loadavg": list(loadavg) if loadavg else None,
        "memory": _memory_info(),
        "disk": _disk_info((os.environ.get("RUNTIME_MONITOR_DISK_PATH") or "/").strip() or "/"),
    }

    checked_at = datetime.now(timezone.utc).isoformat()
    return {
        "ok": True,
        "checked_at": checked_at,
        "window": {
            "start": start.replace(tzinfo=timezone.utc).isoformat(),
            "end": now.replace(tzinfo=timezone.utc).isoformat(),
            "seconds": window_sec,
        },
        "online": online,
        "usage": {
            "generation_events": generation_events,
            "failure_events": failure_events,
            "capability_calls": {"total": capability_total, "success": capability_success, "failed": capability_failed},
            "generation_capability_calls": {"total": generation_capability_total, "failed": generation_capability_failed},
            "creative_jobs": {"created": creative_created, "failed": creative_failed},
            "scheduled_runs": {"created": scheduled_total, "failed": scheduled_failed},
            "h5_messages": {"created": h5_total, "failed": h5_failed},
            "publish_tasks": {"created": publish_total, "failed": publish_failed},
            "tool_calls": {"created": tool_total, "failed": tool_failed},
            "chat_turns": chat_turns,
            "saved_generation_records": generated_assets,
            "saved_generation_users": generated_asset_users,
            "assets_created": asset_total,
            "credits_spent": credits_spent,
            "model_usage": model_usage,
        },
        "resource": resource,
    }


def _local_time_text(iso_text: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
        return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_text


def build_runtime_monitor_card(data: Dict[str, Any]) -> Dict[str, Any]:
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    online = data.get("online") if isinstance(data.get("online"), dict) else {}
    resource = data.get("resource") if isinstance(data.get("resource"), dict) else {}
    memory = resource.get("memory") if isinstance(resource.get("memory"), dict) else {}
    disk = resource.get("disk") if isinstance(resource.get("disk"), dict) else {}
    loadavg = resource.get("loadavg") if isinstance(resource.get("loadavg"), list) else []
    model_usage = usage.get("model_usage") if isinstance(usage.get("model_usage"), dict) else {}
    window = data.get("window") if isinstance(data.get("window"), dict) else {}

    text_title = "\u670d\u52a1\u5668\u8fd0\u884c\u76d1\u63a7"
    text_window = "\u65f6\u95f4\u7a97\u53e3"
    text_online_users = "\u5728\u7ebf\u7528\u6237"
    text_online_devices = "\u5728\u7ebf\u8bbe\u5907"
    text_current = "\u5f53\u524d"
    text_scope = "\u7a97\u53e3"
    text_generation_events = "\u751f\u6210\u76f8\u5173\u4e8b\u4ef6"
    text_failure_events = "\u5931\u8d25\u4e8b\u4ef6"
    text_capability_calls = "\u80fd\u529b\u8c03\u7528"
    text_success = "\u6210\u529f"
    text_failed = "\u5931\u8d25"
    text_saved_records = "\u5165\u5e93\u751f\u6210\u8bb0\u5f55"
    text_related_users = "\u5173\u8054\u7528\u6237"
    text_chat_turns = "\u5bf9\u8bdd\u8f6e\u6b21"
    text_tool_calls = "\u5de5\u5177\u8c03\u7528"
    text_credits = "\u6d88\u8017\u7b97\u529b"
    text_model_usage = "\u6a21\u578b\u8c03\u7528\u7edf\u8ba1"
    text_final_provider = "\u6700\u7ec8\u547d\u4e2d\u6e20\u9053"
    text_attempt_provider = "\u6e20\u9053\u5c1d\u8bd5"
    text_top_attempt_provider = "Top \u5c1d\u8bd5\u6e20\u9053"
    text_request = "\u8bf7\u6c42"
    text_memory = "\u5185\u5b58"
    text_disk = "\u78c1\u76d8"
    text_dialog = "\u5bf9\u8bdd"
    text_image = "\u56fe\u7247"
    text_video = "\u89c6\u9891"

    def _provider_name(item: Dict[str, Any]) -> str:
        provider = str(item.get("provider") or "").strip()
        channel = str(item.get("channel") or "").strip()
        if provider and channel:
            return f"{provider}/{channel}"
        return provider or channel or "-"

    def _final_provider_summary(items: Any) -> str:
        if not isinstance(items, list):
            return "-"
        winners = []
        for item in items:
            if not isinstance(item, dict):
                continue
            success = int(item.get("success") or 0)
            if success <= 0:
                continue
            winners.append(f"{_provider_name(item)}: {success}")
        return " / ".join(winners[:3]) or "-"

    def _attempt_provider_summary(items: Any) -> str:
        if not isinstance(items, list):
            return "-"
        parts = []
        for item in items[:3]:
            if not isinstance(item, dict):
                continue
            parts.append(f"{_provider_name(item)}: {int(item.get('total') or 0)}")
        return " / ".join(parts) or "-"

    failure_events = int(usage.get("failure_events") or 0)
    disk_pct = disk.get("used_percent")
    mem_pct = memory.get("used_percent")
    cpu_pct = resource.get("cpu_percent")
    warn = failure_events > 0
    for value in (disk_pct, mem_pct, cpu_pct):
        try:
            warn = warn or float(value) >= 85.0
        except Exception:
            pass

    header_color = "red" if warn else "green"
    capability_calls = usage.get("capability_calls") if isinstance(usage.get("capability_calls"), dict) else {}
    tool_calls = usage.get("tool_calls") if isinstance(usage.get("tool_calls"), dict) else {}

    lines = [
        f"{text_window}: {_local_time_text(str(window.get('start') or ''))} - {_local_time_text(str(window.get('end') or ''))}",
        "",
        f"- {text_online_users}: {text_current} {online.get('current_users', 0)} / {text_scope} {online.get('window_users', 0)}",
        f"- {text_online_devices}: {text_current} {online.get('current_installations', 0)} / {text_scope} {online.get('window_installations', 0)}",
        f"- {text_generation_events}: {usage.get('generation_events', 0)}",
        f"- {text_failure_events}: {usage.get('failure_events', 0)}",
        f"- {text_capability_calls}: {capability_calls.get('total', 0)}；{text_success} {capability_calls.get('success', 0)}；{text_failed} {capability_calls.get('failed', 0)}",
        f"- {text_saved_records}: {usage.get('saved_generation_records', 0)}；{text_related_users} {usage.get('saved_generation_users', 0)}",
        f"- {text_chat_turns}: {usage.get('chat_turns', 0)}；{text_tool_calls} {tool_calls.get('created', 0)}",
        f"- {text_credits}: {_fmt_number(usage.get('credits_spent'), 4)}",
        "",
        f"### {text_model_usage}",
    ]

    labels = {
        "dialog": text_dialog,
        "image": text_image,
        "video": text_video,
    }
    for category in ("dialog", "image", "video"):
        label = labels[category]
        cat = model_usage.get(category) if isinstance(model_usage.get(category), dict) else {}
        request = cat.get("request") if isinstance(cat.get("request"), dict) else {}
        attempt = cat.get("attempt") if isinstance(cat.get("attempt"), dict) else {}
        lines.extend(
            [
                f"- {label}: {text_request} {request.get('total', 0)}，{text_success} {request.get('success', 0)}，{text_failed} {request.get('failed', 0)}",
                f"  {text_final_provider}: {_final_provider_summary(request.get('top_providers'))}",
                f"  {text_attempt_provider} {attempt.get('total', 0)} 次；{text_top_attempt_provider} {_attempt_provider_summary(attempt.get('top_providers'))}",
            ]
        )

    lines.extend(
        [
            "",
            f"- CPU: {_fmt_number(cpu_pct)}%",
            f"- Load: {', '.join(_fmt_number(x) for x in loadavg) if loadavg else '-'}",
            f"- {text_memory}: {_fmt_number(mem_pct)}% ({_fmt_number(_bytes_gib(memory.get('used_bytes')))} / {_fmt_number(_bytes_gib(memory.get('total_bytes')))} GiB)",
            f"- {text_disk} {disk.get('path') or '-'}: {_fmt_number(disk_pct)}% ({_fmt_number(_bytes_gib(disk.get('used_bytes')))} / {_fmt_number(_bytes_gib(disk.get('total_bytes')))} GiB)",
        ]
    )

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": header_color, "title": {"tag": "plain_text", "content": text_title}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}],
        },
    }


async def post_runtime_monitor_to_feishu(data: Dict[str, Any], *, webhook: str) -> Dict[str, Any]:
    payload = build_runtime_monitor_card(data)
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


async def runtime_monitor_tick() -> Dict[str, Any]:
    webhook = _webhook_url()
    if not webhook:
        raise RuntimeError("RUNTIME_MONITOR_FEISHU_WEBHOOK/PROVIDER_BALANCE_FEISHU_WEBHOOK is not configured")
    data = await collect_runtime_monitor_snapshot()
    feishu_resp = await post_runtime_monitor_to_feishu(data, webhook=webhook)
    logger.info(
        "[runtime-monitor] sent generation_events=%s failure_events=%s current_users=%s",
        data.get("usage", {}).get("generation_events"),
        data.get("usage", {}).get("failure_events"),
        data.get("online", {}).get("current_users"),
    )
    return {"ok": True, "data": data, "feishu": feishu_resp}


async def runtime_monitor_loop_forever(interval_sec: Optional[float] = None) -> None:
    interval = interval_sec or _env_float("RUNTIME_MONITOR_INTERVAL_SEC", _DEFAULT_INTERVAL_SEC)
    interval = max(60.0, float(interval))
    initial_delay = max(0.0, _env_float("RUNTIME_MONITOR_INITIAL_DELAY_SEC", 60.0))
    if initial_delay:
        await asyncio.sleep(initial_delay)
    while True:
        try:
            await runtime_monitor_tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[runtime-monitor] tick failed")
        await asyncio.sleep(interval)
