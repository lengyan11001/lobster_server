from __future__ import annotations

import pytest

from mcp.http_server import _normalize_video_generate_payload
from mcp.video_model_resolve import DASHSCOPE_WAN30_VIDEO_MODEL, resolve_video_model_id


def test_wan30_aliases_resolve_to_dashscope_model():
    assert resolve_video_model_id("wan3.0", False) == DASHSCOPE_WAN30_VIDEO_MODEL
    assert resolve_video_model_id("万相3.0", True) == DASHSCOPE_WAN30_VIDEO_MODEL
    assert resolve_video_model_id("wan3.0-video", False) == DASHSCOPE_WAN30_VIDEO_MODEL


def test_wan30_text_payload_keeps_prompt_and_supported_parameters():
    out = _normalize_video_generate_payload(
        {
            "model": "wan3.0",
            "prompt": "一只猫在草地上奔跑",
            "duration": 20,
            "aspect_ratio": "21:9",
            "resolution": "1080p",
            "prompt_extend": True,
        }
    )

    assert out == {
        "model": "wan3.0-video",
        "prompt": "一只猫在草地上奔跑",
        "duration": 20,
        "ratio": "21:9",
        "resolution": "1080P",
        "prompt_extend": True,
    }
    assert "image_url" not in out


def test_wan30_defaults_to_official_resolution():
    out = _normalize_video_generate_payload(
        {
            "model": "wan3.0",
            "prompt": "城市夜景延时摄影",
            "duration": 6,
        }
    )

    assert out["resolution"] == "1080P"
    assert out["ratio"] == "adaptive"


def test_wan30_image_payload_uses_first_image_reference():
    out = _normalize_video_generate_payload(
        {
            "model": "万相3.0",
            "prompt": "让图片中的人物自然挥手",
            "image_urls": [
                "https://cdn.example.com/first.png",
                "https://cdn.example.com/ignored.png",
            ],
            "duration": 5,
            "aspect_ratio": "portrait",
            "resolution": "720p",
        }
    )

    assert out["model"] == "wan3.0-video"
    assert out["image_url"] == "https://cdn.example.com/first.png"
    assert out["duration"] == 5
    assert out["ratio"] == "9:16"
    assert out["resolution"] == "720P"


@pytest.mark.asyncio
async def test_comfly_wan30_submit_uses_dashscope_schema(monkeypatch):
    from mcp import comfly_upstream

    observed = {}

    monkeypatch.setattr(
        comfly_upstream,
        "get_comfly_config",
        lambda token_group="": ("https://dashscope.example.com", "dashscope-wan30-key"),
    )
    monkeypatch.setattr(
        comfly_upstream,
        "lookup_comfly_model",
        lambda model_id: {
            "api_format": "dashscope_wan30",
            "comfly_model": "wan3.0-video",
            "token_group": "dashscope_wan30",
        },
    )

    async def fake_request(client, action, method, url, **kwargs):
        observed.update({"action": action, "method": method, "url": url, "kwargs": kwargs})
        return 200, {"output": {"task_id": "wan-task-1"}}, 1

    monkeypatch.setattr(comfly_upstream, "_request_comfly_json", fake_request)

    response = await comfly_upstream.call_comfly_video_generate(
        "wan3.0-video",
        {
            "prompt": "让图片中的人物自然挥手",
            "image_url": "https://cdn.example.com/first.png",
            "duration": 8,
            "aspect_ratio": "9:16",
            "resolution": "720p",
        },
    )

    assert response["output"]["task_id"] == "wan-task-1"
    assert observed["url"] == "https://dashscope.example.com/api/v1/services/aigc/video-generation/video-synthesis"
    assert observed["kwargs"]["headers"]["X-DashScope-Async"] == "enable"
    body = observed["kwargs"]["json"]
    assert body["model"] == "wan3.0-video"
    assert body["input"]["prompt"] == "让图片中的人物自然挥手"
    assert body["input"]["media"] == [
        {"type": "first_frame", "url": "https://cdn.example.com/first.png"}
    ]
    assert body["parameters"] == {
        "resolution": "720P",
        "ratio": "9:16",
        "duration": 8,
    }


def test_wan30_result_format_reads_dashscope_output_status_and_url():
    from mcp.comfly_upstream import format_comfly_video_response_as_sutui

    result = format_comfly_video_response_as_sutui(
        {
            "output": {
                "task_id": "wan-task-2",
                "task_status": "SUCCEEDED",
                "video_url": "https://cdn.example.com/result.mp4",
            }
        }
    )

    assert result["task_id"] == "wan-task-2"
    assert result["status"] == "completed"
    assert result["url"] == "https://cdn.example.com/result.mp4"


def test_direct_proxy_accepts_wan30_alias_and_uses_dashscope_policy():
    from backend.app.api import comfly_proxy

    assert comfly_proxy._canonical_video_model("wan3.0") == "wan3.0-video"
    assert comfly_proxy._canonical_video_model("万相 3.0") == "wan3.0-video"
    policy = comfly_proxy._video_provider_policy("wan3.0")
    assert policy["model_family"] == "wan30"
    assert policy["providers"] == [
        {
            "channel": "dashscope",
            "model": "wan3.0-video",
            "base_url": "/api/comfly-proxy",
        }
    ]


def test_direct_proxy_extracts_dashscope_task_id_from_output():
    from backend.app.api.comfly_proxy import _task_id_from_response

    assert _task_id_from_response({"output": {"task_id": "wan-task-3"}}) == "wan-task-3"


def test_dashscope_wan30_config_uses_only_dedicated_key(monkeypatch):
    from mcp import comfly_upstream

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("ALIYUN_DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_WAN30_API_KEY", raising=False)
    monkeypatch.setenv("COMFLY_API_BASE", "https://comfly.example.com")
    monkeypatch.setenv("COMFLY_API_KEY", "comfly-key")

    base, key = comfly_upstream.get_comfly_config("dashscope_wan30")

    assert base == "https://dashscope.aliyuncs.com"
    assert key == ""

    monkeypatch.setenv("DASHSCOPE_API_KEY", "generic-qwen-key")
    base, key = comfly_upstream.get_comfly_config("dashscope_wan30")
    assert base == "https://dashscope.aliyuncs.com"
    assert key == ""

    monkeypatch.setenv("DASHSCOPE_WAN30_API_KEY", "dedicated-wan30-key")
    base, key = comfly_upstream.get_comfly_config("dashscope_wan30")
    assert base == "https://dashscope.aliyuncs.com"
    assert key == "dedicated-wan30-key"
