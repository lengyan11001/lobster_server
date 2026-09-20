"""确认后转入后台任务的进度卡（2026-09-20）。

用户在对话里点「确认执行」后，任务其实转到了后台（worker / mastra / 设备），但会话里只剩一句
"已确认"，页面上看不到进度；离开页面再回来也不知道做到哪了。这里把这张卡作为一条
`task_card` 事件挂在**原始请求消息**上：

  - 确认时立刻写一张（排队中 → 执行中）
  - 看护循环 mastra_task_watch 按真实状态更新（执行中 / 已完成 / 失败，带产物链接）
  - 事件是持久化的：离开页面、换设备打开都能看到最新状态；UI 取该消息最后一条 task_card 渲染

对外只需要两个函数：latest_task_card / upsert_task_card（不提交事务，由调用方 commit）。
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

_MAX_ARTIFACTS = 6


def status_label(status: str) -> str:
    return STATUS_LABELS.get(str(status or "").strip().lower(), "进行中")


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


def _normalize_artifacts(artifacts: Any) -> List[str]:
    out: List[str] = []
    for item in artifacts or []:
        url = str(item or "").strip()
        if url and url not in out:
            out.append(url)
        if len(out) >= _MAX_ARTIFACTS:
            break
    return out


def upsert_task_card(
    db: Session,
    *,
    message_id: str,
    user_id: int,
    status: str,
    title: str = "",
    text: str = "",
    artifacts: Any = None,
    source: str = "",
    force: bool = False,
) -> bool:
    """写一张新的进度卡事件；状态/文案/产物都没变时跳过（避免每轮轮询都堆事件）。

    返回是否真的写入了事件。**不提交事务**，由调用方 commit。
    """
    if not message_id:
        return False
    status_key = str(status or "").strip().lower() or "running"
    urls = _normalize_artifacts(artifacts)
    payload = {
        "card_id": f"task:{message_id}",
        "status": status_key,
        "status_label": status_label(status_key),
        "title": str(title or "").strip()[:80],
        "text": str(text or "").strip()[:600],
        "artifacts": urls,
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
        )
        if same:
            return False
    db.add(H5ChatEvent(message_id=message_id, user_id=user_id, event_type=CARD_EVENT, payload=payload))
    return True