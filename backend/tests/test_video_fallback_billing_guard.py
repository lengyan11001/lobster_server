from __future__ import annotations

import json
from pathlib import Path

from starlette.requests import Request

from backend.app.api.comfly_proxy import (
    _is_trusted_internal_video_fallback,
    _openmind_video_body,
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
            "image_urls": ["https://example.com/a.png", "https://example.com/b.png"],
        },
        "grok-video-3",
        {},
    )

    assert body["duration"] == 8
    assert "seconds" not in body
    assert body["images"] == ["https://example.com/a.png", "https://example.com/b.png"]
    assert body["image_urls"] == body["images"]


def test_xai_video_body_maps_duration_and_first_image():
    body = _xai_video_body(
        {
            "prompt": "product video",
            "seconds": "8",
            "image_urls": ["https://example.com/a.png", "https://example.com/b.png"],
        },
        "grok-imagine-video-1.5",
    )

    assert body == {
        "model": "grok-imagine-video-1.5",
        "prompt": "product video",
        "duration": 8,
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
