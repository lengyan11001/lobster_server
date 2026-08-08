from __future__ import annotations

import asyncio

from backend.app.api import comfly_proxy as module


class _FakeDb:
    def __init__(self) -> None:
        self.added = []
        self.flush_count = 0

    def add(self, value) -> None:
        self.added.append(value)

    def flush(self) -> None:
        self.flush_count += 1


def test_generated_image_asset_uses_bounded_upload_io(monkeypatch) -> None:
    db = _FakeDb()
    upload_calls = []

    async def fake_download(_url):
        return b"image-bytes", "image/png", ".png"

    async def fake_upload(func, *args):
        upload_calls.append((func, args))
        return "asset-1", "assets/asset-1.png", 11, "https://cdn.example/asset-1.png"

    monkeypatch.setattr(module, "_download_image_bytes", fake_download)
    monkeypatch.setattr(module, "_run_asset_upload_io", fake_upload)

    result = asyncio.run(
        module._persist_generated_image_asset(
            db,
            user_id=7,
            url="https://upstream.example/image.png",
            prompt="test prompt",
            model="gpt-image-2-openmindapi",
        )
    )

    assert result["asset_id"] == "asset-1"
    assert len(upload_calls) == 1
    assert upload_calls[0][0] is module._save_bytes_or_tos
    assert upload_calls[0][1] == (b"image-bytes", ".png", "image/png")
    assert len(db.added) == 1
    assert db.flush_count == 1


def test_generated_image_persistence_is_queued_without_waiting(monkeypatch) -> None:
    queued = []
    persisted = []

    async def fake_persist(user_id, **kwargs):
        persisted.append((user_id, kwargs))

    def fake_spawn(coro, *, name):
        queued.append((coro, name))

    monkeypatch.setattr(module, "_persist_generated_images_in_background", fake_persist)
    monkeypatch.setattr(module, "spawn_tracked_task", fake_spawn)
    monkeypatch.setattr(module, "_extract_image_result_urls", lambda _payload: ["https://upstream.example/image.png"])

    assert module._queue_generated_image_asset_persistence(
        7,
        response_payload={"large": "upstream-response"},
        prompt="test prompt",
        model="gpt-image-2-openmindapi",
        limit=1,
    ) is True
    assert len(queued) == 1
    assert queued[0][1] == "image-asset-persist-7"

    asyncio.run(queued[0][0])
    assert persisted == [
        (
            7,
            {
                "response_payload": {"data": [{"url": "https://upstream.example/image.png"}]},
                "prompt": "test prompt",
                "model": "gpt-image-2-openmindapi",
                "limit": 1,
                "exclude_urls": None,
            },
        )
    ]
