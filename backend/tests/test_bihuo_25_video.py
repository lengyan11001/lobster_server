from __future__ import annotations

import pytest

import mcp.http_server as mcp_server
from mcp.http_server import (
    _apiz_create_request_parts,
    _normalize_video_generate_payload,
    _prepare_bihuo_25_video_input,
)
from mcp.video_model_resolve import APIZ_BIHUO_25_VIDEO_MODEL, resolve_video_model_id


def test_bihuo_25_alias_resolves_without_falling_back_to_old_seed2():
    assert resolve_video_model_id("必火2.5", True) == APIZ_BIHUO_25_VIDEO_MODEL
    assert resolve_video_model_id("st-ai/super-seed2-lite", True) == APIZ_BIHUO_25_VIDEO_MODEL


def test_bihuo_25_multimodal_payload_keeps_all_reference_types():
    out = _normalize_video_generate_payload(
        {
            "model": APIZ_BIHUO_25_VIDEO_MODEL,
            "prompt": "让【@图片1】中的产品跟随【@音频1】的节奏运动",
            "functionMode": "omini",
            "image_urls": ["https://example.com/product.png"],
            "video_urls": ["https://example.com/motion.mp4"],
            "audio_urls": ["https://example.com/music.mp3"],
            "ratio": "adaptive",
            "resolution": "480p",
            "duration": 18,
        }
    )

    assert out == {
        "model": APIZ_BIHUO_25_VIDEO_MODEL,
        "__apiz_params_model": "Seedance_2.5",
        "prompt": "让【@图片1】中的产品跟随【@音频1】的节奏运动",
        "functionMode": "omini",
        "ratio": "adaptive",
        "resolution": "480p",
        "filePaths": [
            "https://example.com/product.png",
            "https://example.com/motion.mp4",
            "https://example.com/music.mp3",
        ],
        "duration": 18,
    }


def test_bihuo_25_first_last_frame_uses_singular_upstream_mode():
    out = _normalize_video_generate_payload(
        {
            "model": APIZ_BIHUO_25_VIDEO_MODEL,
            "prompt": "平滑推进",
            "functionMode": "first_last_frames",
            "image_url": "https://example.com/first.png",
            "end_image_url": "https://example.com/last.png",
            "duration": 12,
        }
    )

    assert out["functionMode"] == "first_last_frame"
    assert out["image_url"] == "https://example.com/first.png"
    assert out["end_image_url"] == "https://example.com/last.png"
    assert out["duration"] == 12
    assert "filePaths" not in out


def test_bihuo_25_edit_omits_duration_and_extend_keeps_it():
    base = {
        "model": APIZ_BIHUO_25_VIDEO_MODEL,
        "prompt": "保持主体一致",
        "video_urls": ["https://example.com/source.mp4"],
        "duration": 21,
    }
    edit = _normalize_video_generate_payload({**base, "functionMode": "edit"})
    extend = _normalize_video_generate_payload({**base, "functionMode": "extend"})

    assert edit["filePaths"] == ["https://example.com/source.mp4"]
    assert "duration" not in edit
    assert extend["duration"] == 21


def test_bihuo_25_apiz_request_sets_inner_model_without_leaking_private_key():
    normalized = _normalize_video_generate_payload(
        {
            "model": APIZ_BIHUO_25_VIDEO_MODEL,
            "prompt": "延长镜头",
            "functionMode": "extend",
            "video_urls": ["https://example.com/source.mp4"],
            "duration": 8,
        }
    )

    model, params = _apiz_create_request_parts(normalized)

    assert model == APIZ_BIHUO_25_VIDEO_MODEL
    assert params["model"] == "Seedance_2.5"
    assert "__apiz_params_model" not in params


@pytest.mark.asyncio
async def test_bihuo_25_edit_transfers_external_video(monkeypatch):
    calls = []

    async def fake_call(*args, **kwargs):
        calls.append((args, kwargs))
        return {"result": {"content": [{"type": "text", "text": '{"url":"https://cdn.example.com/source.mp4"}'}]}}

    monkeypatch.setattr(mcp_server, "_call_upstream_mcp_tool", fake_call)
    payload = {
        "model": APIZ_BIHUO_25_VIDEO_MODEL,
        "functionMode": "edit",
        "filePaths": ["https://tos.example.com/source.mp4"],
    }

    out = await _prepare_bihuo_25_video_input(
        payload,
        upstream_url="https://api.example.com/mcp",
        sutui_token="secret",
        brand_mark="bihuo",
    )

    assert out["filePaths"] == ["https://cdn.example.com/source.mp4"]
    assert calls[0][0][1] == "transfer_url"
    assert calls[0][0][2] == {"url": "https://tos.example.com/source.mp4", "type": "image"}


@pytest.mark.parametrize("duration", [3, 31])
def test_bihuo_25_rejects_unsupported_duration(duration):
    with pytest.raises(ValueError, match="4-30"):
        _normalize_video_generate_payload(
            {
                "model": APIZ_BIHUO_25_VIDEO_MODEL,
                "prompt": "测试",
                "functionMode": "omini",
                "image_urls": ["https://example.com/a.png"],
                "duration": duration,
            }
        )
