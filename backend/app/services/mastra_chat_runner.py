from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from ..api.auth import access_token_claims, create_access_token
from ..api.h5_chat import _add_event, _finish_mastra_parent_from_children
from ..db import SessionLocal
from ..models import H5ChatApproval, H5ChatMessage, H5ChatSession, User
from .brand_context import user_brand_mark

logger = logging.getLogger(__name__)

_WORKER_ID = "mastra-server"
_FINAL_STATUSES = {"completed", "failed", "cancelled"}
_STREAM_EVENT_TYPES = {"thinking", "tool_start", "tool_end", "progress"}


@dataclass(frozen=True)
class MastraChatJob:
    message_id: str
    user_id: int
    brand: str
    installation_id: str
    session_id: str
    content: str
    attachments: List[Dict[str, Any]]
    permission_mode: str
    approval_granted: bool
    approval_id: str
    authorization: str
    legacy_history: List[Dict[str, str]]


def _enabled() -> bool:
    return (os.environ.get("LOBSTER_MASTRA_CHAT_ENABLED") or "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _max_concurrency() -> int:
    try:
        return max(1, min(12, int(os.environ.get("LOBSTER_MASTRA_MAX_CONCURRENCY") or "4")))
    except (TypeError, ValueError):
        return 4


def _poll_interval_seconds() -> float:
    try:
        return max(0.25, min(10.0, float(os.environ.get("LOBSTER_MASTRA_POLL_SECONDS") or "1")))
    except (TypeError, ValueError):
        return 1.0


def _stale_after_seconds() -> int:
    try:
        return max(300, int(os.environ.get("LOBSTER_MASTRA_STALE_SECONDS") or "1200"))
    except (TypeError, ValueError):
        return 1200


def _mastra_base_url() -> str:
    return (os.environ.get("LOBSTER_MASTRA_URL") or "http://127.0.0.1:4111").strip().rstrip("/")


def _internal_secret() -> str:
    configured = (os.environ.get("LOBSTER_MASTRA_INTERNAL_SECRET") or "").strip()
    if configured:
        return configured
    app_secret = (os.environ.get("LOBSTER_SECRET_KEY") or os.environ.get("SECRET_KEY") or "").strip()
    if not app_secret:
        from ..core.config import settings

        app_secret = str(settings.secret_key or "").strip()
    return hashlib.sha256(f"{app_secret}:lobster-mastra".encode("utf-8")).hexdigest()


def _legacy_history(db, row: H5ChatMessage) -> List[Dict[str, str]]:
    previous = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.user_id == row.user_id,
            H5ChatMessage.parent_message_id.is_(None),
            H5ChatMessage.id != row.id,
            H5ChatMessage.created_at < row.created_at,
            H5ChatMessage.session_id == row.session_id,
            H5ChatMessage.mode != "mastra",
        )
        .order_by(H5ChatMessage.created_at.desc())
        .limit(8)
        .all()
    )
    messages: List[Dict[str, str]] = []
    for previous_row in reversed(previous):
        content = (previous_row.content or "").strip()
        if content:
            messages.append({"role": "user", "content": content[:8000]})
        reply = (previous_row.reply_text or previous_row.error or "").strip()
        if reply:
            messages.append({"role": "assistant", "content": reply[:12000]})
    return messages


def _recover_stale_sync() -> int:
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(seconds=_stale_after_seconds())
        rows = (
            db.query(H5ChatMessage)
            .filter(
                H5ChatMessage.mode == "mastra",
                H5ChatMessage.status == "processing",
                H5ChatMessage.updated_at < cutoff,
            )
            .limit(100)
            .all()
        )
        now = datetime.utcnow()
        for row in rows:
            approvals = db.query(H5ChatApproval).filter(
                H5ChatApproval.message_id == row.id,
                H5ChatApproval.status == "executing",
            ).all()
            for approval in approvals:
                approval.status = "approved"
                approval.updated_at = now
            row.status = "pending"
            row.claimed_by_installation_id = None
            row.claimed_at = None
            row.updated_at = now
            _add_event(db, row, "queued", {"text": "服务恢复后继续处理"})
        if rows:
            db.commit()
        return len(rows)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _claim_jobs_sync(limit: int) -> List[MastraChatJob]:
    db = SessionLocal()
    try:
        query = (
            db.query(H5ChatMessage)
            .filter(H5ChatMessage.mode == "mastra", H5ChatMessage.status == "pending")
            .order_by(H5ChatMessage.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(max(1, limit))
        )
        rows = query.all()
        if not rows:
            return []

        now = datetime.utcnow()
        jobs: List[MastraChatJob] = []
        for row in rows:
            user = db.query(User).filter(User.id == row.user_id).first()
            if user is None:
                row.status = "failed"
                row.error = "账号不存在，无法继续处理"
                row.finished_at = now
                row.updated_at = now
                _add_event(db, row, "error", {"error": row.error})
                continue

            session = (
                db.query(H5ChatSession)
                .filter(H5ChatSession.id == row.session_id, H5ChatSession.user_id == row.user_id)
                .first()
            )
            permission_mode = "full" if session and session.permission_mode == "full" else "confirm"
            approval = (
                db.query(H5ChatApproval)
                .filter(H5ChatApproval.message_id == row.id, H5ChatApproval.status == "approved")
                .order_by(H5ChatApproval.decided_at.desc(), H5ChatApproval.created_at.desc())
                .first()
            )
            if approval:
                approval.status = "executing"
                approval.updated_at = now

            row.status = "processing"
            row.claimed_by_installation_id = _WORKER_ID
            row.claimed_at = now
            row.updated_at = now
            _add_event(db, row, "claimed", {"text": "AI 调度助手已接收"})
            jobs.append(
                MastraChatJob(
                    message_id=row.id,
                    user_id=row.user_id,
                    brand=user_brand_mark(user),
                    installation_id=(row.installation_id or "").strip(),
                    session_id=(row.session_id or "default").strip(),
                    content=(row.content or "").strip(),
                    attachments=list(row.attachments or []),
                    permission_mode=permission_mode,
                    approval_granted=bool(approval),
                    approval_id=approval.id if approval else "",
                    authorization=create_access_token(access_token_claims(user)),
                    legacy_history=_legacy_history(db, row),
                )
            )
        db.commit()
        return jobs
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _append_event_sync(message_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    db = SessionLocal()
    try:
        row = db.query(H5ChatMessage).filter(H5ChatMessage.id == message_id).first()
        if row is None or row.status in _FINAL_STATUSES:
            return
        row.updated_at = datetime.utcnow()
        _add_event(db, row, event_type, payload or {})
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _complete_sync(
    message_id: str,
    reply: str,
    dispatches: List[Dict[str, Any]],
    usage: Optional[Dict[str, Any]],
) -> None:
    db = SessionLocal()
    try:
        row = db.query(H5ChatMessage).filter(H5ChatMessage.id == message_id).first()
        if row is None:
            return
        if row.status in _FINAL_STATUSES:
            return
        clean_reply = (reply or "").strip() or (
            "任务已下发，正在等待 Online 执行。" if dispatches else "处理完成。"
        )
        row.reply_text = clean_reply
        row.error = None
        row.updated_at = datetime.utcnow()

        approved_approval = (
            db.query(H5ChatApproval)
            .filter(H5ChatApproval.message_id == row.id, H5ChatApproval.status == "approved")
            .order_by(H5ChatApproval.decided_at.desc(), H5ChatApproval.created_at.desc())
            .first()
        )
        if approved_approval:
            row.status = "pending"
            row.claimed_by_installation_id = None
            row.claimed_at = None
            row.updated_at = datetime.utcnow()
            _add_event(
                db,
                row,
                "queued",
                {"text": "已确认执行，正在开始任务", "approval_id": approved_approval.id},
            )
            db.commit()
            return

        pending_approval = (
            db.query(H5ChatApproval)
            .filter(H5ChatApproval.message_id == row.id, H5ChatApproval.status == "pending")
            .order_by(H5ChatApproval.created_at.desc())
            .first()
        )
        if pending_approval:
            row.status = "processing"
            row.claimed_by_installation_id = None
            row.claimed_at = None
            _add_event(
                db,
                row,
                "progress",
                {
                    "text": "执行方案已准备，等待你的确认",
                    "reply_text": clean_reply,
                    "approval_id": pending_approval.id,
                },
            )
            db.commit()
            return

        children = (
            db.query(H5ChatMessage)
            .filter(H5ChatMessage.parent_message_id == row.id, H5ChatMessage.user_id == row.user_id)
            .order_by(H5ChatMessage.created_at.asc())
            .all()
        )
        if children:
            row.status = "processing"
            _add_event(
                db,
                row,
                "progress",
                {
                    "text": "已完成调度，等待 Online 返回执行结果",
                    "reply_text": clean_reply,
                    "online_message_ids": [child.id for child in children],
                },
            )
            _finish_mastra_parent_from_children(db, children[-1])
        else:
            now = datetime.utcnow()
            row.status = "completed"
            row.finished_at = now
            row.updated_at = now
            _add_event(
                db,
                row,
                "final",
                {"reply_text": clean_reply, "dispatches": dispatches, "usage": usage or {}},
            )
            approvals = db.query(H5ChatApproval).filter(
                H5ChatApproval.message_id == row.id,
                H5ChatApproval.status == "executing",
            ).all()
            for approval in approvals:
                approval.status = "completed"
                approval.finished_at = now
                approval.updated_at = now
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _fallback_or_fail_sync(message_id: str, error: str) -> str:
    db = SessionLocal()
    try:
        row = db.query(H5ChatMessage).filter(H5ChatMessage.id == message_id).first()
        if row is None or row.status in _FINAL_STATUSES:
            return "ignored"
        child_exists = (
            db.query(H5ChatMessage.id)
            .filter(H5ChatMessage.parent_message_id == row.id, H5ChatMessage.user_id == row.user_id)
            .first()
            is not None
        )
        now = datetime.utcnow()
        if child_exists:
            row.status = "processing"
            row.reply_text = row.reply_text or "任务已下发，正在等待 Online 执行。"
            row.updated_at = now
            _add_event(db, row, "progress", {"text": "已下发的任务继续由 Online 处理"})
            result = "waiting_online"
        else:
            row.mode = "direct"
            row.status = "pending"
            row.claimed_by_installation_id = None
            row.claimed_at = None
            row.updated_at = now
            _add_event(
                db,
                row,
                "progress",
                {"text": "AI 调度服务暂时不可用，已自动转交 Online 处理"},
            )
            result = "fallback_online"
        db.commit()
        logger.warning("[mastra_chat] message=%s result=%s error=%s", message_id, result, error[:500])
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def _append_event(message_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    await asyncio.to_thread(_append_event_sync, message_id, event_type, payload)


async def _run_job(job: MastraChatJob) -> None:
    body = {
        "message": job.content,
        "attachments": job.attachments,
        "authorization": job.authorization,
        "brand": job.brand,
        "user_id": str(job.user_id),
        "installation_id": job.installation_id,
        "session_id": job.session_id,
        "parent_message_id": job.message_id,
        "thread_id": f"h5:{job.brand}:{job.user_id}:{job.session_id}",
        "resource_id": f"{job.brand}:{job.user_id}",
        "legacy_history": job.legacy_history,
        "permission_mode": job.permission_mode,
        "approval_granted": job.approval_granted,
        "approval_id": job.approval_id,
    }
    timeout = httpx.Timeout(connect=8.0, read=900.0, write=30.0, pool=8.0)
    delta_buffer = ""
    last_delta_flush = asyncio.get_running_loop().time()
    final_received = False

    async def flush_delta() -> None:
        nonlocal delta_buffer, last_delta_flush
        if not delta_buffer:
            return
        text = delta_buffer
        delta_buffer = ""
        last_delta_flush = asyncio.get_running_loop().time()
        await _append_event(job.message_id, "delta", {"text": text})

    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            async with client.stream(
                "POST",
                f"{_mastra_base_url()}/internal/chat/stream",
                headers={
                    "Content-Type": "application/json",
                    "X-Lobster-Mastra-Secret": _internal_secret(),
                },
                json=body,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    event_type = str(event.get("type") or "").strip().lower()
                    if event_type == "delta":
                        delta_buffer += str(event.get("text") or "")
                        elapsed = asyncio.get_running_loop().time() - last_delta_flush
                        if len(delta_buffer) >= 80 or elapsed >= 0.15:
                            await flush_delta()
                        continue

                    await flush_delta()
                    if event_type in _STREAM_EVENT_TYPES:
                        payload = {key: value for key, value in event.items() if key != "type"}
                        await _append_event(job.message_id, event_type, payload)
                        continue
                    if event_type == "final":
                        final_received = True
                        await asyncio.to_thread(
                            _complete_sync,
                            job.message_id,
                            str(event.get("reply") or ""),
                            list(event.get("dispatches") or []),
                            event.get("usage") if isinstance(event.get("usage"), dict) else None,
                        )
                        continue
                    if event_type == "error":
                        raise RuntimeError(str(event.get("error") or "AI 调度失败"))
        await flush_delta()
        if not final_received:
            raise RuntimeError("AI 调度服务未返回最终结果")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await flush_delta()
        await asyncio.to_thread(_fallback_or_fail_sync, job.message_id, str(exc))


async def mastra_chat_background_loop() -> None:
    if not _enabled():
        logger.info("[mastra_chat] runner disabled")
        while True:
            await asyncio.sleep(3600)

    recovered = await asyncio.to_thread(_recover_stale_sync)
    if recovered:
        logger.warning("[mastra_chat] recovered %s stale message(s)", recovered)

    concurrency = _max_concurrency()
    running: set[asyncio.Task] = set()
    logger.info("[mastra_chat] runner started concurrency=%s", concurrency)
    while True:
        finished = {task for task in running if task.done()}
        for task in finished:
            running.remove(task)
            try:
                task.result()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[mastra_chat] job task failed")

        capacity = concurrency - len(running)
        if capacity > 0:
            try:
                jobs = await asyncio.to_thread(_claim_jobs_sync, capacity)
            except Exception:
                logger.exception("[mastra_chat] failed to claim messages")
                jobs = []
            for job in jobs:
                running.add(asyncio.create_task(_run_job(job), name=f"mastra-chat-{job.message_id[:8]}"))

        if running:
            try:
                await asyncio.wait(running, timeout=_poll_interval_seconds(), return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                for task in running:
                    task.cancel()
                await asyncio.gather(*running, return_exceptions=True)
                raise
        else:
            await asyncio.sleep(_poll_interval_seconds())
