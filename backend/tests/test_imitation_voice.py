from backend.app.api.douyin_platform_information_desk import ImitationIn, _task_payload
from backend.app.models import DouyinImitationTask
from backend.app.services.douyin_imitation_video import build_voice_mix_filter


def test_voice_filter_speeds_audio_to_video_duration():
    short = build_voice_mix_filter(10, 5)
    assert "atempo=0.500000" in short
    assert "apad" in short
    assert "atrim=0:10.000" in short

    long = build_voice_mix_filter(10, 20)
    assert "atempo=2.000000" in long
    assert "atrim=0:10.000" in long

    chained = build_voice_mix_filter(10, 30)
    assert "atempo=2.0" in chained
    assert "atempo=1.500000" in chained

    unknown = build_voice_mix_filter(10, 0)
    assert "apad" in unknown
    assert "atrim=0:10.000" in unknown


def test_imitation_payload_keeps_voice_fields():
    row = DouyinImitationTask(
        user_id=1,
        task_id="task-1",
        item_id="",
        title="跟创",
        voice_mode="clone",
        voice_id="male-qn-qingse",
        voice_source="system",
        voice_applied=True,
    )
    payload = _task_payload(row)
    assert payload["voice_mode"] == "clone"
    assert payload["voice_id"] == "male-qn-qingse"
    assert payload["voice_source"] == "system"
    assert payload["voice_applied"] is True


def test_imitation_in_defaults_to_original_voice():
    body = ImitationIn()
    assert body.voice_mode == "original"
    assert body.voice_id == ""
    assert body.voice_source == ""


import pytest


@pytest.mark.asyncio
async def test_prepare_voice_audio_system_voice(db_session, test_user, monkeypatch):
    from backend.app.services import douyin_imitation_video as svc
    from backend.app.services import device_tts
    from backend.app.api import wechat_channels_transcript

    monkeypatch.setattr(
        "backend.app.api.cutcli_templates._load_sutui_token_for_stt",
        lambda db, uid: ("stt-token", "test"),
    )
    monkeypatch.setattr(
        wechat_channels_transcript,
        "_process_one_video",
        lambda **kwargs: {"transcript": "原视频文案"},
    )
    monkeypatch.setattr(device_tts, "synthesize", lambda text, token, voice_id: "https://example.test/tts.mp3")
    async def fake_download(url, limit=None):
        return b"audio-bytes", ""
    async def fake_upload(data, suffix, content_type):
        return "https://tos.example.test/voice.mp3", ""
    monkeypatch.setattr(svc, "_download", fake_download)
    monkeypatch.setattr(svc, "_upload_tos", fake_upload)

    result = await svc.prepare_voice_audio_for_video(
        "https://tos.example.test/video.mp4",
        voice_id="male-qn-qingse",
        voice_source="system",
        db=db_session,
        user_id=test_user.id,
    )
    assert result["ok"] is True
    assert result["text"] == "原视频文案"
    assert result["audio_url"] == "https://tos.example.test/voice.mp3"


@pytest.mark.asyncio
async def test_prepare_voice_audio_mine_voice(db_session, test_user, monkeypatch):
    from backend.app.services import douyin_imitation_video as svc
    from backend.app.api import hifly_assets, wechat_channels_transcript
    from backend.app.models import UserHiflyVoiceAsset

    db_session.add(UserHiflyVoiceAsset(
        user_id=test_user.id,
        title="我的克隆",
        status="success",
        hifly_task_id="voice-task-voice-prep",
        hifly_voice_id="minimax_clone_1",
        meta={"provider": "minimax"},
    ))
    db_session.commit()

    monkeypatch.setattr(
        "backend.app.api.cutcli_templates._load_sutui_token_for_stt",
        lambda db, uid: ("stt-token", "test"),
    )
    monkeypatch.setattr(
        wechat_channels_transcript,
        "_process_one_video",
        lambda **kwargs: {"transcript": "我的声音文案"},
    )
    async def fake_preview_tts_audio(**kwargs):
        return {"audio_bytes": b"mine-audio", "duration_seconds": 3}
    async def fake_upload(data, suffix, content_type):
        return "https://tos.example.test/mine.mp3", ""
    monkeypatch.setattr(hifly_assets, "_preview_tts_audio", fake_preview_tts_audio)
    monkeypatch.setattr(svc, "_upload_tos", fake_upload)

    result = await svc.prepare_voice_audio_for_video(
        "https://tos.example.test/video.mp4",
        voice_id="minimax_clone_1",
        voice_source="mine",
        db=db_session,
        user_id=test_user.id,
    )
    assert result["ok"] is True
    assert result["text"] == "我的声音文案"
    assert result["audio_url"] == "https://tos.example.test/mine.mp3"


class _FakeResponse:
    def __init__(self, status_code, payload):
        import json
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        self.calls.append(json or {})
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_submit_imitation_sends_reference_audio(monkeypatch):
    import backend.app.services.douyin_imitation_video as svc

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    fake = _FakeAsyncClient([_FakeResponse(200, {"output": {"task_id": "t1"}})])
    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda **kwargs: fake)

    result = await svc.submit_imitation(
        "https://img.example.test/a.png",
        "https://video.example.test/a.mp4",
        reference_audio_url="https://audio.example.test/a.mp3",
    )
    assert result["ok"] is True
    media = fake.calls[0]["input"]["media"]
    assert {"type": "reference_audio", "url": "https://audio.example.test/a.mp3"} in media


@pytest.mark.asyncio
async def test_submit_imitation_retries_without_reference_audio(monkeypatch):
    import backend.app.services.douyin_imitation_video as svc

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    fake = _FakeAsyncClient([
        _FakeResponse(400, {"code": "InvalidParameter", "message": "reference_audio invalid"}),
        _FakeResponse(200, {"output": {"task_id": "t2"}}),
    ])
    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda **kwargs: fake)

    result = await svc.submit_imitation(
        "https://img.example.test/a.png",
        "https://video.example.test/a.mp4",
        reference_audio_url="https://audio.example.test/a.mp3",
    )
    assert result["ok"] is True
    assert len(fake.calls) == 2
    assert all(m.get("type") != "reference_audio" for m in fake.calls[1]["input"]["media"])
