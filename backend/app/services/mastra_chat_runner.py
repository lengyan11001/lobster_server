from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections import Counter
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
    queue_mode: str
    target_message_id: str
    permission_mode: str
    approval_granted: bool
    approval_id: str
    authorization: str
    recent_history: List[Dict[str, str]]
    conversation_summary: str
    summary_messages: List[Dict[str, str]]
    summary_through_message_id: str


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


def _max_concurrency_per_user() -> int:
    try:
        return max(1, min(4, int(os.environ.get("LOBSTER_MASTRA_MAX_CONCURRENCY_PER_USER") or "1")))
    except (TypeError, ValueError):
        return 1


def _summary_keep_turns() -> int:
    try:
        return max(3, min(12, int(os.environ.get("LOBSTER_MASTRA_SUMMARY_KEEP_TURNS") or "5")))
    except (TypeError, ValueError):
        return 5


def _summary_min_turns() -> int:
    try:
        return max(2, min(20, int(os.environ.get("LOBSTER_MASTRA_SUMMARY_MIN_TURNS") or "4")))
    except (TypeError, ValueError):
        return 4


def _summary_min_chars() -> int:
    try:
        return max(4000, min(60000, int(os.environ.get("LOBSTER_MASTRA_SUMMARY_MIN_CHARS") or "12000")))
    except (TypeError, ValueError):
        return 12000


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


def _recent_mastra_history(db, row: H5ChatMessage) -> List[Dict[str, str]]:
    previous = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.user_id == row.user_id,
            H5ChatMessage.session_id == row.session_id,
            H5ChatMessage.parent_message_id.is_(None),
            H5ChatMessage.id != row.id,
            H5ChatMessage.mode == "mastra",
            H5ChatMessage.status == "completed",
            H5ChatMessage.created_at < row.created_at,
        )
        .order_by(H5ChatMessage.created_at.desc(), H5ChatMessage.id.desc())
        .limit(_summary_keep_turns())
        .all()
    )
    messages: List[Dict[str, str]] = []
    for previous_row in reversed(previous):
        content = (previous_row.content or "").strip()
        if content:
            messages.append({"role": "user", "content": content[:6000]})
        reply = (previous_row.reply_text or previous_row.error or "").strip()
        if reply:
            messages.append({"role": "assistant", "content": reply[:10000]})
    return messages


def _summary_context(
    db,
    row: H5ChatMessage,
    session: Optional[H5ChatSession],
) -> tuple[str, List[Dict[str, str]], str]:
    if session is None:
        return "", [], ""
    existing_summary = (session.summary_text or "").strip()[:16000]
    previous = (
        db.query(H5ChatMessage)
        .filter(
            H5ChatMessage.user_id == row.user_id,
            H5ChatMessage.session_id == row.session_id,
            H5ChatMessage.parent_message_id.is_(None),
            H5ChatMessage.mode == "mastra",
            H5ChatMessage.status == "completed",
            H5ChatMessage.created_at < row.created_at,
        )
        .order_by(H5ChatMessage.created_at.asc(), H5ChatMessage.id.asc())
        .all()
    )
    keep_turns = _summary_keep_turns()
    compressible = previous[:-keep_turns] if len(previous) > keep_turns else []
    cursor = (session.summary_through_message_id or "").strip()
    if cursor:
        cursor_index = next((index for index, item in enumerate(compressible) if item.id == cursor), None)
        if cursor_index is not None:
            compressible = compressible[cursor_index + 1 :]
        else:
            cursor_row = db.query(H5ChatMessage).filter(H5ChatMessage.id == cursor).first()
            if cursor_row and cursor_row.created_at:
                compressible = [item for item in compressible if item.created_at > cursor_row.created_at]

    total_chars = sum(len(item.content or "") + len(item.reply_text or "") for item in compressible)
    if len(compressible) < _summary_min_turns() and total_chars < _summary_min_chars():
        return existing_summary, [], ""

    messages: List[Dict[str, str]] = []
    used_chars = 0
    through_id = ""
    for item in compressible[:12]:
        user_text = (item.content or "").strip()[:8000]
        assistant_text = (item.reply_text or item.error or "").strip()[:12000]
        remaining = 48000 - used_chars
        if remaining <= 0:
            break
        if len(user_text) + len(assistant_text) > remaining:
            assistant_budget = max(0, remaining - min(len(user_text), 8000))
            user_text = user_text[: min(len(user_text), remaining)]
            assistant_text = assistant_text[:assistant_budget]
        messages.append({"message_id": item.id, "user": user_text, "assistant": assistant_text})
        used_chars += len(user_text) + len(assistant_text)
        through_id = item.id
    return existing_summary, messages, through_id


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


def _claim_jobs_sync(
    limit: int,
    running_user_counts: Optional[Dict[int, int]] = None,
    per_user_limit: Optional[int] = None,
) -> List[MastraChatJob]:
    db = SessionLocal()
    try:
        per_user_limit = per_user_limit or _max_concurrency_per_user()
        user_counts = Counter(running_user_counts or {})
        query = (
            db.query(H5ChatMessage)
            .filter(H5ChatMessage.mode == "mastra", H5ChatMessage.status == "pending")
            .order_by(
                H5ChatMessage.queue_priority.desc(),
                H5ChatMessage.created_at.asc(),
                H5ChatMessage.id.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(max(100, limit * 25))
        )
        rows = query.all()
        if not rows:
            return []

        now = datetime.utcnow()
        jobs: List[MastraChatJob] = []
        for row in rows:
            if len(jobs) >= max(1, limit):
                break
            if user_counts[int(row.user_id)] >= per_user_limit:
                continue
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
            conversation_summary, summary_messages, summary_through_message_id = _summary_context(db, row, session)
            approval = (
                db.query(H5ChatApproval)
                .filter(H5ChatApproval.message_id == row.id, H5ChatApproval.status == "approved")
                .order_by(H5ChatApproval.decided_at.desc(), H5ChatApproval.created_at.desc())
                .first()
            )
            if approval:
                approval.status = "executing"
                approval.updated_at = now

            content = (row.content or "").strip()
            attachments = list(row.attachments or [])
            if row.queue_mode == "steer" and row.target_message_id:
                target = (
                    db.query(H5ChatMessage)
                    .filter(
                        H5ChatMessage.id == row.target_message_id,
                        H5ChatMessage.user_id == row.user_id,
                        H5ChatMessage.session_id == row.session_id,
                    )
                    .first()
                )
                if target:
                    original = (target.content or "").strip()
                    content = (
                        "请根据用户刚补充的要求重新处理当前任务。\n\n"
                        f"原任务：\n{original}\n\n"
                        f"补充要求：\n{content}"
                    )
                    combined = list(target.attachments or []) + attachments
                    attachment_keys = set()
                    attachments = []
                    for item in combined:
                        key = str(item.get("asset_id") or item.get("url") or item.get("name") or "")
                        if key and key in attachment_keys:
                            continue
                        if key:
                            attachment_keys.add(key)
                        attachments.append(item)

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
                    content=content,
                    attachments=attachments,
                    queue_mode=(row.queue_mode or "normal").strip(),
                    target_message_id=(row.target_message_id or "").strip(),
                    permission_mode=permission_mode,
                    approval_granted=bool(approval),
                    approval_id=approval.id if approval else "",
                    authorization=create_access_token(access_token_claims(user)),
                    recent_history=_recent_mastra_history(db, row),
                    conversation_summary=conversation_summary,
                    summary_messages=summary_messages,
                    summary_through_message_id=summary_through_message_id,
                )
            )
            user_counts[int(row.user_id)] += 1
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


def _update_summary_sync(job: MastraChatJob, summary_text: str) -> None:
    db = SessionLocal()
    try:
        session = (
            db.query(H5ChatSession)
            .filter(H5ChatSession.id == job.session_id, H5ChatSession.user_id == job.user_id)
            .first()
        )
        if session is None or not job.summary_through_message_id:
            return
        session.summary_text = (summary_text or "").strip()[:16000] or session.summary_text
        session.summary_through_message_id = job.summary_through_message_id
        session.summary_updated_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def _refresh_conversation_summary(job: MastraChatJob) -> str:
    if not job.summary_messages or not job.summary_through_message_id:
        return job.conversation_summary
    timeout = httpx.Timeout(connect=8.0, read=240.0, write=30.0, pool=8.0)
    payload = {
        "authorization": job.authorization,
        "brand": job.brand,
        "user_id": str(job.user_id),
        "installation_id": job.installation_id,
        "parent_message_id": job.message_id,
        "existing_summary": job.conversation_summary,
        "messages": job.summary_messages,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                f"{_mastra_base_url()}/internal/summarize",
                headers={
                    "Content-Type": "application/json",
                    "X-Lobster-Mastra-Secret": _internal_secret(),
                },
                json=payload,
            )
        response.raise_for_status()
        data = response.json()
        summary = str(data.get("summary") or "").strip()[:16000]
        if not summary:
            return job.conversation_summary
        await asyncio.to_thread(_update_summary_sync, job, summary)
        logger.info(
            "[mastra_chat] compacted session=%s through=%s turns=%s",
            job.session_id,
            job.summary_through_message_id,
            len(job.summary_messages),
        )
        return summary
    except Exception as exc:
        logger.warning("[mastra_chat] summary refresh skipped session=%s error=%s", job.session_id, str(exc)[:300])
        return job.conversation_summary


async def _run_job_request(job: MastraChatJob) -> None:
    conversation_summary = await _refresh_conversation_summary(job)
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
        "recent_history": job.recent_history,
        "conversation_summary": conversation_summary,
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
                        if len(delta_buffer) >= 160 or elapsed >= 0.25:
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


def _message_cancelled_sync(message_id: str) -> bool:
    db = SessionLocal()
    try:
        row = db.query(H5ChatMessage.status).filter(H5ChatMessage.id == message_id).first()
        return row is None or str(row[0] or "").lower() == "cancelled"
    finally:
        db.close()


async def _wait_for_message_cancellation(message_id: str) -> None:
    while True:
        await asyncio.sleep(0.5)
        try:
            if await asyncio.to_thread(_message_cancelled_sync, message_id):
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[mastra_chat] cancellation check failed message=%s error=%s", message_id, str(exc)[:300])


async def _run_job(job: MastraChatJob) -> None:
    if await asyncio.to_thread(_message_cancelled_sync, job.message_id):
        return
    request_task = asyncio.create_task(_run_job_request(job), name=f"mastra-request-{job.message_id[:8]}")
    cancel_task = asyncio.create_task(
        _wait_for_message_cancellation(job.message_id),
        name=f"mastra-cancel-watch-{job.message_id[:8]}",
    )
    try:
        done, _ = await asyncio.wait(
            {request_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if request_task in done:
            await request_task
            return
        request_task.cancel()
        await asyncio.gather(request_task, return_exceptions=True)
        logger.info("[mastra_chat] cancelled active request message=%s", job.message_id)
    finally:
        cancel_task.cancel()
        await asyncio.gather(cancel_task, return_exceptions=True)


async def mastra_chat_background_loop() -> None:
    if not _enabled():
        logger.info("[mastra_chat] runner disabled")
        while True:
            await asyncio.sleep(3600)

    recovered = await asyncio.to_thread(_recover_stale_sync)
    if recovered:
        logger.warning("[mastra_chat] recovered %s stale message(s)", recovered)

    concurrency = _max_concurrency()
    per_user_limit = _max_concurrency_per_user()
    running: Dict[asyncio.Task, int] = {}
    logger.info("[mastra_chat] runner started concurrency=%s per_user=%s", concurrency, per_user_limit)
    while True:
        finished = {task for task in running if task.done()}
        for task in finished:
            running.pop(task, None)
            try:
                task.result()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[mastra_chat] job task failed")

        capacity = concurrency - len(running)
        if capacity > 0:
            try:
                running_user_counts = dict(Counter(running.values()))
                jobs = await asyncio.to_thread(_claim_jobs_sync, capacity, running_user_counts, per_user_limit)
            except Exception:
                logger.exception("[mastra_chat] failed to claim messages")
                jobs = []
            for job in jobs:
                task = asyncio.create_task(_run_job(job), name=f"mastra-chat-{job.message_id[:8]}")
                running[task] = job.user_id

        if running:
            try:
                await asyncio.wait(set(running), timeout=_poll_interval_seconds(), return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                for task in running:
                    task.cancel()
                await asyncio.gather(*running.keys(), return_exceptions=True)
                raise
        else:
            await asyncio.sleep(_poll_interval_seconds())
