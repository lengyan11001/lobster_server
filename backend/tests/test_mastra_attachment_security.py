from __future__ import annotations

import asyncio

import httpx
import pytest

from backend.app.services import mastra_attachment_security as security


@pytest.mark.parametrize(
    ("values", "blocked"),
    [
        ({"filename": "photo.icns"}, True),
        ({"url": "https://example.test/photo.JXL?token=1"}, True),
        ({"content_type": "image/heic; charset=binary"}, True),
        ({"header": b"icns" + b"\x00" * 32}, True),
        ({"header": b"\xff\x0a" + b"\x00" * 32}, True),
        ({"header": b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32}, True),
        ({"header": b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1"}, True),
        ({"header": b"\xff\xd8\xff\xe0" + b"\x00" * 32, "filename": "photo.jpg"}, False),
    ],
)
def test_blocked_image_format_detects_metadata_and_magic(values, blocked):
    assert bool(security.blocked_image_format(**values)) is blocked


def test_remote_mastra_image_probe_rejects_disguised_heif(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == "bytes=0-255"
        return httpx.Response(
            206,
            headers={"Content-Type": "image/jpeg"},
            content=b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1",
        )

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        security.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=transport, **kwargs),
    )

    with pytest.raises(security.UnsafeMastraImageError, match="HEIF/HEIC"):
        asyncio.run(
            security.assert_safe_remote_mastra_images(
                [
                    {
                        "url": "https://cdn.example.test/disguised.jpg",
                        "name": "disguised.jpg",
                        "media_type": "image",
                        "content_type": "image/jpeg",
                    }
                ]
            )
        )
