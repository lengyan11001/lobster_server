"""确认后转入后台任务的进度卡（2026-09-20，v2 支持聚合 / 取消 / 产物回推）。

用户在对话里点「确认执行」后，任务转到后台（worker / mastra / 设备），但会话里只剩一句"已确认"。
这里把这张卡做成一条持久化的 `task_card` 事件挂在**原始请求消息**上：

  - 确认时立刻写一张（排队中）
  - 看护循环 mastra_task_watch 按真实状态更新（执行中 / 已完成 / 失败 / 已取消）
  - 一次请求下的多个任务聚合成一张卡（items 列表 + 整体状态）
  - 完成时把产物同时放进 media_urls，前端会直接贴预览图/播放器

对外：latest_task_card(db, message_id) / upsert_task_card(db, ...)（不提交事务，由调用方 commit）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import H5ChatEvent

CARD_EVENT = "task_card"

STATUS_LABELS = {
    "queued": "排队中",
    "running": "执行中",
    "done": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}

# 整体状态优先级：只要还有在跑的就算执行中；有失败且没有在跑就算失败；全部完成才算完成
_STATUS_RANK = {"running": 0, "queued": 1, "failed": 2, "cancelled": 3, "done": 4}
_ACTIVE_STATUSES = {"queued", "running"}

_MAX_ARTIFACTS = 6
_MAX_ITEMS = 12


def status_label(status: str) -> str:
    return STATUS_LABELS.get(str(status or "").strip().lower(), "进行中")


def overall_status(statuses: List[str], *, fallback: str = "running") -> str:
    """把多个子任务状态合成一个整体状态。"""
    known = [str(item or "").strip().lower() for item in statuses if str(item or "").strip()]
    if not known:
        return fallback
    if any(item in _ACTIVE_STATUSES for item in known):
        return "running"
    if any(item == "failed" for item in known):
        return "failed"
    if any(item == "cancelled" for item in known):
        return "cancelled" if all(item == "cancelled" for item in known) else "done"
    return "done"


def latest_task_card(db: Session, message_id: str) -> Optional[Dict[str, Any]]:
    if not message_id:
        return None
    row = (
        db.query(H5ChatEvent)
        .filter(H5ChatEvent.message_id == message_id, H5ChatEvent.event_type == CARD_EVENT)
        .order_by(H5ChatEvent.id.desc())
        .first()
    )
    payload = row.payload if row is not None and isinstance(row.payload, dict) else None
    return dict(payload) if payload else None


def normalize_artifacts(artifacts: Any, limit: int = _MAX_ARTIFACTS) -> List[str]:
    out: List[str] = []
    for item in artifacts or []:
        url = str(item or "").strip()
        if url and url not in out:
            out.append(url)
        if len(out) >= limit:
            break
    return out


def normalize_items(items: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or "").strip().lower() or "running"
        out.append(
            {
                "key": str(raw.get("key") or "")[:120],
                "title": str(raw.get("title") or "")[:80],
                "status": status,
                "status_label": status_label(status),
                "text": str(raw.get("text") or "")[:300],
                "artifacts": normalize_artifacts(raw.get("artifacts"), 4),
            }
        )
        if len(out) >= _MAX_ITEMS:
            break
    return out


def _same_items(left: Any, right: Any) -> bool:
    def _fingerprint(value: Any) -> List[tuple]:
        return [
            (
                str(item.get("key") or ""),
                str(item.get("status") or ""),
                str(item.get("text") or ""),
                tuple(item.get("artifacts") or []),
            )
            for item in (value or [])
            if isinstance(item, dict)
        ]

    return _fingerprint(left) == _fingerprint(right)


def upsert_task_card(
    db: Session,
    *,
    message_id: str,
    user_id: int,
    status: str,
    title: str = "",
    text: str = "",
    artifacts: Any = None,
    items: Any = None,
    source: str = "",
    cancellable: Optional[bool] = None,
    cancel_reason: str = "",
    force: bool = False,
) -> bool:
    """写一张新的进度卡事件；状态/文案/产物/子任务都没变时跳过。**不提交事务**。"""
    if not message_id:
        return False
    status_key = str(status or "").strip().lower() or "running"
    normalized_items = normalize_items(items)
    urls = normalize_artifacts(artifacts)
    if not urls:
        # 顶层没给产物时，用子任务里的产物（前端据此直接贴预览）
        urls = normalize_artifacts([url for item in normalized_items for url in (item.get("artifacts") or [])])
    if cancellable is None:
        cancellable = status_key in _ACTIVE_STATUSES or any(
            str(item.get("status") or "") in _ACTIVE_STATUSES for item in normalized_items
        )
    payload = {
        "card_id": f"task:{message_id}",
        "status": status_key,
        "status_label": status_label(status_key),
        "title": str(title or "").strip()[:80],
        "text": str(text or "").strip()[:800],
        "artifacts": urls,
        # 前端 collectMediaUrls 会读这个字段 → 图片/视频直接贴预览
        "media_urls": urls,
        "items": normalized_items,
        "cancellable": bool(cancellable),
        "cancel_reason": str(cancel_reason or "")[:200],
        "source": str(source or "").strip()[:32],
        "updated_at": datetime.utcnow().isoformat(),
    }
    previous = latest_task_card(db, message_id)
    if previous and not force:
        same = (
            str(previous.get("status") or "") == payload["status"]
            and str(previous.get("text") or "") == payload["text"]
            and list(previous.get("artifacts") or []) == urls
            and str(previous.get("title") or "") == payload["title"]
            and bool(previous.get("cancellable")) == payload["cancellable"]
            and _same_items(previous.get("items"), normalized_items)
        )
        if same:
            return False
    db.add(H5ChatEvent(message_id=message_id, user_id=user_id, event_type=CARD_EVENT, payload=payload))
    return True
