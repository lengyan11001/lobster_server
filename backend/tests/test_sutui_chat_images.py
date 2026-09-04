from __future__ import annotations

import base64

import httpx
import pytest


@pytest.mark.asyncio
async def test_remote_image_is_converted_to_data_uri(monkeypatch):
    from backend.app.api import sutui_chat_proxy

    source = b"\x89PNG\r\n\x1a\nsmall-image"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, headers={"content-type": "image/png"}, content=source)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe"},
                        {"type": "image_url", "image_url": {"url": "https://assets.test/image"}},
                    ],
                }
            ]
        }
        monkeypatch.setattr(sutui_chat_proxy, "_CHAT_IMAGE_MAX_BYTES", 1024 * 1024)
        monkeypatch.setattr(sutui_chat_proxy, "_CHAT_IMAGE_MAX_TOTAL_BYTES", 1024 * 1024)

        data_uri, size, mime = await sutui_chat_proxy._download_chat_image_as_data_uri(client, "https://assets.test/image")
        assert data_uri == "data:image/png;base64," + base64.b64encode(source).decode("ascii")
        assert size == len(source)
        assert mime == "image/png"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_data_uri_is_not_downloaded(monkeypatch):
    from backend.app.api import sutui_chat_proxy

    body = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}],
            }
        ]
    }

    async def fail_download(*args, **kwargs):
        raise AssertionError("data URI must not be downloaded")

    monkeypatch.setattr(sutui_chat_proxy, "_download_chat_image_as_data_uri", fail_download)
    result = await sutui_chat_proxy._prepare_multimodal_images(body, trace_id="test")
    assert result == {"images": 0, "bytes": 0}


@pytest.mark.asyncio
async def test_prepare_multimodal_images_replaces_remote_reference(monkeypatch):
    from backend.app.api import sutui_chat_proxy

    body = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": "https://assets.test/image"}}],
            }
        ]
    }

    async def fake_download(_client, _url):
        return "data:image/jpeg;base64,QUJD", 3, "image/jpeg"

    monkeypatch.setattr(sutui_chat_proxy, "_download_chat_image_as_data_uri", fake_download)
    result = await sutui_chat_proxy._prepare_multimodal_images(body, trace_id="test")
    assert result == {"images": 1, "bytes": 3}
    assert body["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_chat_audit_redacts_image_data_uri():
    from backend.app.services.sutui_api_audit import clip_openai_chat_completions_json_for_audit

    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 400}}
                ],
            }
        ]
    }
    out = clip_openai_chat_completions_json_for_audit(body)
    assert "data:image/png;base64" not in out
    assert "image data omitted" in out
