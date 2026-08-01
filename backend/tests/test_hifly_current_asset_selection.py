from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from starlette.requests import Request


@pytest.mark.asyncio
async def test_create_by_tts_resolves_current_avatar_and_voice(
    monkeypatch,
    db_session,
    test_user,
):
    from backend.app.api import hifly_assets
    from backend.app.models import UserHiflyAvatarAsset, UserHiflyVoiceAsset

    now = datetime.utcnow()
    rows = [
        UserHiflyAvatarAsset(
            user_id=test_user.id,
            title="旧分身",
            status="success",
            hifly_task_id="avatar-task-old",
            hifly_avatar_id="avatar-old",
            updated_at=now - timedelta(hours=2),
        ),
        UserHiflyAvatarAsset(
            user_id=test_user.id,
            title="当前分身",
            status="success",
            hifly_task_id="avatar-task-current",
            hifly_avatar_id="avatar-current",
            cover_url="https://example.test/avatar.jpg",
            updated_at=now,
        ),
        UserHiflyAvatarAsset(
            user_id=test_user.id,
            title="未完成分身",
            status="processing",
            hifly_task_id="avatar-task-processing",
            hifly_avatar_id="avatar-processing",
            updated_at=now + timedelta(hours=1),
        ),
        UserHiflyVoiceAsset(
            user_id=test_user.id,
            title="旧声音",
            status="success",
            hifly_task_id="voice-task-old",
            hifly_voice_id="voice-old",
            updated_at=now - timedelta(hours=2),
        ),
        UserHiflyVoiceAsset(
            user_id=test_user.id,
            title="当前声音",
            status="success",
            hifly_task_id="voice-task-current",
            hifly_voice_id="voice-current",
            updated_at=now,
        ),
        UserHiflyVoiceAsset(
            user_id=test_user.id,
            title="未完成声音",
            status="processing",
            hifly_task_id="voice-task-processing",
            hifly_voice_id="voice-processing",
            updated_at=now + timedelta(hours=1),
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()

    captured = {}

    async def _pre_deduct(_request, payload):
        captured["billing_payload"] = dict(payload)
        return {"credits_pre_deducted": 8, "estimated_seconds": 10, "expected_credits": 8}

    async def _post(path, token, payload):
        captured["path"] = path
        captured["token"] = token
        captured["payload"] = dict(payload)
        return {"task_id": "video-task-1", "request_id": "request-1"}

    monkeypatch.setattr(hifly_assets, "_hifly_pre_deduct_tts", _pre_deduct)
    monkeypatch.setattr(hifly_assets, "_post", _post)

    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    body = hifly_assets.HiflyVideoCreateBody(
        title="测试口播",
        avatar="current",
        voice="default",
        text="这是一段测试口播。",
        st_show=1,
    )
    result = await hifly_assets.create_my_video_by_tts(
        request=request,
        body=body,
        current_user=test_user,
        db=db_session,
    )

    assert result["ok"] is True
    assert result["task_id"] == "video-task-1"
    assert captured["path"] == "/api/v2/hifly/video/create_by_tts"
    assert captured["payload"]["avatar"] == "avatar-current"
    assert captured["payload"]["voice"] == "voice-current"
    assert result["item"]["avatar"] == "avatar-current"
    assert result["item"]["voice"] == "voice-current"
    assert result["item"]["avatar_title"] == "当前分身"
    assert result["item"]["voice_title"] == "当前声音"
