from __future__ import annotations

import asyncio


def test_background_loop_is_restarted_after_failure() -> None:
    from backend.background_worker import _supervise_loop

    async def scenario() -> None:
        calls = 0
        stable_started = asyncio.Event()
        stay_running = asyncio.Event()

        async def factory() -> None:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RuntimeError("temporary failure")
            stable_started.set()
            await stay_running.wait()

        task = asyncio.create_task(
            _supervise_loop(
                "test-loop",
                factory,
                initial_backoff_seconds=0.01,
                max_backoff_seconds=0.02,
            )
        )
        await asyncio.wait_for(stable_started.wait(), timeout=1)
        assert calls == 3
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
