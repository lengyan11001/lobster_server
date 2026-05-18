from __future__ import annotations

from mcp.http_server import _normalize_video_generate_payload
from mcp.video_model_resolve import resolve_video_model_id


def test_default_video_model_uses_xai_grok_text_to_video():
    out = _normalize_video_generate_payload({"prompt": "一条产品宣传短视频"})

    assert out["model"] == "xai/grok-imagine-video/text-to-video"
    assert out["duration"] == 5


def test_default_video_model_switches_to_xai_grok_image_to_video_when_image_present():
    out = _normalize_video_generate_payload(
        {
            "prompt": "基于这张图生成口播视频",
            "image_url": "https://example.com/a.png",
            "duration": 8,
        }
    )

    assert out["model"] == "xai/grok-imagine-video/image-to-video"
    assert out["image_url"] == "https://example.com/a.png"
    assert out["duration"] == 8


def test_grok_aliases_resolve_to_xai_grok_models():
    assert resolve_video_model_id("grok-video-3", False) == "xai/grok-imagine-video/text-to-video"
    assert resolve_video_model_id("grok-video-3", True) == "xai/grok-imagine-video/image-to-video"
    assert (
        resolve_video_model_id("xai/grok-imagine-video/text-to-video", True)
        == "xai/grok-imagine-video/image-to-video"
    )


def test_xai_grok_models_keep_sutui_route():
    from mcp.comfly_upstream import should_route_to_comfly

    assert should_route_to_comfly("video.generate", "xai/grok-imagine-video/text-to-video") is False
    assert should_route_to_comfly("video.generate", "xai/grok-imagine-video/image-to-video") is False


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
