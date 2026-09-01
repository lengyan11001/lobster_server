"""Single-instance background loops for the production server.

Run this as a separate process from the web API workers.  The FastAPI app can
then use multiple uvicorn workers without duplicating periodic probes,
reconciliation, or scheduled publishing loops.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path
from typing import Awaitable, Callable, List

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

from dotenv import load_dotenv

load_dotenv(_root / ".env", override=False)

_log_level_name = os.environ.get("LOG_LEVEL", "debug").strip().lower()
_log_level = getattr(logging, _log_level_name.upper(), logging.DEBUG)
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
try:
    from backend.app.core.log_retention import configure_daily_file_logging

    configure_daily_file_logging(_root, "background", _log_level)
except Exception:
    pass

from backend.app.core.config import settings
from backend.app.services.ip_content_schedule_runner import ip_content_schedule_background_loop
from backend.app.services.h5_chat_retention import h5_chat_retention_background_loop
from backend.app.services.mastra_chat_runner import mastra_chat_background_loop
from backend.app.services.meta_social_schedule_runner import meta_social_schedule_background_loop
from backend.app.services.provider_balance_monitor import (
    is_provider_balance_monitor_enabled,
    provider_balance_monitor_loop_forever,
)
from backend.app.services.runtime_monitor import is_runtime_monitor_enabled, runtime_monitor_loop_forever
from backend.app.services.runtime_state_maintenance import (
    fail_client_runs_on_startup_sync,
    runtime_state_maintenance_loop,
)
from backend.app.services.sutui_llm_probe import (
    is_sutui_llm_probe_enabled_for_this_instance,
    sutui_llm_probe_loop_forever,
)
from backend.app.services.sutui_reconcile import is_sutui_reconcile_enabled, sutui_reconcile_loop_forever
from backend.app.services.douyin_platform_information_desk import (
    douyin_platform_information_desk_background_loop,
)

logger = logging.getLogger("backend.background_worker")


async def _supervise_loop(
    name: str,
    factory: Callable[[], Awaitable[None]],
    *,
    initial_backoff_seconds: float = 1.0,
    max_backoff_seconds: float = 60.0,
) -> None:
    """Restart a failed periodic loop instead of leaving a healthy-looking dead worker."""
    backoff = max(0.01, float(initial_backoff_seconds))
    max_backoff = max(backoff, float(max_backoff_seconds))
    while True:
        try:
            await factory()
            logger.error("[background] loop exited unexpectedly name=%s", name)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[background] loop crashed name=%s restart_in=%.1fs", name, backoff)
        await asyncio.sleep(backoff)
        backoff = min(max_backoff, backoff * 2.0)


def _enabled_from_env(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _task_factories() -> List[tuple[str, Callable[[], Awaitable[None]]]]:
    factories: List[tuple[str, Callable[[], Awaitable[None]]]] = []
    if _enabled_from_env("LOBSTER_BACKGROUND_SUTUI_LLM_PROBE_ENABLED", True) and is_sutui_llm_probe_enabled_for_this_instance():
        factories.append(("sutui_llm_probe", lambda: sutui_llm_probe_loop_forever(3600.0)))
    else:
        logger.info("[background] 速推 LLM 定时探测未启用")

    if _enabled_from_env("LOBSTER_BACKGROUND_SUTUI_RECONCILE_ENABLED", True) and is_sutui_reconcile_enabled():
        factories.append(("sutui_reconcile", lambda: sutui_reconcile_loop_forever()))
    else:
        logger.info("[background] 速推对账未启用")

    if _enabled_from_env("LOBSTER_BACKGROUND_META_SOCIAL_ENABLED", True) and settings.meta_app_id and settings.meta_app_secret:
        factories.append(("meta_social_schedule", meta_social_schedule_background_loop))
    else:
        logger.info("[background] Meta Social 定时发布未启用")

    if _enabled_from_env("LOBSTER_BACKGROUND_IP_CONTENT_SCHEDULE_ENABLED", True):
        factories.append(("ip_content_schedule", ip_content_schedule_background_loop))
    else:
        logger.info("[background] IP日更定时任务未启用")

    if _enabled_from_env("LOBSTER_MASTRA_CHAT_ENABLED", True):
        factories.append(("mastra_chat", mastra_chat_background_loop))
        factories.append(("h5_chat_retention", h5_chat_retention_background_loop))
    else:
        logger.info("[background] AI 调度会话未启用")

    if _enabled_from_env("LOBSTER_BACKGROUND_PROVIDER_BALANCE_MONITOR_ENABLED", True) and is_provider_balance_monitor_enabled():
        factories.append(("provider_balance_monitor", provider_balance_monitor_loop_forever))
    else:
        logger.info("[background] provider balance monitor disabled")

    if _enabled_from_env("LOBSTER_BACKGROUND_RUNTIME_MONITOR_ENABLED", True) and is_runtime_monitor_enabled():
        factories.append(("runtime_monitor", runtime_monitor_loop_forever))
    else:
        logger.info("[background] runtime monitor disabled")
    if _enabled_from_env("LOBSTER_BACKGROUND_RUNTIME_STATE_MAINTENANCE_ENABLED", True):
        factories.append(("runtime_state_maintenance", runtime_state_maintenance_loop))
    else:
        logger.info("[background] runtime state maintenance disabled")
    if _enabled_from_env("LOBSTER_BACKGROUND_DOUYIN_INFORMATION_DESK_ENABLED", True):
        factories.append(("douyin_platform_information_desk", douyin_platform_information_desk_background_loop))
    else:
        logger.info("[background] douyin platform information desk disabled")
    return factories


async def main_async() -> int:
    try:
        reconciled = await asyncio.to_thread(fail_client_runs_on_startup_sync)
        logger.warning("[background] startup client runs reconciled=%s", reconciled)
    except Exception:
        logger.exception("[background] failed to reconcile client runs on startup")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop_event.set())

    tasks: List[asyncio.Task] = []
    for name, factory in _task_factories():
        task = asyncio.create_task(_supervise_loop(name, factory), name=name)
        tasks.append(task)
        logger.info("[background] 已启动任务: %s", name)

    if not tasks:
        logger.warning("[background] 没有可运行的后台任务，进程保持存活等待退出信号")

    await stop_event.wait()
    logger.info("[background] 收到退出信号，取消 %d 个任务", len(tasks))
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task
    return 0


def main() -> int:
    logger.info("[background] 启动 LOG_LEVEL=%s", _log_level_name)
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
