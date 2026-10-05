"""生成任务对账补偿：已预扣但前端没轮询到的任务，后台去上游对一次账。

背景（2026-10-05）：用户画布生成 nano banana 时前端轮询断了（前端只提示
「暂时无法读取任务状态」），上游其实已经 completed —— 结果既没登记、预扣也没退。
这里做后台对账：

- 上游 completed      → 登记产物（画布任务记录 + 内容记录，用户能看到）
- 上游 failed         → 退回预扣
- 上游 404 / 任务不存在 → 退回预扣并标记

服务器压力控制：
- 每批最多 GENERATION_RECONCILE_BATCH 条（默认 5，上限 50）；
- 只挑「预扣后 ≥2 分钟、≤48 小时」的任务（太新的让前端自己轮询，太旧的放弃）；
- 逐条串行 + 条间间隔 GENERATION_RECONCILE_GAP_SECONDS（默认 0.8s）；
- 轮次间隔 GENERATION_RECONCILE_INTERVAL_SECONDS（默认 60s），一轮没活也照常睡。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from ..api import canvas_hub

logger = logging.getLogger(__name__)

DEFAULT_BATCH = 5
DEFAULT_INTERVAL_SECONDS = 60.0
MIN_AGE_SECONDS = 120.0
MAX_AGE_HOURS = 48.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name) or default))
    except (TypeError, ValueError):
        return int(default)


def is_generation_reconcile_enabled() -> bool:
    value = str(os.environ.get("GENERATION_RECONCILE_ENABLED", "1")).strip().lower()
    return value not in {"0", "false", "off", "no", ""}


def classify_upstream(payload: Any) -> "tuple[str, str, str]":
    """把上游返回归一成 (verdict, result_url, detail)。

    verdict ∈ completed / failed / missing / pending
    """
    if not isinstance(payload, dict):
        return "pending", "", ""
    data = payload.get("data")
    if not isinstance(data, dict):
        detail = str(payload.get("detail") or payload.get("message") or "")
        try:
            code = int(payload.get("code") or 0)
        except (TypeError, ValueError):
            code = 0
        if "任务不存在" in detail or code == 404 or str(payload.get("code")) == "404":
            return "missing", "", detail or "任务不存在"
        return "pending", "", detail[:200]
    status = str(data.get("status") or "").strip().lower()
    url = canvas_hub._extract_result_url(data.get("output") or data.get("result") or data)
    if status in {"completed", "succeed", "success", "done"}:
        return "completed", url, ""
    if status in {"failed", "fail", "error", "webhook_error"}:
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        return "failed", url, str(output.get("error") or data.get("message") or "")[:200]
    return "pending", url, ""


def _media_type_for(url: str) -> str:
    lowered = str(url or "").lower()
    if any(token in lowered for token in (".mp4", ".mov", ".webm", "/video/")):
        return "video"
    if any(token in lowered for token in (".mp3", ".wav", ".m4a", ".aac", "/audio/")):
        return "audio"
    return "image"


def _due_canvas_rows(db, limit: int) -> List[Dict[str, Any]]:
    now = time.time()
    rows = db.execute(text("""
        select id, user_id, task_id, model, path, charged, created_at
          from canvas_task
         where coalesce(task_id, '') <> ''
           and coalesce(charged, 0) > 0
           and coalesce(refunded, false) = false
           and status in ('submitted', 'processing', 'running', 'pending')
           and created_at <= :min_ts
           and created_at >= :max_ts
         order by id asc
         limit :limit
    """), {"limit": int(limit), "min_ts": now - MIN_AGE_SECONDS,
           "max_ts": now - MAX_AGE_HOURS * 3600.0}).fetchall()
    return [dict(row._mapping) for row in rows]


async def reconcile_canvas_once(*, limit: Optional[int] = None, dry_run: bool = False) -> Dict[str, Any]:
    """对账一批画布任务（只处理「预扣了但还没结算」的）。"""
    from ..api import canvas_proxy
    from ..db import SessionLocal
    from ..models import User

    batch = max(1, min(int(limit or _env_int("GENERATION_RECONCILE_BATCH", DEFAULT_BATCH)), 50))
    gap = max(0.0, min(_env_float("GENERATION_RECONCILE_GAP_SECONDS", 0.8), 10.0))
    stats = {"checked": 0, "completed": 0, "failed": 0, "missing": 0, "pending": 0, "errors": 0}
    db = SessionLocal()
    try:
        rows = _due_canvas_rows(db, batch)
        stats["checked"] = len(rows)
        for row in rows:
            task_id = str(row.get("task_id") or "")
            user_id = int(row.get("user_id") or 0)
            model = str(row.get("model") or "")
            charged = float(row.get("charged") or 0)
            try:
                payload = await canvas_hub.apiz_json("POST", "/api/v3/tasks/query", {"task_id": task_id})
            except Exception as exc:  # noqa: BLE001 单条失败不影响其它
                stats["errors"] += 1
                logger.warning("[reconcile] 查询上游失败 task=%s: %s", task_id, exc)
                await asyncio.sleep(gap)
                continue
            verdict, url, detail = classify_upstream(payload)
            if verdict not in stats:
                verdict = "pending"
            stats[verdict] += 1
            if not dry_run and verdict != "pending":
                try:
                    if verdict == "completed" and url:
                        canvas_hub.sync_canvas_task(
                            db, user_id, task_id=task_id, status="completed", result_url=url,
                            params=json_dumps_safe((payload.get("data") or {}).get("params")),
                            model=model,
                        )
                        canvas_hub.register_content_record(
                            db, user_id, url, media_type=_media_type_for(url),
                            title="%s 生成" % (model or "canvas"), task_id=task_id, model=model,
                            extra={"reconciled": True},
                        )
                        logger.info("[reconcile] task=%s 已补登记产物 %s", task_id, url[:80])
                    else:
                        user = db.query(User).filter(User.id == user_id).first()
                        if user is not None and charged > 0:
                            canvas_proxy.refund_canvas(
                                db, user, charged, model,
                                str(row.get("path") or "api/v3/tasks/create"),
                                f"对账补退（上游 {verdict}）",
                            )
                            canvas_hub.mark_canvas_task_refunded(db, user_id, task_id=task_id)
                        canvas_hub.sync_canvas_task(db, user_id, task_id=task_id, status="failed",
                                                    error=(detail or verdict)[:200], model=model)
                        logger.info("[reconcile] task=%s 判定 %s，已退预扣 %s", task_id, verdict, charged)
                except Exception as exc:  # noqa: BLE001
                    stats["errors"] += 1
                    logger.warning("[reconcile] 结单失败 task=%s: %s", task_id, exc)
            await asyncio.sleep(gap)
    finally:
        db.close()
    return stats


def json_dumps_safe(value: Any) -> str:
    import json

    try:
        return json.dumps(value or {}, ensure_ascii=False)[:8000]
    except Exception:  # noqa: BLE001
        return ""


async def reconcile_douyin_imitation_once(*, limit: Optional[int] = None) -> Dict[str, Any]:
    """跟创（做同款）对账：RUNNING 卡住的，去上游查一次，失败就退款。"""
    from ..api import douyin_platform_information_desk as desk_api
    from ..db import SessionLocal
    from ..models import DouyinImitationTask, User
    from . import douyin_desk_billing as billing

    batch = max(1, min(int(limit or _env_int("GENERATION_RECONCILE_BATCH", DEFAULT_BATCH)), 50))
    stats = {"checked": 0, "completed": 0, "failed": 0, "errors": 0}
    db = SessionLocal()
    try:
        rows = (db.query(DouyinImitationTask)
                .filter(DouyinImitationTask.status == "RUNNING")
                .order_by(DouyinImitationTask.id.asc())
                .limit(batch).all())
        stats["checked"] = len(rows)
        for row in rows:
            try:
                result = await desk_api.query_imitation(str(row.task_id or ""))
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                logger.warning("[reconcile] 跟创查询失败 task=%s: %s", row.task_id, exc)
                continue
            if not result.get("ok") or not result.get("done"):
                continue
            if result.get("status") == "SUCCESS":
                row.status = "SUCCESS"
                row.video_url = str(result.get("video_url") or row.video_url or "")
                stats["completed"] += 1
            else:
                row.status = "FAILED"
                row.fail_reason = str(result.get("fail_reason") or "生成失败")[:255]
                stats["failed"] += 1
                user = db.query(User).filter(User.id == int(row.user_id)).first()
                if user is not None and float(row.credits_charged or 0) > 0 and not float(row.credits_refunded or 0):
                    billing.refund(db, user, billing.Decimal(str(row.credits_charged)),
                                   reason="douyin_imitation_reconcile_failed")
                    row.credits_refunded = row.credits_charged
            db.commit()
    finally:
        db.close()
    return stats


async def generation_reconcile_loop(interval_seconds: float = DEFAULT_INTERVAL_SECONDS) -> None:
    """后台循环：每轮各跑一批画布 / 跟创对账（串行、有间隔，避免压上游）。"""
    interval = max(20.0, float(interval_seconds))
    while True:
        try:
            canvas_stats = await reconcile_canvas_once()
            if canvas_stats.get("checked"):
                logger.info("[reconcile] canvas %s", canvas_stats)
            imitation_stats = await reconcile_douyin_imitation_once()
            if imitation_stats.get("checked"):
                logger.info("[reconcile] douyin_imitation %s", imitation_stats)
        except Exception:  # noqa: BLE001 循环不能死
            logger.exception("[reconcile] loop error")
        await asyncio.sleep(interval)
