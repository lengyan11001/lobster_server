"""长任务看护 + 后台任务进度卡（2026-09-20）。

职责：
  1) 把"确认后转入后台"的任务（Online 子任务 / 服务器生成任务）扫出来，轮询真实状态
  2) 完成后推一条通知到用户「系统任务」会话（离开页面也能收到）
  3) 同步维护原消息上的进度卡 task_card：聚合多个子任务、整体状态、产物链接
     （取消后不再复活）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from ..api.auth import access_token_claims, create_access_token
from ..db import SessionLocal
from ..models import H5ChatEvent, H5ChatMessage, ScheduledTaskRun, User
from .h5_chat_sessions import attach_system_task_message
from .mastra_task_card import (
    latest_task_card,
    normalize_artifacts,
    overall_status,
    status_label,
    upsert_task_card,
)

logger = logging.getLogger(__name__)

MCP_URL = "http://127.0.0.1:8001/mcp"
TASK_NOTICE_EVENT = "task_notice"

_MAX_ASSETS = 4
_ONLINE_TTL_HOURS = 6
_MEDIA_SCAN_HOURS = 6
_MEDIA_TIMEOUT_MINUTES = 45
_COLD_START_WINDOW_MINUTES = 5
_cold_start_done = False

_IN_PROGRESS_STATUSES = {
    "pending", "queued", "submitted", "processing", "generating", "running",
    "处理中", "生成中", "排队中", "运行中", "上传中", "等待中",
}
_FAILURE_STATUSES = {
    "failed", "failure", "error", "cancelled", "canceled", "timeout", "expired",
    "失败", "错误", "取消", "超时",
}
_SUCCESS_STATUSES = {
    "success", "completed", "done", "succeeded", "finished",
    "已完成", "生成成功", "成功", "完成",
}
_CANCELLED_STATUSES = {"cancelled", "canceled", "已取消", "已停止"}


def _enabled() -> bool:
    raw = os.environ.get("LOBSTER_BACKGROUND_MASTRA_TASK_WATCH_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _interval_seconds() -> float:
    try:
        return max(5.0, min(300.0, float(os.environ.get("LOBSTER_MASTRA_TASK_WATCH_INTERVAL_SECONDS") or 20)))
    except (TypeError, ValueError):
        return 20.0


def _notice_key(kind: str, *parts: Any) -> str:
    return ":".join([kind] + [str(part or "") for part in parts])[:200]


def _notice_already_sent(db: Session, root_message_id: str, key: str) -> bool:
    if not root_message_id:
        return False
    rows = (
        db.query(H5ChatEvent.payload)
        .filter(H5ChatEvent.message_id == root_message_id, H5ChatEvent.event_type == TASK_NOTICE_EVENT)
        .order_by(H5ChatEvent.id.desc())
        .limit(20)
        .all()
    )
    for (payload,) in rows:
        if isinstance(payload, dict) and str(payload.get("key") or "") == key:
            return True
    return False


def _collect_urls(value: Any, limit: int = _MAX_ASSETS) -> List[str]:
    found: List[str] = []
    seen = set()

    def visit(item: Any, depth: int) -> None:
        if depth > 6 or len(found) >= limit:
            return
        if isinstance(item, str):
            text = item.strip()
            if text.startswith("http://") or text.startswith("https://"):
                if text not in seen:
                    seen.add(text)
                    found.append(text)
            elif text.startswith("{") or text.startswith("["):
                try:
                    visit(json.loads(text), depth + 1)
                except Exception:  # noqa: BLE001
                    return
            return
        if isinstance(item, list):
            for child in item[:20]:
                visit(child, depth + 1)
            return
        if isinstance(item, dict):
            for key in ("url", "source_url", "public_url", "file_url", "image_url", "video_url", "output_url", "media_urls"):
                if key in item:
                    visit(item[key], depth + 1)
            for child in item.values():
                if isinstance(child, (dict, list)):
                    visit(child, depth + 1)

    visit(value, 0)
    return found


def _truncate(text: Any, limit: int = 400) -> str:
    raw = str(text or "").strip()
    return raw[:limit] + ("…" if len(raw) > limit else "")


def _push_notice(
    db: Session,
    *,
    user_id: int,
    installation_id: str,
    text: str,
    root_message_id: str,
    key: str,
) -> None:
    now = datetime.utcnow()
    body = text.strip()
    if not body:
        return
    if _notice_already_sent(db, root_message_id, key):
        return
    message = H5ChatMessage(
        id=os.urandom(16).hex(),
        user_id=user_id,
        installation_id=installation_id or "",
        mode="scheduled_task",
        content=body,
        status="completed",
        reply_text=body,
        created_at=now,
        updated_at=now,
        finished_at=now,
    )
    attach_system_task_message(db, message, now=now)
    db.add(message)
    if root_message_id:
        db.add(
            H5ChatEvent(
                message_id=root_message_id,
                user_id=user_id,
                event_type=TASK_NOTICE_EVENT,
                payload={"key": key, "text": _truncate(body), "notified_message_id": message.id},
                created_at=now,
            )
        )
    db.commit()
    logger.info("[task-watch] notified user=%s key=%s message=%s", user_id, key, message.id)


# ── Online 子任务 ────────────────────────────────────────────────


def _online_child_summary(db: Session, child: H5ChatMessage) -> Optional[Dict[str, Any]]:
    """把一条 Online 子任务整理成「通知 + 进度卡」都需要的信息。"""
    if child.parent_message_id is None:
        return None
    run = (
        db.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.h5_message_id == child.id)
        .order_by(ScheduledTaskRun.created_at.desc())
        .first()
    )
    title = str(getattr(run, "title", "") or child.content or "任务").strip()[:60]
    raw_status = str(child.status or "").strip().lower()
    run_status = str(getattr(run, "status", "") or "").strip().lower()
    if raw_status in {"cancelled", "canceled"} or run_status in _CANCELLED_STATUSES:
        state = "cancelled"
    elif raw_status in {"pending", "processing"} or run_status in {"pending", "processing"}:
        state = "running"
    elif raw_status == "failed" or run_status == "failed":
        state = "failed"
    else:
        state = "done"
    result_text = str(getattr(run, "result_text", "") or child.reply_text or "").strip()
    error_text = str(getattr(run, "error", "") or child.error or "").strip()
    urls = _collect_urls(getattr(run, "result_payload", None) or child.reply_text)
    key = _notice_key("online", child.id)
    if state == "cancelled":
        text = f"「{title}」已取消。"
    elif state == "failed":
        text = f"「{title}」执行失败：{_truncate(error_text or result_text or '未返回原因')}。可以让我重试或换一种做法。"
    elif state == "running":
        text = f"「{title}」正在执行（后台任务，可以离开页面）。"
    else:
        lines = [f"「{title}」已完成。"]
        if result_text:
            lines.append(_truncate(result_text, 500))
        if urls:
            lines.append("产物：" + "、".join(urls))
        lines.append("需要我继续发布、二次剪辑或做数据分析，直接说就行。")
        text = "\n".join(lines)
    return {
        "key": key,
        "title": title,
        "state": state,
        "urls": urls,
        "notice_text": text,
        "short_text": text.split("\n")[0][:200],
        "root_id": child.parent_message_id,
        "user_id": int(child.user_id),
    }


def _online_child_notice(db: Session, child: H5ChatMessage) -> Optional[Tuple[str, str, str]]:
    """返回 (key, text, root_message_id)；不需要通知时返回 None。"""
    summary = _online_child_summary(db, child)
    if summary is None:
        return None
    return summary["key"], summary["notice_text"], summary["root_id"]


def _online_items(db: Session, root_id: str, now: datetime) -> List[Dict[str, Any]]:
    cutoff = now - timedelta(hours=_ONLINE_TTL_HOURS)
    children = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.parent_message_id == root_id,
            H5ChatMessage.updated_at >= cutoff,
        )
        .order_by(H5ChatMessage.created_at.desc())
        .limit(12)
        .all()
    )
    items: List[Dict[str, Any]] = []
    for child in children:
        summary = _online_child_summary(db, child)
        if not summary:
            continue
        items.append(
            {
                "key": summary["key"],
                "title": summary["title"],
                "status": summary["state"],
                "text": summary["short_text"],
                "artifacts": summary["urls"],
            }
        )
    return items


# ── 媒体/生成任务 ────────────────────────────────────────────────


def _message_installation_id(db: Session, message_id: str) -> str:
    row = db.query(H5ChatMessage.installation_id).filter(H5ChatMessage.id == message_id).first()
    return str(getattr(row, "installation_id", "") or "") if row else ""


def _media_tasks_from_events(db: Session, now: datetime) -> List[Dict[str, Any]]:
    """从最近的事件里挑出"还没结束"的媒体/生成任务。"""
    cutoff = now - timedelta(hours=_MEDIA_SCAN_HOURS)
    rows = (
        db.query(H5ChatEvent)
        .filter(
            H5ChatEvent.event_type.in_(("progress", "queued", "thinking")),
            H5ChatEvent.created_at >= cutoff,
        )
        .order_by(H5ChatEvent.id.desc())
        .limit(800)
        .all()
    )
    out: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        candidate = payload.get("media_task")
        if not isinstance(candidate, dict):
            continue
        task_id = str(candidate.get("task_id") or "").strip()
        capability_id = str(candidate.get("capability_id") or "").strip()
        if not task_id or not capability_id or task_id in seen:
            continue
        if bool(candidate.get("terminal")):
            continue
        status = str(candidate.get("status") or "").strip().lower()
        if status and status not in _IN_PROGRESS_STATUSES:
            continue
        seen.add(task_id)
        out.append(
            {
                "message_id": row.message_id,
                "user_id": row.user_id,
                "task_id": task_id,
                "capability_id": capability_id,
                "created_at": row.created_at,
                "installation_id": _message_installation_id(db, row.message_id),
            }
        )
        if len(out) >= 40:
            break
    return out


def _mint_user_token(db: Session, user_id: int) -> Optional[str]:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        return None
    try:
        return create_access_token(access_token_claims(user))
    except Exception:  # noqa: BLE001
        logger.warning("[task-watch] 生成用户令牌失败 user=%s", user_id, exc_info=True)
        return None


async def _poll_capability_task(
    token: str,
    capability_id: str,
    task_id: str,
    installation_id: str = "",
) -> Optional[Dict[str, Any]]:
    body = {
        "jsonrpc": "2.0",
        "id": "task-watch",
        "method": "tools/call",
        "params": {
            "name": "invoke_capability",
            "arguments": {
                "capability_id": "task.get_result",
                "payload": {"task_id": task_id, "capability_id": capability_id},
            },
        },
    }
    try:
        headers = {"Authorization": f"Bearer {token}"}
        if installation_id:
            headers["X-Installation-Id"] = installation_id
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            response = await client.post(MCP_URL, json=body, headers=headers)
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.info("[task-watch] 轮询失败 task=%s: %s", task_id, exc)
        return None
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return None
    content = result.get("content")
    text = ""
    if isinstance(content, list) and content and isinstance(content[0], dict):
        text = str(content[0].get("text") or "")
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except Exception:  # noqa: BLE001
        return {"status": "unknown", "raw": text[:400]}
    if not isinstance(parsed, dict):
        return {"raw": text[:400]}
    for key in ("data", "result"):
        nested = parsed.get(key)
        if isinstance(nested, dict):
            parsed = {**parsed, **nested}
    return parsed


def _task_error_text(task: Dict[str, Any]) -> str:
    error = task.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("detail") or error.get("error") or error)[:300]
    if error:
        return str(error)[:300]
    message = task.get("message") or task.get("detail")
    if isinstance(message, dict):
        return str(message.get("message") or message)[:300]
    return str(message or "")[:300]


def _media_state(task: Dict[str, Any]) -> str:
    status = str(task.get("status") or task.get("state") or "").strip().lower()
    if status in _CANCELLED_STATUSES:
        return "cancelled"
    if status in _FAILURE_STATUSES:
        return "failed"
    if status in _SUCCESS_STATUSES:
        return "done"
    return ""


def _media_item_key(capability_id: str, task_id: str) -> str:
    return f"media:{capability_id}:{task_id}"


# ── 进度卡聚合 ───────────────────────────────────────────────────


def _rebuild_root_card(
    db: Session,
    *,
    root_id: str,
    user_id: int,
    extra_items: Optional[Dict[str, Dict[str, Any]]] = None,
) -> bool:
    """按该请求下所有子任务的真实状态，重写一张聚合进度卡。"""
    if not root_id:
        return False
    previous = latest_task_card(db, root_id) or {}
    if str(previous.get("status") or "") == "cancelled":
        return False  # 已取消：不再复活
    now = datetime.utcnow()
    items = _online_items(db, root_id, now)
    by_key = {str(item.get("key") or ""): item for item in items}
    # 媒体任务：本轮刚轮询到的优先，其余沿用上一张卡里的（避免两次轮询之间状态回退）
    for key, item in (extra_items or {}).items():
        by_key[key] = item
    for raw in previous.get("items") or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "")
        if key.startswith("media:") and key not in by_key:
            by_key[key] = raw
    items = list(by_key.values())[:12]
    if not items:
        return False
    status = overall_status([str(item.get("status") or "") for item in items])
    artifacts = normalize_artifacts([url for item in items for url in (item.get("artifacts") or [])])
    titles = [str(item.get("title") or "") for item in items if str(item.get("title") or "")]
    done_count = sum(1 for item in items if str(item.get("status")) == "done")
    failed_count = sum(1 for item in items if str(item.get("status")) == "failed")
    if len(items) == 1:
        title = titles[0] if titles else "后台任务"
    else:
        title = f"后台任务（{len(items)} 个）"
    lines = []
    for item in items:
        lines.append(f"- {item.get('title') or '任务'}：{status_label(str(item.get('status') or ''))}")
    if status == "running":
        summary = f"{done_count}/{len(items)} 已完成，其余执行中；可以离开页面，完成后我会在这里更新。"
    elif status == "done":
        summary = f"{len(items)} 个任务全部完成。" + (f"（{failed_count} 个失败）" if failed_count else "")
    elif status == "failed":
        summary = f"有 {failed_count} 个任务失败，其余已完成；失败原因见下。"
    else:
        summary = ""
    text = "\n".join([line for line in [summary] + lines if line]).strip()
    full_text = text
    if status == "done":
        detail = [str(item.get("text") or "") for item in items if str(item.get("status")) == "done" and item.get("text")]
        if detail:
            full_text = text + "\n" + "\n".join(detail[:3])
    return upsert_task_card(
        db,
        message_id=root_id,
        user_id=user_id,
        status=status,
        title=title,
        text=full_text,
        artifacts=artifacts,
        items=items,
        source="watch",
    )


# ── 主循环 ───────────────────────────────────────────────────────


def _watch_online_children(now: datetime, *, window_minutes: Optional[float] = None) -> int:
    db = SessionLocal()
    try:
        if window_minutes is None:
            cutoff = now - timedelta(hours=_ONLINE_TTL_HOURS)
        else:
            cutoff = now - timedelta(minutes=max(1.0, float(window_minutes)))
        # 1) 进行中/刚结束的子任务：先把进度卡刷新一遍（聚合多个子任务）
        touched_roots: Dict[str, int] = {}
        recent_children = (
            db.query(H5ChatMessage)
            .filter(
                H5ChatMessage.parent_message_id.isnot(None),
                H5ChatMessage.updated_at >= cutoff,
                H5ChatMessage.status.in_(("pending", "processing", "completed", "failed", "cancelled")),
            )
            .order_by(H5ChatMessage.updated_at.desc())
            .limit(200)
            .all()
        )
        for child in recent_children:
            if child.parent_message_id:
                touched_roots[child.parent_message_id] = int(child.user_id)
        for root_id, user_id in touched_roots.items():
            try:
                _rebuild_root_card(db, root_id=root_id, user_id=user_id)
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
                logger.warning("[task-watch] 刷新进度卡失败 root=%s", root_id, exc_info=True)

        # 2) 已结束的：推通知（幂等）
        notified = 0
        finished = [child for child in recent_children if str(child.status or "").lower() in {"completed", "failed"}]
        for child in finished:
            try:
                summary = _online_child_summary(db, child)
                if not summary:
                    continue
                key = summary["key"]
                root_message_id = summary["root_id"]
                if _notice_already_sent(db, root_message_id, key):
                    continue
                _push_notice(
                    db,
                    user_id=int(child.user_id),
                    installation_id=str(child.installation_id or ""),
                    text=summary["notice_text"],
                    root_message_id=root_message_id,
                    key=key,
                )
                notified += 1
            except Exception:  # noqa: BLE001
                db.rollback()
                logger.warning("[task-watch] 处理 Online 子任务失败 id=%s", child.id, exc_info=True)
        return notified
    finally:
        db.close()


async def _watch_media_tasks(now: datetime) -> int:
    db = SessionLocal()
    try:
        pending = _media_tasks_from_events(db, now)
        tokens: Dict[int, Optional[str]] = {}
        media_items: Dict[str, Dict[str, Dict[str, Any]]] = {}
        updates: List[Dict[str, Any]] = []
        for item in pending:
            user_id = int(item["user_id"])
            if user_id not in tokens:
                tokens[user_id] = _mint_user_token(db, user_id)
            token = tokens[user_id]
            if not token:
                continue
            key = _media_item_key(item["capability_id"], item["task_id"])
            previous = latest_task_card(db, item["message_id"]) or {}
            if any(str(raw.get("key") or "") == key and str(raw.get("status")) == "cancelled" for raw in (previous.get("items") or []) if isinstance(raw, dict)):
                continue
            if _notice_already_sent(db, item["message_id"], _notice_key(key)):
                continue
            task = await _poll_capability_task(
                token,
                item["capability_id"],
                item["task_id"],
                item.get("installation_id") or "",
            )
            age_minutes = (now - item["created_at"]).total_seconds() / 60.0 if item["created_at"] else 0.0
            state = _media_state(task or {})
            urls = _collect_urls(task or {})
            if not state and age_minutes < _MEDIA_TIMEOUT_MINUTES:
                media_items.setdefault(item["message_id"], {})[key] = {
                    "key": key,
                    "title": str(item["capability_id"]),
                    "status": "running",
                    "text": f"{item['capability_id']} 生成中，已等待 {int(age_minutes)} 分钟。",
                    "artifacts": urls,
                }
                continue
            if not state:
                state = "running"
                text = (
                    f"生成任务还在处理（{item['capability_id']}，已等待 {int(age_minutes)} 分钟）。"
                    "我会继续等，出结果就通知你；你也可以让我先做别的。"
                )
            elif state == "failed":
                text = (
                    f"生成任务失败（{item['capability_id']}）："
                    f"{_truncate(_task_error_text(task or {}) or '未返回原因')}。可以让我重试或换参数。"
                )
            elif state == "cancelled":
                text = f"生成任务已取消（{item['capability_id']}）。"
            else:
                lines = [f"生成完成（{item['capability_id']}）。"]
                if urls:
                    lines.append("产物：" + "、".join(urls))
                lines.append("需要我继续发布、改写或做二次处理，直接说就行。")
                text = "\n".join(lines)
            media_items.setdefault(item["message_id"], {})[key] = {
                "key": key,
                "title": str(item["capability_id"]),
                "status": state,
                "text": _truncate(text, 300),
                "artifacts": urls,
            }
            if state in {"done", "failed", "cancelled"}:
                updates.append(
                    {
                        "message_id": item["message_id"],
                        "user_id": user_id,
                        "text": text,
                        "key": _notice_key(key),
                    }
                )
        for message_id, items in media_items.items():
            # 取该消息的属主（事件里带 user_id）
            owner_row = (
                db.query(H5ChatEvent.user_id)
                .filter(H5ChatEvent.message_id == message_id)
                .order_by(H5ChatEvent.id.desc())
                .first()
            )
            user_id = int(owner_row[0]) if owner_row else 0
            if not user_id:
                continue
            try:
                _rebuild_root_card(db, root_id=message_id, user_id=user_id, extra_items=items)
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
                logger.warning("[task-watch] 刷新媒体进度卡失败 message=%s", message_id, exc_info=True)
        for update in updates:
            _push_notice(
                db,
                user_id=update["user_id"],
                installation_id="",
                text=update["text"],
                root_message_id=update["message_id"],
                key=update["key"],
            )
        return len(pending)
    finally:
        db.close()


async def watch_once() -> Dict[str, int]:
    global _cold_start_done
    now = datetime.utcnow()
    media_scanned = await _watch_media_tasks(now)
    if _cold_start_done:
        online_notified = _watch_online_children(now)
    else:
        online_notified = _watch_online_children(now, window_minutes=_COLD_START_WINDOW_MINUTES)
        _cold_start_done = True
    return {"media_scanned": media_scanned, "online_notified": online_notified}


async def mastra_task_watch_loop_forever(interval_seconds: Optional[float] = None) -> None:
    interval = interval_seconds or _interval_seconds()
    logger.info("[task-watch] 长任务看护启动 interval=%.1fs", interval)
    while True:
        try:
            stats = await watch_once()
            if stats.get("online_notified"):
                logger.info("[task-watch] 本轮通知 %s 条 Online 完成", stats["online_notified"])
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("[task-watch] 看护循环异常", exc_info=True)
        await asyncio.sleep(interval)


def is_mastra_task_watch_enabled() -> bool:
    return _enabled()
