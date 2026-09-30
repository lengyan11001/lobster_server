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
    _openmind_video_headers,
    _openmind_video_model,
    _remember_video_image_retry_context,
    _video_image_retry_contexts,
    _video_image_retry_poll_target,
    _video_image_retry_roots,
    _video_provider_policy,
    _xing_seedance_body,
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


def test_openmind_seedance_uses_dedicated_key_without_changing_other_video_channels(monkeypatch):
    from backend.app.api import comfly_proxy

    monkeypatch.setenv("OPENMIND_API_KEY", "global-openmind-key")
    monkeypatch.setenv("OPENMIND_SEEDANCE_API_KEY", "seedance-openmind-key")

    seedance_headers = _openmind_video_headers("doubao-seedance-2-0-260128")
    grok_headers = _openmind_video_headers("grok-video-3")

    assert seedance_headers["Authorization"] == "Bearer seedance-openmind-key"
    assert grok_headers["Authorization"] == "Bearer global-openmind-key"
    assert comfly_proxy._is_openmind_seedance_model("seedance2.0-HD")


def test_openmind_seedance_default_model_uses_verified_model(monkeypatch):
    monkeypatch.delenv("OPENMIND_SEEDANCE_MODEL", raising=False)
    monkeypatch.delenv("OPENMIND_SEEDANCE_FAST_MODEL", raising=False)

    assert _openmind_video_model("doubao-seedance-2-0-260128") == "seedance2.0"
    assert _openmind_video_model("doubao-seedance-2-0-fast-260128") == "seedance2.0"


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


def test_grok_family_runs_openmind_first_and_wan30_last():
    """用户口径（2026-09-30）：openmind 优先，wan3.0(DashScope) 放最后调度。"""
    policy = _video_provider_policy("xai/grok-imagine-video-1.5/image-to-video")

    assert policy["ok"] is True
    assert policy["model_family"] == "grok"
    assert policy["providers"] == [
        {
            "channel": "openmind",
            "model": "grok-video-3",
            "base_url": "/api/comfly-proxy",
        },
        {
            "channel": "comfly",
            "model": "grok-imagine-video-1.5",
            "base_url": "/api/comfly-proxy",
        },
        {
            "channel": "xai",
            "model": "grok-imagine-video-1.5",
            "base_url": "/api/comfly-proxy",
        },
        {
            "channel": "dashscope",
            "model": "wan3.0-video",
            "base_url": "/api/comfly-proxy",
        },
    ]


def test_wan30_inclusion_switch(monkeypatch):
    """VIDEO_POLICY_WAN30_POSITION=off 时不带万相3.0；默认带上但排在最后（openmind 优先）。"""
    monkeypatch.setenv("VIDEO_POLICY_WAN30_POSITION", "last")
    providers = _video_provider_policy("grok-imagine-video-1.5", "comfly")["providers"]
    assert providers[-1] == {
        "channel": "dashscope",
        "model": "wan3.0-video",
        "base_url": "/api/comfly-proxy",
    }

    monkeypatch.setenv("VIDEO_POLICY_WAN30_POSITION", "off")
    providers = _video_provider_policy("grok-imagine-video-1.5", "comfly")["providers"]
    assert all(item["channel"] != "dashscope" for item in providers)

    monkeypatch.delenv("VIDEO_POLICY_WAN30_POSITION", raising=False)
    providers = _video_provider_policy("grok-imagine-video-1.5", "comfly")["providers"]
    assert providers[0]["channel"] == "openmind"
    assert providers[-1]["channel"] == "dashscope"


def test_video_provider_global_order_openmind_first_wan_seedance_last(monkeypatch):
    """全局调度顺序：openmind 优先；wan3.0(dashscope) 与 seedance 通道放最后。"""
    monkeypatch.setenv("VIDEO_POLICY_WAN30_POSITION", "first")

    grok = _video_provider_policy("grok-imagine-video-1.5-preview", "openmind", "seedance_tvc")
    channels = [item["channel"] for item in grok["providers"]]
    assert channels[0] == "openmind"
    assert channels[-1] == "dashscope"

    seedance20 = _video_provider_policy("seedance2.0-900", "")
    channels20 = [item["channel"] for item in seedance20["providers"]]
    assert channels20[0] == "openmind"
    assert channels20[-1] == "seedance"

    unknown = _video_provider_policy("some-unknown-video-model", "")
    channels_unknown = [item["channel"] for item in unknown["providers"]]
    assert channels_unknown[0] == "openmind"
    assert channels_unknown[-1] == "seedance"


def test_veo_family_uses_openmind_never_yunwu():
    policy = _video_provider_policy("apiz/veo3.1/text-to-video")

    assert policy["ok"] is True
    assert policy["model_family"] == "veo31"
    assert policy["providers"] == [
        {
            "channel": "openmind",
            "model": "veo3.1",
            "base_url": "/api/comfly-proxy",
        },
        {
            "channel": "comfly",
            "model": "veo3.1-fast",
            "base_url": "/api/comfly-proxy",
        },
    ]
    assert all(item["channel"] != "yunwu" for item in policy["providers"])


def test_seedance25_uses_xing_provider():
    policy = _video_provider_policy("seedance-2.5")

    assert policy["ok"] is True
    assert policy["model_family"] == "seedance25"
    assert policy["providers"] == [
        {
            "channel": "xing",
            "model": "seedance-2.5",
            "base_url": "/api/comfly-proxy",
        }
    ]


def test_xing_seedance_requested_model_overrides_env_default(monkeypatch):
    monkeypatch.setenv("XING_SEEDANCE_MODEL", "seedance2.0-900")

    body = _xing_seedance_body(
        {"prompt": "test video", "duration": 10, "aspect_ratio": "16:9"},
        "seedance-2.5",
    )

    assert body["model"] == "seedance-2.5"
    assert body["ratio"] == "16:9"


def test_xai_video_model_has_billable_pricing_entry():
    pricing_path = Path(__file__).resolve().parents[2] / "comfly_pricing.json"
    pricing = json.loads(pricing_path.read_text(encoding="utf-8"))
    entry = pricing["models"]["grok-imagine-video-1.5"]

    assert entry["price_type"] == "per_call"
    assert entry["price_per_unit"] == 160
    assert entry["api_format"] == "comfyui_grok"
    assert entry["token_group"] == "comfyui_video"


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
    queued = []
    local_save = AsyncMock(side_effect=AssertionError("main server must not download or save the video"))
    monkeypatch.setattr(comfly_proxy, "_transfer_video_to_tos_via_proxy", transfer)
    monkeypatch.setattr(comfly_proxy, "_save_bytes_or_tos", local_save)
    monkeypatch.setattr(
        comfly_proxy,
        "spawn_tracked_task",
        lambda coro, *, name: queued.append((coro, name)),
    )
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

    assert result["video_url"] == source_url
    assert result["source_video_url"] == source_url
    assert result["tos_transfer_status"] == "queued"
    assert result["video"]["url"] == source_url
    assert result["video"]["source_url"] == source_url
    assert "tos_transfer_error" not in result
    transfer.assert_not_awaited()
    assert len(queued) == 1
    assert queued[0][1] == "openmind-video-tos-transfer-task-1"
    asyncio.run(queued[0][0])
    transfer.assert_awaited_once_with(source_url, task_id="task-1")
    assert comfly_proxy._openmind_tos_url_cache[f"task-1:{source_url}"] == tos_url
    local_save.assert_not_called()


def test_openmind_video_proxy_transfer_failure_is_reported(monkeypatch):
    from backend.app.api import comfly_proxy

    source_url = "https://vidgen.x.ai/example-failed.mp4"
    transfer = AsyncMock(side_effect=RuntimeError("proxy download timed out"))
    queued = []
    monkeypatch.setattr(comfly_proxy, "_transfer_video_to_tos_via_proxy", transfer)
    monkeypatch.setattr(
        comfly_proxy,
        "spawn_tracked_task",
        lambda coro, *, name: queued.append((coro, name)),
    )
    comfly_proxy._openmind_tos_url_cache.clear()

    result = asyncio.run(
        _mirror_openmind_video_to_tos(
            {"status": "completed", "video_url": source_url},
            "task-failed",
        )
    )

    assert result["video_url"] == source_url
    assert result["tos_transfer_status"] == "queued"
    assert "tos_transfer_error" not in result
    assert len(queued) == 1
    asyncio.run(queued[0][0])
    transfer.assert_awaited_once_with(source_url, task_id="task-failed")


def test_openmind_video_poll_uses_cached_tos_url_without_queue(monkeypatch):
    from backend.app.api import comfly_proxy

    source_url = "https://vidgen.x.ai/example-cached.mp4"
    tos_url = "https://assets.example.com/openmind-cached.mp4"
    queued = []
    comfly_proxy._openmind_tos_url_cache.clear()
    comfly_proxy._openmind_tos_url_cache[f"task-cached:{source_url}"] = tos_url
    monkeypatch.setattr(
        comfly_proxy,
        "spawn_tracked_task",
        lambda coro, *, name: queued.append((coro, name)),
    )

    result = asyncio.run(
        _mirror_openmind_video_to_tos(
            {"status": "completed", "video_url": source_url, "video": {"url": source_url}},
            "task-cached",
        )
    )

    assert result["video_url"] == tos_url
    assert result["tos_url"] == tos_url
    assert result["source_video_url"] == source_url
    assert result["video"]["url"] == tos_url
    assert result["video"]["source_url"] == source_url
    assert queued == []


def test_local_bestseller_video_policy_skips_expensive_wan30(monkeypatch):
    """同城爆款单段视频固定 OpenMind(160)，不能插 wan3.0(1200)，否则用户余额不足直接失败。"""
    monkeypatch.setenv("VIDEO_POLICY_WAN30_POSITION", "first")

    policy = _video_provider_policy("grok-imagine-video-1.5-preview", "openmind", "local_bestseller")
    assert policy["model_family"] == "grok"
    assert [item["channel"] for item in policy["providers"]] == ["openmind", "comfly"]
    assert policy["providers"][0]["model"] == "grok-video-3"
    assert all(item["model"] != "wan3.0-video" for item in policy["providers"])

    # 其它功能（分镜台批量）仍然保留 wan3.0 兜底
    other = _video_provider_policy("grok-imagine-video-1.5-preview", "openmind", "seedance_tvc")
    assert any(item["channel"] == "dashscope" for item in other["providers"])
    assert [item["channel"] for item in other["providers"]][-1] == "dashscope"


def test_yingmeng_1_0_never_routes_to_yunwu():
    """影梦 1.0（yunwu-veo3.1-plus）已停用 yunwu：任何入口都只能拿到 OpenMind / comfly。"""
    for model, channel_name in (
        ("yunwu-veo3.1-plus", "yunwu"),
        ("veo3.1", "yunwu"),
        ("veo3.1", "openmind"),
        ("veo3.1", ""),
    ):
        policy = _video_provider_policy(model, channel_name)
        assert policy["model_family"] == "veo31", (model, channel_name)
        assert [item["channel"] for item in policy["providers"]] == ["openmind", "comfly"], (model, channel_name)

    h5 = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    assert 'return { model: "veo3.1", channel: "openmind" };' in h5
    assert 'return { model: "veo3.1", channel: "yunwu" };' not in h5
