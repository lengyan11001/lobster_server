from __future__ import annotations

import pytest

import mcp.http_server as mcp_server
from mcp.http_server import (
    _normalize_video_generate_payload,
    _video_fallback_allowed,
    _video_fallback_candidates,
    _video_fallback_payload,
)
from mcp.video_model_resolve import resolve_default_video_model_id, resolve_video_model_id


def test_default_video_model_uses_apiz_veo_text_to_video():
    out = _normalize_video_generate_payload({"prompt": "一条产品宣传短视频"})

    assert out == {
        "model": "apiz/veo3.1/text-to-video",
        "prompt": "一条产品宣传短视频",
        "duration": 4,
        "aspect_ratio": "16:9",
        "resolution": "720p",
    }


def test_default_video_model_switches_to_apiz_veo_image_to_video_when_image_present():
    out = _normalize_video_generate_payload(
        {
            "prompt": "基于这张图生成口播视频",
            "image_url": "https://example.com/a.png",
            "duration": 8,
        }
    )

    assert out["model"] == "apiz/veo3.1/image-to-video"
    assert out["image_url"] == "https://example.com/a.png"
    assert out["duration"] == 8


def test_apiz_veo_reference_mode_uses_three_images_and_fixed_eight_seconds():
    out = _normalize_video_generate_payload(
        {
            "model": "apiz/veo3.1/text-to-video",
            "prompt": "参考这些产品图生成视频",
            "image_urls": [
                "https://example.com/1.png",
                "https://example.com/2.png",
                "https://example.com/3.png",
                "https://example.com/4.png",
            ],
            "duration": 4,
            "aspect_ratio": "1:1",
            "resolution": "4k",
        }
    )

    assert out["model"] == "apiz/veo3.1/reference-to-video"
    assert out["image_urls"] == [
        "https://example.com/1.png",
        "https://example.com/2.png",
        "https://example.com/3.png",
    ]
    assert out["duration"] == 8
    assert out["aspect_ratio"] == "16:9"
    assert out["resolution"] == "720p"


def test_apiz_veo_duration_is_coerced_to_supported_enum():
    out = _normalize_video_generate_payload(
        {
            "model": "apiz/veo3.1/text-to-video",
            "prompt": "产品视频",
            "duration": 12,
            "aspect_ratio": "9:16",
            "resolution": "1080P",
        }
    )

    assert out["duration"] == 8
    assert out["aspect_ratio"] == "9:16"
    assert out["resolution"] == "1080p"


def test_grok_text_to_video_keeps_explicit_duration():
    out = _normalize_video_generate_payload(
        {
            "model": "xai/grok-imagine-video/text-to-video",
            "prompt": "product video",
            "duration": 30,
        }
    )

    assert out["duration"] == 30


def test_grok_aliases_resolve_to_xai_grok_models():
    assert resolve_video_model_id("grok-video-3", False) == "xai/grok-imagine-video/text-to-video"
    assert resolve_video_model_id("grok-video-3", True) == "xai/grok-imagine-video/image-to-video"
    assert (
        resolve_video_model_id("xai/grok-imagine-video/text-to-video", True)
        == "xai/grok-imagine-video/image-to-video"
    )


def test_stale_configured_default_migrates_to_apiz_without_changing_explicit_grok():
    assert (
        resolve_default_video_model_id("xai/grok-imagine-video/text-to-video", False)
        == "apiz/veo3.1/text-to-video"
    )
    assert (
        resolve_default_video_model_id("xai/grok-imagine-video/text-to-video", True)
        == "apiz/veo3.1/image-to-video"
    )
    assert resolve_video_model_id("grok-video-3", False) == "xai/grok-imagine-video/text-to-video"


def test_apiz_veo_resolution_uses_image_count():
    assert resolve_video_model_id("veo3.1", False, image_count=0) == "apiz/veo3.1/text-to-video"
    assert resolve_video_model_id("veo3.1", True, image_count=1) == "apiz/veo3.1/image-to-video"
    assert resolve_video_model_id("veo3.1", True, image_count=2) == "apiz/veo3.1/reference-to-video"


@pytest.mark.parametrize(
    ("legacy_model", "image_urls", "expected_model"),
    [
        ("fal-ai/veo3.1", [], "apiz/veo3.1/text-to-video"),
        (
            "fal-ai/veo3.1",
            ["https://example.com/a.png"],
            "apiz/veo3.1/image-to-video",
        ),
        (
            "fal-ai/veo3.1/image-to-video",
            ["https://example.com/a.png"],
            "apiz/veo3.1/image-to-video",
        ),
        (
            "fal-ai/veo3.1/reference-to-video",
            ["https://example.com/a.png", "https://example.com/b.png"],
            "apiz/veo3.1/reference-to-video",
        ),
    ],
)
def test_legacy_fal_veo31_routes_to_apiz_family(legacy_model, image_urls, expected_model):
    payload = {"model": legacy_model, "prompt": "legacy client request"}
    if image_urls:
        payload["image_urls"] = image_urls

    out = _normalize_video_generate_payload(payload)

    assert out["model"] == expected_model


def test_video_fallback_policy_depends_on_input_mode():
    text_candidates = _video_fallback_candidates({"prompt": "text video"})
    image_candidates = _video_fallback_candidates(
        {"prompt": "image video", "image_url": "https://example.com/a.png"}
    )

    assert text_candidates[0] == {"channel": "comfly", "model": "veo3.1-fast"}
    assert image_candidates[0] == {"channel": "openmind", "model": "grok-video-3"}
    assert [item["channel"] for item in image_candidates] == ["openmind", "comfly", "yunwu"]


def test_comfly_veo_fallback_drops_apiz_only_parameters():
    out = _video_fallback_payload(
        {
            "model": "apiz/veo3.1/text-to-video",
            "prompt": "test",
            "duration": 8,
            "resolution": "1080p",
            "aspect_ratio": "9:16",
        },
        "veo3.1-fast",
    )

    assert out["model"] == "veo3.1-fast"
    assert out["aspect_ratio"] == "9:16"
    assert out["enhance_prompt"] is True
    assert "duration" not in out
    assert "resolution" not in out


def test_video_fallback_does_not_bypass_non_retryable_failures():
    assert _video_fallback_allowed("HTTP 503 upstream unavailable") is True
    assert _video_fallback_allowed("HTTP 429 temporary rate limit") is True
    assert _video_fallback_allowed("HTTP 402 insufficient credits") is False
    assert _video_fallback_allowed("HTTP 401 unauthorized") is False
    assert _video_fallback_allowed("HTTP 422 invalid parameter") is False
    assert _video_fallback_allowed("content safety violation") is False


@pytest.mark.asyncio
async def test_submit_fallback_exposes_one_public_task_id(monkeypatch):
    async def fake_submit(state, candidate, token, request):
        return {"id": "provider-private-task", "status": "queued"}

    monkeypatch.setattr(mcp_server, "_submit_video_fallback_candidate", fake_submit)
    mcp_server._video_fallback_tasks.clear()

    result = await mcp_server._start_video_fallback_after_submit_failure(
        {"model": "apiz/veo3.1/text-to-video", "prompt": "test"},
        "HTTP 503 upstream unavailable",
        "jwt",
        None,
    )

    assert result is not None
    assert result["task_id"].startswith("video-fallback-")
    assert result["task_id"] != "provider-private-task"
    state = mcp_server._video_fallback_tasks[result["task_id"]]
    assert state["provider_task_id"] == "provider-private-task"


@pytest.mark.asyncio
async def test_fallback_stops_after_non_retryable_candidate_error(monkeypatch):
    attempts = []

    async def fake_submit(state, candidate, token, request):
        attempts.append(candidate["channel"])
        return {"error": {"message": "HTTP 401 unauthorized"}, "status": "failed"}

    monkeypatch.setattr(mcp_server, "_submit_video_fallback_candidate", fake_submit)
    mcp_server._video_fallback_tasks.clear()

    state = mcp_server._new_video_fallback_state(
        "public-task",
        {"model": "apiz/veo3.1/text-to-video", "prompt": "test"},
    )
    state["channel"] = ""
    state["provider_task_id"] = ""
    result = await mcp_server._start_next_video_fallback(state, "jwt", None)

    assert attempts == ["comfly"]
    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_apiz_terminal_failure_switches_provider_without_changing_task_id(monkeypatch):
    public_task_id = "apiz-public-task"
    mcp_server._video_fallback_tasks.clear()
    mcp_server._register_apiz_video_task(
        public_task_id,
        {"model": "apiz/veo3.1/text-to-video", "prompt": "test"},
    )

    async def fake_start(state, token, request):
        state["channel"] = "comfly"
        state["provider_task_id"] = "provider-private-task"
        return {"task_id": state["public_task_id"], "status": "pending", "fallback_used": True}

    monkeypatch.setattr(mcp_server, "_start_next_video_fallback", fake_start)
    result = await mcp_server._maybe_fallback_after_apiz_poll(
        public_task_id,
        {"task_id": public_task_id, "status": "failed", "message": "HTTP 503 upstream unavailable"},
        "jwt",
        None,
    )

    assert result["task_id"] == public_task_id
    assert result["status"] == "pending"
    assert mcp_server._video_fallback_tasks[public_task_id]["provider_task_id"] == "provider-private-task"


@pytest.mark.asyncio
async def test_fallback_poll_does_not_switch_after_non_retryable_failure(monkeypatch):
    public_task_id = "public-task"
    state = mcp_server._new_video_fallback_state(
        public_task_id,
        {"model": "apiz/veo3.1/text-to-video", "prompt": "test"},
    )
    state["channel"] = "comfly"
    state["provider_task_id"] = "provider-task"
    mcp_server._video_fallback_tasks.clear()
    mcp_server._remember_video_fallback_state(public_task_id, state)

    class Response:
        status_code = 401
        text = "unauthorized"
        content = b"unauthorized"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(mcp_server.httpx, "AsyncClient", lambda *args, **kwargs: Client())
    monkeypatch.setattr(mcp_server, "_video_fallback_proxy_headers", lambda token, request: {})

    async def should_not_start(*args, **kwargs):
        raise AssertionError("non-retryable polling failure must not switch provider")

    monkeypatch.setattr(mcp_server, "_start_next_video_fallback", should_not_start)
    result = await mcp_server._poll_registered_video_fallback(public_task_id, "jwt", None)

    assert result["status"] == "failed"
    assert "401" in str(result["error"])


def test_xai_grok_models_keep_sutui_route():
    from mcp.comfly_upstream import lookup_comfly_model, should_route_to_comfly

    assert should_route_to_comfly("video.generate", "xai/grok-imagine-video/text-to-video") is False
    assert should_route_to_comfly("video.generate", "xai/grok-imagine-video/image-to-video") is False
    assert lookup_comfly_model("xai/grok-imagine-video/text-to-video") is None
    assert lookup_comfly_model("xai/grok-imagine-video/image-to-video") is None


def test_grok_resolution_is_limited_to_upstream_enum():
    out = _normalize_video_generate_payload(
        {
            "model": "xai/grok-imagine-video/text-to-video",
            "prompt": "产品短视频",
            "resolution": "720P",
        }
    )
    assert out["resolution"] == "720p"

    out = _normalize_video_generate_payload(
        {
            "model": "xai/grok-imagine-video/text-to-video",
            "prompt": "产品短视频",
            "resolution": "1080p",
        }
    )
    assert out["resolution"] == "720p"

    out = _normalize_video_generate_payload(
        {
            "model": "xai/grok-imagine-video/text-to-video",
            "prompt": "产品短视频",
            "resolution": "auto",
        }
    )
    assert "resolution" not in out
