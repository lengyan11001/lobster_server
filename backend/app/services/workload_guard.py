from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Coroutine, TypeVar

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


logger = logging.getLogger(__name__)
_T = TypeVar("_T")


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


@dataclass(frozen=True)
class WorkloadLease:
    waited_ms: int


class WorkloadQueueFull(RuntimeError):
    pass


class BoundedWorkGate:
    def __init__(self, *, concurrency: int, queue_limit: int, wait_timeout_seconds: int) -> None:
        self.concurrency = max(1, int(concurrency))
        self.queue_limit = max(0, int(queue_limit))
        self.wait_timeout_seconds = max(1, int(wait_timeout_seconds))
        self._semaphore = asyncio.Semaphore(self.concurrency)
        self._state_lock = asyncio.Lock()
        self._active = 0
        self._waiting = 0

    @property
    def active(self) -> int:
        return self._active

    @property
    def waiting(self) -> int:
        return self._waiting

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[WorkloadLease]:
        started = time.monotonic()
        async with self._state_lock:
            if self._active + self._waiting >= self.concurrency + self.queue_limit:
                raise WorkloadQueueFull("heavy workload queue is full")
            self._waiting += 1
        acquired = False
        queued = True
        try:
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=self.wait_timeout_seconds)
                acquired = True
            except asyncio.TimeoutError as exc:
                raise WorkloadQueueFull("heavy workload queue wait timed out") from exc
            async with self._state_lock:
                self._waiting = max(0, self._waiting - 1)
                queued = False
                self._active += 1
            yield WorkloadLease(waited_ms=max(0, int((time.monotonic() - started) * 1000)))
        finally:
            async with self._state_lock:
                if queued:
                    self._waiting = max(0, self._waiting - 1)
                if acquired:
                    self._active = max(0, self._active - 1)
            if acquired:
                self._semaphore.release()


def work_gate_from_env(
    prefix: str,
    *,
    concurrency: int,
    queue_limit: int,
    wait_timeout_seconds: int,
) -> BoundedWorkGate:
    clean_prefix = str(prefix or "SERVER_WORK").strip().upper()
    return BoundedWorkGate(
        concurrency=_env_int(f"{clean_prefix}_MAX_CONCURRENCY", concurrency, minimum=1, maximum=64),
        queue_limit=_env_int(f"{clean_prefix}_MAX_QUEUE", queue_limit, minimum=0, maximum=1000),
        wait_timeout_seconds=_env_int(
            f"{clean_prefix}_QUEUE_TIMEOUT_SECONDS",
            wait_timeout_seconds,
            minimum=5,
            maximum=86400,
        ),
    )


_BACKGROUND_HEAVY_GATE = work_gate_from_env(
    "SERVER_BACKGROUND_HEAVY",
    concurrency=6,
    queue_limit=24,
    wait_timeout_seconds=1800,
)
_TRACKED_TASKS: set[asyncio.Task] = set()


@asynccontextmanager
async def background_heavy_slot(kind: str) -> AsyncIterator[WorkloadLease]:
    try:
        async with _BACKGROUND_HEAVY_GATE.slot() as lease:
            if lease.waited_ms:
                logger.info("background workload admitted kind=%s waited_ms=%s", kind, lease.waited_ms)
            yield lease
    except WorkloadQueueFull:
        logger.warning(
            "background workload overloaded kind=%s active=%s waiting=%s",
            kind,
            _BACKGROUND_HEAVY_GATE.active,
            _BACKGROUND_HEAVY_GATE.waiting,
        )
        raise


def spawn_tracked_task(coro: Coroutine[object, object, _T], *, name: str) -> asyncio.Task[_T]:
    task = asyncio.create_task(coro)
    if task is None:
        return task
    set_name = getattr(task, "set_name", None)
    if callable(set_name):
        set_name(name)
    _TRACKED_TASKS.add(task)

    def _finished(done: asyncio.Task) -> None:
        _TRACKED_TASKS.discard(done)
        if done.cancelled():
            return
        try:
            error = done.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.error(
                "tracked background task failed name=%s",
                done.get_name(),
                exc_info=(type(error), error, error.__traceback__),
            )

    task.add_done_callback(_finished)
    return task


_HEAVY_EXACT_PATHS = {
    "/admin/api/openclaw-memory/agent-upload",
    "/admin/api/openclaw-memory/upload",
    "/api/assets/upload",
    "/api/assets/save-url",
    "/api/assets/upload-temp",
    "/api/comfly-proxy/v1/files",
    "/api/comfly-proxy/v1/images/edits",
    "/api/diagnostics/upload",
    "/api/global-leads/jobs",
    "/api/h5-chat/uploads",
    "/api/h5-chat/upload-image",
    "/api/hifly/my/avatar/task",
    "/api/hifly/my/voice/task",
    "/api/mastra-chat/memory/import-asset",
    "/api/personal-settings/memory-documents/generate",
    "/api/personal-settings/memory-documents/save-upload",
    "/api/personal-settings/memory-documents/complete-online-generation-upload",
    "/api/shanjian-digital-human/profile/train",
    "/api/shanjian-digital-human/video/create",
    "/api/shanjian-digital-human/video/task",
    "/api/shanjian-smart-clip/submit",
    "/api/wechat-channels-transcript/jobs",
    "/api/wechat-channels-transcript/videos",
    "/api/wan/role-transfer/upload",
    "/api/wan/role-transfer/tasks",
    "/api/hifly/my/video/task",
    "/api/meta-social/sync",
    "/api/openclaw/restart",
    "/api/lead-collection/template-runs",
    "/api/juhe-wechat/media/upload-file",
    "/api/juhe-wechat/media/upload-url",
    "/api/wecom/media/upload",
    "/api/wecom/proxy/media/upload",
}


def heavy_workload_kind(method: str, path: str) -> str:
    method = str(method or "").upper()
    path = str(path or "").rstrip("/") or "/"
    if method not in {"POST", "PUT", "PATCH"}:
        return ""
    if path in _HEAVY_EXACT_PATHS:
        return "heavy"
    if path == "/api/h5/recorder/files" and method == "POST":
        return "heavy"
    if path.startswith("/api/h5/recorder/memory-files/") and path.endswith("/transcribe"):
        return "heavy"
    if path.startswith("/api/h5/recorder/files/") and path.endswith("/retry"):
        return "heavy"
    if path.startswith("/api/ip-content/") and (
        "/generate/" in path
        or path.endswith("/sync")
        or path.endswith("/query")
        or path == "/api/ip-content/drafts"
    ):
        return "heavy"
    if path.startswith("/api/hifly/my/") and any(
        marker in path
        for marker in ("/create-", "/preview-tts", "/share")
    ):
        return "heavy"
    if path.startswith("/api/global-leads/jobs/") and path.endswith(("/run-next", "/resume")):
        return "heavy"
    if path.startswith("/api/social-leads/jobs/") and path.endswith(("/run-next", "/resume")):
        return "heavy"
    if path.startswith("/api/linkedin-mining/jobs/") and path.endswith(("/run-next", "/resume")):
        return "heavy"
    if path.startswith("/api/wechat-channels-transcript/jobs/") and path.endswith("/resume"):
        return "heavy"
    if path.startswith("/api/cutcli/") and ("render" in path or "/stt/" in path):
        return "heavy"
    return ""


def install_workload_guard(app: FastAPI) -> BoundedWorkGate:
    gate = BoundedWorkGate(
        concurrency=_env_int("SERVER_HEAVY_MAX_CONCURRENCY", 6, minimum=1, maximum=32),
        queue_limit=_env_int("SERVER_HEAVY_MAX_QUEUE", 24, minimum=0, maximum=500),
        wait_timeout_seconds=_env_int("SERVER_HEAVY_QUEUE_TIMEOUT_SECONDS", 120, minimum=5, maximum=900),
    )
    app.state.heavy_work_gate = gate

    @app.middleware("http")
    async def bounded_heavy_workload(request: Request, call_next):
        kind = heavy_workload_kind(request.method, request.url.path)
        if not kind:
            return await call_next(request)
        try:
            async with gate.slot() as lease:
                response = await call_next(request)
                response.headers["X-Workload-Queue-Ms"] = str(lease.waited_ms)
                return response
        except WorkloadQueueFull:
            logger.warning(
                "heavy workload overloaded method=%s path=%s active=%s waiting=%s",
                request.method,
                request.url.path,
                gate.active,
                gate.waiting,
            )
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "15"},
                content={"detail": "当前处理任务较多，已达到排队上限，请稍后重试"},
            )

    return gate
