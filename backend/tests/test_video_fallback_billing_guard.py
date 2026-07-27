from __future__ import annotations

from starlette.requests import Request

from backend.app.api.comfly_proxy import (
    _is_trusted_internal_video_fallback,
    _openmind_video_body,
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


def test_openmind_video_body_uses_string_duration_and_all_references():
    body = _openmind_video_body(
        {
            "prompt": "product video",
            "duration": 8,
            "image_urls": ["https://example.com/a.png", "https://example.com/b.png"],
        },
        "grok-video-3",
        {},
    )

    assert body["duration"] == "8"
    assert body["seconds"] == "8"
    assert body["images"] == ["https://example.com/a.png", "https://example.com/b.png"]
    assert body["image_urls"] == body["images"]
