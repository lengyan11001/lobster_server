from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.api import comfly_proxy


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

