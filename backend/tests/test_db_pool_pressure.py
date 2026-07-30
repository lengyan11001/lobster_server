from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from starlette.requests import Request


def _request(brand_mark: str = "bihuo") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/probe",
            "headers": [(b"x-lobster-brand", brand_mark.encode("ascii"))],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def test_authentication_releases_its_read_transaction(db_session, test_user):
    from backend.app.api.auth import access_token_claims, create_access_token, get_current_user

    user_id = int(test_user.id)
    token = create_access_token(access_token_claims(test_user))
    db_session.rollback()

    loaded = asyncio.run(get_current_user(_request(), token, db_session))

    assert not db_session.in_transaction()
    assert int(loaded.id) == user_id


def test_voice_preview_releases_db_before_tts(db_session, test_user, monkeypatch):
    from backend.app.api import hifly_assets
    from backend.app.models import UserHiflyVoiceAsset

    user_id = int(test_user.id)
    row = UserHiflyVoiceAsset(
        user_id=user_id,
        title="test voice",
        status="completed",
        hifly_task_id="qwen_voice_test_task",
        hifly_voice_id="qwen-test-voice",
        meta={"provider": "qwen", "qwen_voice_id": "qwen-test-voice"},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()

    async def fake_preview_tts_audio(**_kwargs):
        assert not db_session.in_transaction()
        return {
            "audio_bytes": b"test-audio",
            "duration_seconds": 1,
            "segments": ["test"],
            "extra_info": {},
        }

    monkeypatch.setattr(hifly_assets, "_preview_tts_audio", fake_preview_tts_audio)
    body = hifly_assets.HiflyVoicePreviewTtsBody(voice="qwen-test-voice", text="test")

    result = asyncio.run(
        hifly_assets.preview_my_voice_tts(
            body,
            current_user=SimpleNamespace(id=user_id),
            db=db_session,
        )
    )

    assert result["ok"] is True
    assert result["audio_url"].startswith("data:audio/mpeg;base64,")


def test_h5_background_run_refresh_is_compact_and_non_overlapping():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    load_runs = script.split("async function loadRuns(options = {})", 1)[1].split(
        "async function loadWorkflowRunsForDate", 1
    )[0]
    init = script.split("(async function init()", 1)[1]

    assert "if (state.runListLoading) return false;" in load_runs
    assert "preserveExisting" in load_runs
    assert 'compact: true' in init
    assert 'preserveExisting: true' in init
    assert '}, 15000);' in init
    assert 'loadRuns({ reset: true });\n      }, 5000);' not in init


def test_h5_run_list_requests_compactness_explicitly():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert '&compact=${compact ? "1" : "0"}' in script


def test_old_h5_run_poll_defaults_to_compact_response():
    from backend.app.api.scheduled_tasks import _run_list_uses_compact_response

    h5_request = _request()
    h5_request.scope["headers"] = [(b"referer", b"https://h5.bhzn.top/")]
    online_request = _request()

    assert _run_list_uses_compact_response(h5_request, None) is True
    assert _run_list_uses_compact_response(h5_request, False) is False
    assert _run_list_uses_compact_response(online_request, None) is False
