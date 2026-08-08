from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from backend.app.services.workload_guard import (
    BoundedWorkGate,
    WorkloadQueueFull,
    heavy_workload_kind,
    install_workload_guard,
    request_workload_kind,
)


def test_bounded_work_gate_counts_active_and_waiting_without_overlap() -> None:
    async def scenario() -> None:
        gate = BoundedWorkGate(concurrency=1, queue_limit=1, wait_timeout_seconds=2)
        holder_entered = asyncio.Event()
        release_holder = asyncio.Event()
        waiter_entered = asyncio.Event()

        async def hold_slot() -> None:
            async with gate.slot():
                holder_entered.set()
                await release_holder.wait()

        async def wait_for_slot() -> None:
            async with gate.slot():
                waiter_entered.set()

        holder = asyncio.create_task(hold_slot())
        await asyncio.wait_for(holder_entered.wait(), timeout=1)
        assert gate.active == 1
        assert gate.waiting == 0

        waiter = asyncio.create_task(wait_for_slot())
        for _ in range(100):
            if gate.waiting == 1:
                break
            await asyncio.sleep(0)
        assert gate.active == 1
        assert gate.waiting == 1

        with pytest.raises(WorkloadQueueFull, match="queue is full"):
            async with gate.slot():
                pytest.fail("queue overflow must not enter the protected slot")

        release_holder.set()
        await asyncio.wait_for(holder, timeout=1)
        await asyncio.wait_for(waiter_entered.wait(), timeout=1)
        await asyncio.wait_for(waiter, timeout=1)
        assert gate.active == 0
        assert gate.waiting == 0

    asyncio.run(scenario())


def test_bounded_work_gate_removes_timed_out_waiter() -> None:
    async def scenario() -> None:
        gate = BoundedWorkGate(concurrency=1, queue_limit=1, wait_timeout_seconds=1)
        release_holder = asyncio.Event()
        holder_entered = asyncio.Event()

        async def hold_slot() -> None:
            async with gate.slot():
                holder_entered.set()
                await release_holder.wait()

        holder = asyncio.create_task(hold_slot())
        await asyncio.wait_for(holder_entered.wait(), timeout=1)
        with pytest.raises(WorkloadQueueFull, match="wait timed out"):
            async with gate.slot():
                pytest.fail("timed out waiter must not enter the protected slot")
        assert gate.active == 1
        assert gate.waiting == 0
        release_holder.set()
        await asyncio.wait_for(holder, timeout=1)

    asyncio.run(scenario())


def test_bounded_work_gate_caps_total_admitted_work_during_concurrent_entry() -> None:
    async def scenario() -> None:
        gate = BoundedWorkGate(concurrency=2, queue_limit=1, wait_timeout_seconds=2)
        release = asyncio.Event()
        entered = asyncio.Event()
        entered_count = 0

        async def hold_slot() -> None:
            nonlocal entered_count
            async with gate.slot():
                entered_count += 1
                if entered_count == 2:
                    entered.set()
                await release.wait()

        holders = [asyncio.create_task(hold_slot()) for _ in range(2)]
        await asyncio.wait_for(entered.wait(), timeout=1)
        waiter = asyncio.create_task(hold_slot())
        for _ in range(100):
            if gate.waiting == 1:
                break
            await asyncio.sleep(0)

        with pytest.raises(WorkloadQueueFull, match="queue is full"):
            async with gate.slot():
                pytest.fail("the total admitted workload must stay bounded")

        release.set()
        await asyncio.gather(*holders, waiter)
        assert gate.active == 0
        assert gate.waiting == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/assets/upload"),
        ("POST", "/api/comfly-proxy/v1/files"),
        ("POST", "/api/comfly-proxy/v1/chat/completions"),
        ("POST", "/api/comfly-proxy/v1/images/generations"),
        ("POST", "/api/comfly-proxy/v1/images/edits"),
        ("POST", "/api/comfly-proxy/v2/videos/generations"),
        ("POST", "/api/comfly-proxy/openmind/v1/videos"),
        ("GET", "/api/comfly-proxy/openmind/v1/videos/task-123"),
        ("POST", "/api/personal-settings/memory-documents/generate"),
        ("POST", "/api/personal-settings/memory-documents/complete-online-generation-upload"),
        ("POST", "/api/cutcli/templates/demo/render"),
        ("POST", "/api/cutcli/jobs/demo/stt/start"),
        ("POST", "/api/h5/recorder/memory-files/demo/transcribe"),
        ("POST", "/api/h5-chat/uploads"),
        ("POST", "/api/hifly/my/avatar/task"),
        ("POST", "/api/juhe-wechat/media/upload-file"),
        ("POST", "/api/wecom/proxy/media/upload"),
        ("POST", "/admin/api/openclaw-memory/upload"),
        ("POST", "/api/linkedin-mining/jobs/demo/resume"),
        ("POST", "/api/wechat-channels-transcript/jobs/demo/resume"),
    ],
)
def test_heavy_workload_routes_are_classified(method: str, path: str) -> None:
    assert heavy_workload_kind(method, path) == "heavy"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/assets/upload"),
        ("GET", "/api/h5-chat/messages"),
        ("POST", "/api/auth/login"),
        ("POST", "/api/h5-chat/devices/heartbeat"),
    ],
)
def test_interactive_routes_bypass_heavy_workload_queue(method: str, path: str) -> None:
    assert heavy_workload_kind(method, path) == ""


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/auth/login", "dynamic"),
        ("GET", "/api/h5-chat/messages", "dynamic"),
        ("POST", "/chat/stream", "dynamic"),
        ("GET", "/api/health", ""),
        ("GET", "/h5-static/h5-app.js", ""),
        ("OPTIONS", "/api/assets/upload", ""),
    ],
)
def test_dynamic_request_admission_classification(method: str, path: str, expected: str) -> None:
    assert request_workload_kind(method, path) == expected


def test_request_admission_fails_fast_without_blocking_health(monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SERVER_REQUEST_MAX_CONCURRENCY", "1")
        monkeypatch.setenv("SERVER_REQUEST_MAX_QUEUE", "0")
        app = FastAPI()
        install_workload_guard(app)
        entered = asyncio.Event()
        release = asyncio.Event()

        @app.get("/api/hold")
        async def hold_request():
            entered.set()
            await release.wait()
            return {"ok": True}

        @app.get("/api/health")
        async def health_request():
            return {"status": "ok"}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            holder = asyncio.create_task(client.get("/api/hold"))
            await asyncio.wait_for(entered.wait(), timeout=1)
            overloaded = await client.get("/api/hold")
            health = await client.get("/api/health")
            assert overloaded.status_code == 503
            assert overloaded.headers["retry-after"] == "3"
            assert health.status_code == 200
            release.set()
            completed = await asyncio.wait_for(holder, timeout=1)
            assert completed.status_code == 200
            assert completed.headers["x-request-queue-ms"] == "0"

    asyncio.run(scenario())
