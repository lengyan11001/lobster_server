from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.api import comfly_proxy
from mcp import comfly_upstream


@pytest.mark.asyncio
async def test_grok15_url_reference_is_uploaded_as_file(monkeypatch, tmp_path):
    source = tmp_path / "reference.jpg"
    source.write_bytes(b"fake-jpeg")
    captured = {}

    async def fake_download(url: str):
        captured["url"] = url
        return source, "reference.jpg", "image/jpeg"

    monkeypatch.setattr(comfly_proxy, "_download_reference_url_to_temp_file", fake_download)

    data, files, upstream_model, open_files, temp_paths = await comfly_proxy._build_comfly_grok15_multipart(
        {
            "prompt": "test",
            "duration": 10,
            "aspect_ratio": "9:16",
            "image_url": "https://cdn.example.com/reference.jpg",
        },
        "grok-video-3",
        {"api_format": "grok"},
    )

    try:
        assert captured["url"] == "https://cdn.example.com/reference.jpg"
        assert data == {
            "model": "grok-1.5-video-10s",
            "prompt": "test",
            "size": "720x1280",
        }
        assert upstream_model == "grok-1.5-video-10s"
        assert len(files) == 1
        field_name, file_tuple = files[0]
        assert field_name == "input_reference"
        assert file_tuple[0] == "reference.jpg"
        assert file_tuple[2] == "image/jpeg"
        assert hasattr(file_tuple[1], "read")
        assert file_tuple[1].read() == b"fake-jpeg"
        assert temp_paths == [source]
    finally:
        for handle in open_files:
            handle.close()


@pytest.mark.asyncio
async def test_grok15_duration_above_ten_uses_fifteen_second_model(monkeypatch, tmp_path):
    source = tmp_path / "reference.jpg"
    source.write_bytes(b"fake-jpeg")

    async def fake_download(_url: str):
        return source, "reference.jpg", "image/jpeg"

    monkeypatch.setattr(comfly_proxy, "_download_reference_url_to_temp_file", fake_download)

    data, _files, upstream_model, open_files, _temp_paths = await comfly_proxy._build_comfly_grok15_multipart(
        {
            "prompt": "test",
            "duration": 15,
            "image_url": "https://cdn.example.com/reference.jpg",
        },
        "grok-video-3",
        {"api_format": "grok"},
    )

    try:
        assert data["model"] == "grok-1.5-video-15s"
        assert upstream_model == "grok-1.5-video-15s"
    finally:
        for handle in open_files:
            handle.close()


def test_comfly_grok_v2_duration_above_ten_stays_on_ten_second_slot():
    body = comfly_proxy._body_for_upstream_model(
        {
            "prompt": "test",
            "duration": 15,
            "aspect_ratio": "9:16",
            "image_url": "https://cdn.example.com/reference.jpg",
        },
        "grok-video-3",
        {"api_format": "grok", "comfly_model": "grok-video-3"},
    )

    assert body["duration"] == 10


@pytest.mark.asyncio
async def test_grok15_rejects_plain_url_like_reference():
    with pytest.raises(RuntimeError, match="requires input_reference"):
        await comfly_proxy._build_comfly_grok15_multipart(
            {
                "prompt": "test",
                "duration": 6,
                "image_url": "ftp://example.com/reference.jpg",
            },
            "grok-video-3",
            {"api_format": "grok"},
        )


@pytest.mark.asyncio
async def test_comfyui_grok_keeps_canonical_model_and_file_reference(tmp_path):
    source = tmp_path / "reference.jpg"
    source.write_bytes(b"fake-jpeg")

    data, files = await comfly_upstream._build_comfyui_grok_multipart(
        {
            "prompt": "test",
            "aspect_ratio": "9:16",
        },
        model_id="grok-imagine-video-1.5",
        prompt="test",
        first_image=str(source),
    )

    assert data == {
        "model": "grok-imagine-video-1.5",
        "prompt": "test",
        "size": "720x1280",
    }
    assert files["model"] == (None, "grok-imagine-video-1.5")
    assert files["input_reference"][0] == "reference.jpg"
    assert files["input_reference"][1] == b"fake-jpeg"
    assert files["input_reference"][2] == "image/jpeg"
