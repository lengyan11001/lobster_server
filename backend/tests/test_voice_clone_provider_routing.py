from __future__ import annotations

import asyncio
import io

from starlette.datastructures import UploadFile
from starlette.requests import Request


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/hifly/my/voice/create-upload",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def test_qwen_voice_clone_does_not_require_hifly_token(db_session, test_user, monkeypatch):
    from backend.app.api import hifly_assets

    calls = []

    async def forbidden_hifly_post(*_args, **_kwargs):
        raise AssertionError("Qwen voice cloning must not call HiFly")

    async def clone_voice(**kwargs):
        calls.append(kwargs)
        return {
            "voice_id": "qwen-test-voice",
            "clone_raw": {"request_id": "qwen-request"},
        }

    async def preview_voice(**_kwargs):
        return {"audio_bytes": b"preview", "duration_seconds": 1}

    async def store_source(_uploaded):
        return None

    async def store_demo(*_args, **_kwargs):
        return {"asset_id": "demo", "source_url": "https://example.test/demo.mp3"}

    monkeypatch.setattr(hifly_assets, "_voice_tts_provider", lambda: hifly_assets._QWEN_PROVIDER)
    monkeypatch.setattr(hifly_assets, "_post", forbidden_hifly_post)
    monkeypatch.setattr(hifly_assets, "_qwen_clone_voice", clone_voice)
    monkeypatch.setattr(hifly_assets, "_qwen_tts_audio", preview_voice)
    monkeypatch.setattr(hifly_assets, "_store_input_asset", store_source)
    monkeypatch.setattr(hifly_assets, "_persist_input_asset", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hifly_assets, "_persist_voice_demo_asset", store_demo)

    result = asyncio.run(
        hifly_assets.create_my_voice_upload(
            request=_request(),
            token="expired-hifly-token",
            title="实时录音",
            voice_type=8,
            languages="zh",
            file=UploadFile(filename="voice-record.wav", file=io.BytesIO(b"RIFFvoice-sample")),
            current_user=test_user,
            db=db_session,
        )
    )

    assert result["ok"] is True
    assert result["voice"] == "qwen-test-voice"
    assert calls[0]["filename"] == "voice-record.wav"
    assert calls[0]["raw"] == b"RIFFvoice-sample"
