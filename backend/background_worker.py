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
from backend.app.services.meta_social_schedule_runner import meta_social_schedule_background_loop
from backend.app.services.provider_balance_monitor import (
    is_provider_balance_monitor_enabled,
    provider_balance_monitor_loop_forever,
)
from backend.app.services.sutui_llm_probe import (
    is_sutui_llm_probe_enabled_for_this_instance,
    sutui_llm_probe_loop_forever,
)
from backend.app.services.sutui_reconcile import is_sutui_reconcile_enabled, sutui_reconcile_loop_forever

logger = logging.getLogger("backend.background_worker")


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

    if _enabled_from_env("LOBSTER_BACKGROUND_PROVIDER_BALANCE_MONITOR_ENABLED", True) and is_provider_balance_monitor_enabled():
        factories.append(("provider_balance_monitor", provider_balance_monitor_loop_forever))
    else:
        logger.info("[background] provider balance monitor disabled")
    return factories


async def main_async() -> int:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop_event.set())

    tasks: List[asyncio.Task] = []
    for name, factory in _task_factories():
        task = asyncio.create_task(factory(), name=name)
        tasks.append(task)
        logger.info("[background] 已启动任务: %s", name)

    if not tasks:
        logger.warning("[background] 没有可运行的后台任务，进程保持存活等待退出信号")

    await stop_event.wait()
    logger.info("[background] 收到退出信号，取消 %d 个任务", len(tasks))
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
    return 0


def main() -> int:
    logger.info("[background] 启动 LOG_LEVEL=%s", _log_level_name)
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
