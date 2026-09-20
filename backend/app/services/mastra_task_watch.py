"""长任务看护：产物完成/失败后主动推回会话（2026-09-20）。

编排会话里"确认后执行"的长任务分两类：
  1) 下发到 Online 的子任务（H5ChatMessage.parent_message_id 子消息 + ScheduledTaskRun）
  2) 服务器侧生成能力（image.generate / video.generate / pipeline，进度事件里带 media_task）
这两类做完时原本不会主动告诉用户：第 1 类只更新子消息自身；第 2 类要等用户再来一轮才会被
resume 轮询。这里开一个后台看护循环，轮询这两类任务，完成/失败后往用户的「系统任务」会话
推一条可读通知（幂等：同一个任务只通知一次）。
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
from .mastra_task_card import upsert_task_card

logger = logging.getLogger(__name__)

MCP_URL = "http://127.0.0.1:8001/mcp"
TASK_NOTICE_EVENT = "task_notice"

# 通知里最多展示几条产物
_MAX_ASSETS = 4
# 只回看这么久之内结束的任务（避免历史数据被重新通知）
_ONLINE_TTL_HOURS = 6
_MEDIA_SCAN_HOURS = 6
# 超过这个时长仍未结束的媒体任务，推一条"仍在生成"的说明后不再跟踪
_MEDIA_TIMEOUT_MINUTES = 45
# 进程刚启动的第一轮只补最近的完成通知（避免重启/发版时把几小时前的任务一次性推给用户）
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
        .filter(
            H5ChatEvent.message_id == root_message_id,
            H5ChatEvent.event_type == TASK_NOTICE_EVENT,
        )
        .order_by(H5ChatEvent.id.desc())
        .limit(20)
        .all()
    )
    for (payload,) in rows:
        if isinstance(payload, dict) and str(payload.get("key") or "") == key:
            return True
    return False


def _collect_urls(value: Any, limit: int = _MAX_ASSETS) -> List[str]:
    """从任意工具/任务结果里挑出可点击的产物地址。"""
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
    """往用户「系统任务」会话推一条通知，并在原消息上留一个幂等标记事件。"""
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
    failed = str(child.status or "").strip().lower() == "failed" or str(getattr(run, "status", "") or "") == "failed"
    result_text = str(getattr(run, "result_text", "") or child.reply_text or "").strip()
    error_text = str(getattr(run, "error", "") or child.error or "").strip()
    urls = _collect_urls(getattr(run, "result_payload", None) or child.reply_text)
    key = _notice_key("online", child.id)
    if failed:
        text = f"「{title}」执行失败：{_truncate(error_text or result_text or '未返回原因')}。可以让我重试或换一种做法。"
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
        "failed": failed,
        "running": str(child.status or "").strip().lower() in {"pending", "processing"},
        "urls": urls,
        "notice_text": text,
        "root_id": child.parent_message_id,
        "user_id": int(child.user_id),
    }


def _online_child_notice(db: Session, child: H5ChatMessage) -> Optional[Tuple[str, str, str]]:
    """返回 (key, text, root_message_id)；不需要通知时返回 None。"""
    summary = _online_child_summary(db, child)
    if summary is None:
        return None
    return summary["key"], summary["notice_text"], summary["root_id"]


def _media_tasks_from_events(db: Session, now: datetime) -> List[Dict[str, Any]]:
    """从最近的事件里挑出"还没结束"的媒体/生成任务。"""
    installs: Dict[str, str] = {}

    def installation_for(message_id: str) -> str:
        if message_id not in installs:
            row = db.query(H5ChatMessage.installation_id).filter(H5ChatMessage.id == message_id).first()
            installs[message_id] = str(getattr(row, "installation_id", "") or "") if row else ""
        return installs[message_id]
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
                "installation_id": installation_for(row.message_id),
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
            # MCP 侧要求 X-Installation-Id：缺了会直接返回"请使用最新客户端"
            headers["X-Installation-Id"] = installation_id
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            response = await client.post(
                MCP_URL,
                json=body,
                headers=headers,
            )
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
    # MCP 的返回有几种包法：{status:...} / {data:{...}} / {capability_id, result:{...}}
    for key in ("data", "result"):
        nested = parsed.get(key)
        if isinstance(nested, dict):
            parsed = {**parsed, **nested}
    return parsed


def _media_status(task: Dict[str, Any]) -> str:
    status = str(task.get("status") or task.get("state") or "").strip().lower()
    if not status and isinstance(task.get("data"), dict):
        status = str(task["data"].get("status") or "").strip().lower()
    return status


def _task_error_text(task: Dict[str, Any]) -> str:
    """错误信息可能是字符串，也可能是 {message}/{detail}/{error:{message}} 这类结构。"""
    error = task.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("detail") or error.get("error") or error)[:300]
    if error:
        return str(error)[:300]
    message = task.get("message") or task.get("detail")
    if isinstance(message, dict):
        return str(message.get("message") or message)[:300]
    return str(message or "")[:300]


async def _watch_media_tasks(now: datetime) -> int:
    db = SessionLocal()
    try:
        pending = _media_tasks_from_events(db, now)
        tokens: Dict[int, Optional[str]] = {}
        for item in pending:
            user_id = int(item["user_id"])
            if user_id not in tokens:
                tokens[user_id] = _mint_user_token(db, user_id)
            token = tokens[user_id]
            if not token:
                continue
            key = _notice_key("media", item["capability_id"], item["task_id"])
            if _notice_already_sent(db, item["message_id"], key):
                continue
            task = await _poll_capability_task(
                token,
                item["capability_id"],
                item["task_id"],
                item.get("installation_id") or "",
            )
            if not task:
                continue
            status = _media_status(task)
            terminal = bool(task.get("terminal")) or status in _SUCCESS_STATUSES or status in _FAILURE_STATUSES
            age_minutes = (now - item["created_at"]).total_seconds() / 60.0 if item["created_at"] else 0.0
            urls = _collect_urls(task)
            if terminal and status in _FAILURE_STATUSES:
                text = (
                    f"生成任务失败（{item['capability_id']}）："
                    f"{_truncate(_task_error_text(task) or status)}。可以让我重试或换参数。"
                )
                card_status = "failed"
            elif terminal:
                lines = [f"生成完成（{item['capability_id']}）。"]
                if urls:
                    lines.append("产物：" + "、".join(urls))
                lines.append("需要我继续发布、改写或做二次处理，直接说就行。")
                text = "\n".join(lines)
                card_status = "done"
            elif age_minutes >= _MEDIA_TIMEOUT_MINUTES:
                text = (
                    f"生成任务还在处理（{item['capability_id']}，已等待 {int(age_minutes)} 分钟）。"
                    "我会继续等，出结果就通知你；你也可以让我先做别的。"
                )
                card_status = "running"
            else:
                # 还在生成：先更新进度卡（让用户离开页面也能看到在跑）
                upsert_task_card(
                    db,
                    message_id=item["message_id"],
                    user_id=user_id,
                    status="running",
                    title=str(item["capability_id"]),
                    text=f"「{item['capability_id']}」生成中，已等待 {int(age_minutes)} 分钟。",
                    source="media",
                )
                db.commit()
                continue
            upsert_task_card(
                db,
                message_id=item["message_id"],
                user_id=user_id,
                status=card_status,
                title=str(item["capability_id"]),
                text=text,
                artifacts=urls,
                source="media",
            )
            _push_notice(
                db,
                user_id=user_id,
                installation_id="",
                text=text,
                root_message_id=item["message_id"],
                key=key,
            )
        return len(pending)
    finally:
        db.close()


def _watch_online_children(now: datetime, *, window_minutes: Optional[float] = None) -> int:
    db = SessionLocal()
    try:
        if window_minutes is None:
            cutoff = now - timedelta(hours=_ONLINE_TTL_HOURS)
        else:
            cutoff = now - timedelta(minutes=max(1.0, float(window_minutes)))
        # 1) 还在跑的：只更新进度卡（"执行中"），不发通知
        active_children = (
            db.query(H5ChatMessage)
            .filter(
                H5ChatMessage.parent_message_id.isnot(None),
                H5ChatMessage.status.in_(("pending", "processing")),
                H5ChatMessage.updated_at >= cutoff,
            )
            .order_by(H5ChatMessage.updated_at.desc())
            .limit(200)
            .all()
        )
        for child in active_children:
            summary = _online_child_summary(db, child)
            if not summary:
                continue
            upsert_task_card(
                db,
                message_id=summary["root_id"],
                user_id=summary["user_id"],
                status="running",
                title=summary["title"],
                text=f"「{summary['title']}」正在执行（后台任务，可以离开页面）。",
                source="online",
            )
        db.commit()

        children = (
            db.query(H5ChatMessage)
            .filter(
                H5ChatMessage.parent_message_id.isnot(None),
                H5ChatMessage.status.in_(("completed", "failed")),
                H5ChatMessage.updated_at >= cutoff,
            )
            .order_by(H5ChatMessage.updated_at.desc())
            .limit(200)
            .all()
        )
        notified = 0
        for child in children:
            try:
                summary = _online_child_summary(db, child)
                if not summary:
                    continue
                key = summary["key"]
                root_message_id = summary["root_id"]
                # 进度卡终态（含产物），和下面的通知互为补充
                upsert_task_card(
                    db,
                    message_id=root_message_id,
                    user_id=summary["user_id"],
                    status="failed" if summary["failed"] else "done",
                    title=summary["title"],
                    text=summary["notice_text"],
                    artifacts=summary["urls"],
                    source="online",
                )
                if _notice_already_sent(db, root_message_id, key):
                    db.commit()
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


async def watch_once() -> Dict[str, int]:
    global _cold_start_done
    now = datetime.utcnow()
    media_scanned = await _watch_media_tasks(now)
    if _cold_start_done:
        online_notified = _watch_online_children(now)
    else:
        # 第一轮只补最近几分钟的，避免发版/重启后把囤了几小时的任务一次性推给用户
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
