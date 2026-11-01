"""执行位置路由：设备在线就走设备，设备不在线才允许交给服务端。

2026-11-01 用户口径：
1) 有设备在线时，不许吃服务器资源（一律走客户端执行）；
2) 只有「允许服务端执行」的能力（白名单），且该用户当前没有在线设备时，才落到服务端；
3) 随时能切回：把 LOBSTER_SERVER_EXEC_ACTIONS 置空（或不包含该 action）即 100% 回到客户端，
   不需要改代码；文件名/开关只影响“新创建的任务”，已在跑的不受影响。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

SERVER_EXEC_ENV = "LOBSTER_SERVER_EXEC_ACTIONS"
ONLINE_WINDOW_SECONDS = 300

EXECUTOR_CLIENT = "client"
EXECUTOR_SERVER = "server"


def server_exec_actions() -> set[str]:
    """允许服务端执行的能力白名单（逗号分隔的 action 名，默认空=全部走客户端）。"""
    raw = os.environ.get(SERVER_EXEC_ENV, "") or ""
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def is_server_exec_enabled(action: str) -> bool:
    return str(action or "").strip().lower() in server_exec_actions()


def device_online(db: Any, user_id: int, within_seconds: int = ONLINE_WINDOW_SECONDS) -> bool:
    """用户是否有设备在最近 N 秒内上报过心跳。

    查不到（表/字段异常）一律当作“在线”：宁可让设备干活，也不抢服务器资源。
    """
    try:
        from ..models import H5ChatDevicePresence

        cutoff = datetime.utcnow() - timedelta(seconds=within_seconds)
        row = (
            db.query(H5ChatDevicePresence)
            .filter(
                H5ChatDevicePresence.user_id == int(user_id),
                H5ChatDevicePresence.last_seen_at >= cutoff,
            )
            .first()
        )
        return row is not None
    except Exception as exc:  # noqa: BLE001
        logger.info("[exec-routing] device presence check failed user=%s err=%s", user_id, exc)
        return True


def choose_executor(
    db: Any,
    *,
    user_id: int,
    action: str,
    task_kind: str = "",
) -> str:
    """决定这次任务由谁执行：client（默认）或 server。"""
    kind = str(task_kind or "").strip().lower()
    if kind != "client_workflow":
        return EXECUTOR_CLIENT
    if not is_server_exec_enabled(action):
        return EXECUTOR_CLIENT
    if device_online(db, user_id):
        return EXECUTOR_CLIENT
    return EXECUTOR_SERVER


def executor_for_payload(db: Any, *, user_id: int, task_kind: str, payload: Optional[dict]) -> str:
    data = payload if isinstance(payload, dict) else {}
    action = str(data.get("action") or "").strip().lower()
    return choose_executor(db, user_id=user_id, action=action, task_kind=task_kind)
