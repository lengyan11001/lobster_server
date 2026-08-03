from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

from starlette.requests import Request

from backend.app.api.comfly_proxy import (
    _is_image_download_interrupted_payload,
    _is_trusted_internal_video_fallback,
    _maybe_resubmit_interrupted_video,
    _mirror_openmind_video_to_tos,
    _openmind_video_body,
    _remember_video_image_retry_context,
    _video_image_retry_contexts,
    _video_image_retry_poll_target,
    _video_image_retry_roots,
    _video_provider_policy,
    _xai_video_body,
)


def _request(headers: dict[str, str]) -> Request:
    raw_headers = [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw_headers})


def test_internal_video_fallback_requires_marker_and_matching_key(monkeypatch):
    from backend.app.api import comfly_proxy

    monkeypatch.setattr(
        comfly_proxy.settings,
        "lobster_mcp_billing_internal_key",
        "internal-secret",
        raising=False,
    )

    assert _is_trusted_internal_video_fallback(
        _request(
            {
                "X-Lobster-Mcp-Billing": "internal-secret",
                "X-Lobster-Video-Fallback": "1",
            }
        )
    )
    assert not _is_trusted_internal_video_fallback(
        _request({"X-Lobster-Mcp-Billing": "internal-secret"})
    )
    assert not _is_trusted_internal_video_fallback(
        _request(
            {
                "X-Lobster-Mcp-Billing": "wrong-secret",
                "X-Lobster-Video-Fallback": "1",
            }
        )
    )


def test_openmind_video_body_uses_integer_duration_and_all_references():
    body = _openmind_video_body(
        {
            "prompt": "product video",
            "duration": 8,
            "aspect_ratio": "4:5",
            "image_urls": ["https://example.com/a.png", "https://example.com/b.png"],
        },
        "grok-video-3",
        {},
    )

    assert body["duration"] == 8
    assert "seconds" not in body
    assert body["images"] == ["https://example.com/a.png", "https://example.com/b.png"]
    assert body["image_urls"] == body["images"]
    assert body["aspect_ratio"] == "4:5"
    assert body["size"] == "864x1080"


def test_xai_video_body_maps_duration_and_first_image():
    body = _xai_video_body(
        {
            "prompt": "product video",
            "seconds": "8",
            "aspect_ratio": "1:1",
            "resolution": "720P",
            "image_urls": ["https://example.com/a.png", "https://example.com/b.png"],
        },
        "grok-imagine-video-1.5",
    )

    assert body == {
        "model": "grok-imagine-video-1.5",
        "prompt": "product video",
        "duration": 8,
        "aspect_ratio": "1:1",
        "resolution": "720p",
        "image": {"url": "https://example.com/a.png"},
    }


def test_xai_video_is_first_provider_for_grok_family():
    policy = _video_provider_policy("xai/grok-imagine-video/image-to-video")

    assert policy["ok"] is True
    assert policy["model_family"] == "grok"
    assert policy["providers"][0] == {
        "channel": "xai",
        "model": "grok-imagine-video-1.5",
        "base_url": "/api/comfly-proxy",
    }


def test_xai_video_model_has_billable_pricing_entry():
    pricing_path = Path(__file__).resolve().parents[2] / "comfly_pricing.json"
    pricing = json.loads(pricing_path.read_text(encoding="utf-8"))
    entry = pricing["models"]["grok-imagine-video-1.5"]

    assert entry["price_type"] == "per_call"
    assert entry["price_per_unit"] == 80
    assert entry["api_format"] == "xai_official"


def test_interrupted_image_download_payload_detection():
    assert _is_image_download_interrupted_payload(
        {
            "status": "failed",
            "error": {
                "code": "invalid_argument",
                "message": (
                    "Failed to download the provided image "
                    "(image_download_error=image_download_interrupted): "
                    "the connection dropped while downloading the image."
                ),
            },
        }
    )
    assert not _is_image_download_interrupted_payload(
        {"status": "failed", "error": {"message": "content policy violation"}}
    )


def test_video_retry_context_can_reload_from_shared_cache():
    _video_image_retry_contexts.clear()
    _video_image_retry_roots.clear()
    _remember_video_image_retry_context(
        "shared-original",
        provider="xai",
        body={"model": "grok-imagine-video-1.5", "prompt": "test"},
        model="grok-imagine-video-1.5",
        request_user_id=54,
    )
    _video_image_retry_contexts.clear()
    _video_image_retry_roots.clear()

    root, active, context = _video_image_retry_poll_target(
        "shared-original", provider="xai", request_user_id=54
    )

    assert root == "shared-original"
    assert active == "shared-original"
    assert context["body"]["prompt"] == "test"


def test_xai_interrupted_image_download_resubmits_once_without_billing(monkeypatch):
    from backend.app.api import comfly_proxy

    _video_image_retry_contexts.clear()
    _video_image_retry_roots.clear()
    _remember_video_image_retry_context(
        "xai-original",
        provider="xai",
        body={"model": "grok-imagine-video-1.5", "prompt": "test"},
        model="grok-imagine-video-1.5",
        request_user_id=54,
    )
    submit = AsyncMock(return_value={"request_id": "xai-replacement"})
    monkeypatch.setattr(comfly_proxy, "_xai_video_submit", submit)
    monkeypatch.setattr(comfly_proxy, "_audit", lambda *_args, **_kwargs: None)
    failed = {
        "status": "failed",
        "error": {"message": "image_download_error=image_download_interrupted"},
    }

    first = asyncio.run(
        _maybe_resubmit_interrupted_video(
            "xai-original",
            provider="xai",
            payload=failed,
            request_user_id=54,
        )
    )
    second = asyncio.run(
        _maybe_resubmit_interrupted_video(
            "xai-original",
            provider="xai",
            payload=failed,
            request_user_id=54,
        )
    )

    assert first["status"] == "pending"
    assert first["task_id"] == "xai-original"
    assert first["_provider_task_id"] == "xai-replacement"
    assert second is None
    submit.assert_awaited_once()
    root, active, context = _video_image_retry_poll_target(
        "xai-original", provider="xai", request_user_id=54
    )
    assert root == "xai-original"
    assert active == "xai-replacement"
    assert context["resubmit_count"] == 1


def test_openmind_interrupted_image_download_resubmits_once(monkeypatch):
    from backend.app.api import comfly_proxy

    _video_image_retry_contexts.clear()
    _video_image_retry_roots.clear()
    _remember_video_image_retry_context(
        "openmind-original",
        provider="openmind",
        body={"model": "grok-video-3", "prompt": "test"},
        model="grok-video-3",
        request_user_id=54,
    )
    submit = AsyncMock(return_value={"task_id": "openmind-replacement"})
    monkeypatch.setattr(comfly_proxy, "_openmind_video_submit", submit)
    monkeypatch.setattr(comfly_proxy, "_require_model_entry", lambda _model: {})
    monkeypatch.setattr(comfly_proxy, "_audit", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        _maybe_resubmit_interrupted_video(
            "openmind-original",
            provider="openmind",
            payload={
                "status": "failed",
                "video_url": (
                    "Failed to download the provided image "
                    "(image_download_error=image_download_interrupted): "
                    "the connection dropped while downloading the image"
                ),
            },
            request_user_id=54,
        )
    )

    assert result["status"] == "pending"
    assert result["_provider_task_id"] == "openmind-replacement"
    submit.assert_awaited_once()


def test_openmind_video_uses_proxy_transfer_and_replaces_output_url(monkeypatch):
    from backend.app.api import comfly_proxy

    source_url = "https://vidgen.x.ai/example.mp4"
    tos_url = "https://assets.example.com/openmind-task-1.mp4"
    transfer = AsyncMock(return_value=(tos_url, 12345))
    local_save = AsyncMock(side_effect=AssertionError("main server must not download or save the video"))
    monkeypatch.setattr(comfly_proxy, "_transfer_video_to_tos_via_proxy", transfer)
    monkeypatch.setattr(comfly_proxy, "_save_bytes_or_tos", local_save)
    comfly_proxy._openmind_tos_url_cache.clear()

    result = asyncio.run(
        _mirror_openmind_video_to_tos(
            {
                "status": "completed",
                "video_url": source_url,
                "video": {"url": source_url},
            },
            "task-1",
        )
    )

    assert result["video_url"] == tos_url
    assert result["tos_url"] == tos_url
    assert result["source_video_url"] == source_url
    assert result["video"]["url"] == tos_url
    assert result["video"]["source_url"] == source_url
    assert "tos_transfer_error" not in result
    transfer.assert_awaited_once_with(source_url, task_id="task-1")
    local_save.assert_not_called()


def test_openmind_video_proxy_transfer_failure_is_reported(monkeypatch):
    from backend.app.api import comfly_proxy

    source_url = "https://vidgen.x.ai/example-failed.mp4"
    transfer = AsyncMock(side_effect=RuntimeError("proxy download timed out"))
    monkeypatch.setattr(comfly_proxy, "_transfer_video_to_tos_via_proxy", transfer)
    comfly_proxy._openmind_tos_url_cache.clear()

    result = asyncio.run(
        _mirror_openmind_video_to_tos(
            {"status": "completed", "video_url": source_url},
            "task-failed",
        )
    )

    assert result["video_url"] == source_url
    assert result["tos_transfer_error"] == "proxy download timed out"
    transfer.assert_awaited_once_with(source_url, task_id="task-failed")
