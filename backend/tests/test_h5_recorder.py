import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from backend.app.api import h5_recorder
from backend.app.api.h5_recorder import (
    RecorderSpeakerRenameBody,
    _memory_audio_sources,
    _recorded_at,
    _recorder_tos_object_key,
    _segments,
    list_recordings,
    list_memory_audio_files,
    rename_recording_speaker,
    transcribe_memory_audio_file,
)
from backend.app.api.h5_personal_settings import _recorder_records_source_text
from backend.app.h5_main import app as h5_app
from backend.app.models import OpenClawMemoryDocument, RecorderAudioRecord


ROOT = Path(__file__).resolve().parents[2]


def test_recorder_segments_map_speakers_in_first_seen_order():
    output = {
        "utterances": [
            {"speaker_id": "speaker-9", "text": "先确认需求", "start_time": 0, "end_time": 900},
            {"speaker_id": "speaker-2", "text": "好的", "start_time": 950, "end_time": 1300},
            {"speaker_id": "speaker-9", "text": "明天交付", "start_time": 1400, "end_time": 2100},
        ]
    }
    rows = _segments(output)
    assert [row["speaker"] for row in rows] == ["A", "B", "A"]
    assert [row["text"] for row in rows] == ["先确认需求", "好的", "明天交付"]


def test_recorder_segments_do_not_invent_unknown_speaker():
    rows = _segments({"utterances": [{"text": "只有一段文本"}]})
    assert rows[0]["speaker"] == "未知"


def test_h5_recorder_routes_are_available_on_standalone_h5_app():
    routes = {(route.path, method) for route in h5_app.routes for method in (getattr(route, "methods", None) or set())}
    assert ("/api/h5/recorder/files", "GET") in routes
    assert ("/api/h5/recorder/files", "POST") in routes
    assert ("/api/h5/recorder/files/{record_id}", "DELETE") in routes
    assert ("/api/h5/recorder/files/{record_id}", "PATCH") in routes
    assert ("/api/h5/recorder/files/{record_id}/speakers", "PATCH") in routes
    assert ("/api/h5/recorder/files/{record_id}/audio", "GET") in routes
    assert ("/api/h5/recorder/files/{record_id}/retry", "POST") in routes
    assert ("/api/h5/recorder/files/{record_id}/memory", "POST") in routes
    assert ("/api/h5/recorder/memory-files", "GET") in routes
    assert ("/api/h5/recorder/memory-files/{doc_id}/transcribe", "POST") in routes
    assert ("/api/h5/recorder/known-names", "GET") in routes


def test_recorder_timestamp_is_read_from_device_file_name():
    value = _recorded_at("20260806054347.opus")
    assert value is not None
    assert value.isoformat() == "2026-08-06T05:43:47"
    assert _recorded_at("recording.opus") is None


def test_recorder_audio_uses_tos_allowed_assets_prefix():
    assert _recorder_tos_object_key(31, 7) == "assets/recorder/31/7.wav"


def test_recorder_device_refresh_does_not_start_batch_audio_sync():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    refresh_body = script.split("async function refreshLatestRecorderFiles()", 1)[1].split(
        'window.addEventListener("lobster-recorder"', 1
    )[0]
    assert "native.fetchRecorderFiles();" in refresh_body
    assert "native.syncNewRecorderFiles" not in refresh_body
    assert 'data-recorder-download="${escapeHtml(row.fileName || "")}"' in script
    assert "loadRecorderKnownNames" in script
    assert 'row.source_type || "device"' in script


def test_recorder_page_explains_manual_per_file_sync():
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    assert 'id="recorderRefreshBtn" disabled>刷新列表</button>' in html
    assert "刷新只读取目录，选择未同步录音后再上传" in html


def test_recorder_page_treats_device_as_one_of_three_audio_sources():
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    assert 'data-recorder-tab="local"' in html
    assert 'data-recorder-tab="memory"' in html
    assert 'data-recorder-tab="device"' in html
    assert 'id="recorderLocalFileInput"' in html
    assert 'id="recorderMemoryFiles"' in html
    assert 'recorderSubtab: "local"' in script


def test_personal_ip_audio_files_have_per_file_transcription_and_faq_action():
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    assert 'id="recorderFaqBtn"' in html
    assert "data-transcribe-personal-upload" in script
    assert 'input.value === "product_service_faq"' in script
    assert 'id="personalMemoryRecorderSourceList"' in html
    assert 'fd.append("recorder_record_ids"' in script


def _recorder_record(user_id: int, *, status: str = "completed") -> RecorderAudioRecord:
    now = datetime.utcnow()
    return RecorderAudioRecord(
        user_id=user_id,
        file_name="meeting.wav",
        display_name="客户会议",
        device_name="",
        source_type="local",
        file_size=100,
        status=status,
        process_stage="completed" if status == "completed" else "transcribing",
        audio_path="meeting.wav",
        transcript_text="先确认需求。明天交付。",
        transcript_segments=[
            {"speaker": "A", "text": "先确认需求"},
            {"speaker": "B", "text": "好的"},
            {"speaker": "A", "text": "明天交付"},
        ],
        summary_text="客户确认明天交付",
        key_points=["明天交付"],
        error_message="",
        stt_task_id="",
        created_at=now,
        updated_at=now,
    )


def test_recorder_source_text_checks_owner_and_includes_summary(db_session, test_user, other_user):
    own = _recorder_record(test_user.id)
    private = _recorder_record(other_user.id)
    db_session.add_all([own, private])
    db_session.commit()

    text = _recorder_records_source_text(db_session, test_user.id, str(own.id))

    assert "客户会议" in text
    assert "客户确认明天交付" in text
    assert "A：先确认需求" in text
    with pytest.raises(HTTPException) as exc:
        _recorder_records_source_text(db_session, test_user.id, str(private.id))
    assert exc.value.status_code == 404


def test_recorder_source_text_rejects_unfinished_record(db_session, test_user):
    row = _recorder_record(test_user.id, status="processing")
    db_session.add(row)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        _recorder_records_source_text(db_session, test_user.id, str(row.id))

    assert exc.value.status_code == 409


def test_speaker_rename_updates_all_matching_segments_only(db_session, test_user):
    row = _recorder_record(test_user.id)
    db_session.add(row)
    db_session.commit()

    result = rename_recording_speaker(
        row.id,
        RecorderSpeakerRenameBody(speaker="A", display_name="何总"),
        current_user=test_user,
        db=db_session,
    )

    assert result["updated_segments"] == 2
    assert [item["speaker"] for item in result["record"]["segments"]] == ["何总", "B", "何总"]


def test_speaker_rename_rejects_cross_user_record(db_session, test_user, other_user):
    row = _recorder_record(other_user.id)
    db_session.add(row)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        rename_recording_speaker(
            row.id,
            RecorderSpeakerRenameBody(speaker="A", display_name="何总"),
            current_user=test_user,
            db=db_session,
        )

    assert exc.value.status_code == 404


def test_recording_list_can_filter_mobile_device_sources(db_session, test_user):
    local = _recorder_record(test_user.id)
    device = _recorder_record(test_user.id)
    device.source_type = "device"
    device.file_name = "20260806120000.opus"
    db_session.add_all([local, device])
    db_session.commit()

    result = list_recordings(page=1, page_size=20, source_type="device", current_user=test_user, db=db_session)

    assert result["total"] == 1
    assert result["items"][0]["source_type"] == "device"


def _request(installation_id: str = "install-test-01") -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-installation-id", installation_id.encode("ascii"))],
    })


def _memory_doc(user_id: int, doc_id: str, *, source_url: str = "https://example.test/audio.wav") -> OpenClawMemoryDocument:
    now = datetime.utcnow()
    return OpenClawMemoryDocument(
        doc_id=doc_id,
        target_user_id=user_id,
        installation_id="install-test-01",
        origin="user",
        title="客户访谈",
        filename="客户访谈.txt",
        content_text="已有转写",
        status="active",
        meta={"source_audio_files": [{"filename": "访谈.wav", "source_url": source_url, "file_size": 1200}]},
        created_at=now,
        updated_at=now,
    )


def test_memory_audio_sources_are_scoped_to_current_user(db_session, test_user, other_user):
    db_session.add_all([_memory_doc(test_user.id, "own-audio"), _memory_doc(other_user.id, "other-audio")])
    db_session.commit()

    result = list_memory_audio_files(_request(), current_user=test_user, db=db_session)

    assert [item["doc_id"] for item in result["items"]] == ["own-audio"]
    assert result["items"][0]["filename"] == "访谈.wav"


def test_memory_audio_transcription_creates_owned_source_record(monkeypatch, tmp_path, db_session, test_user):
    row = _memory_doc(test_user.id, "audio-for-stt")
    db_session.add(row)
    db_session.commit()

    async def fake_download(_url: str, *, max_bytes: int) -> bytes:
        assert max_bytes == h5_recorder.MAX_AUDIO_BYTES
        return b"audio-data"

    monkeypatch.setattr(h5_recorder, "_download_media_url", fake_download)
    monkeypatch.setattr(h5_recorder, "DATA_ROOT", tmp_path)
    result = asyncio.run(transcribe_memory_audio_file(
        "audio-for-stt",
        _request(),
        BackgroundTasks(),
        source_index=0,
        current_user=test_user,
        db=db_session,
    ))

    saved = db_session.query(RecorderAudioRecord).filter(RecorderAudioRecord.id == result["record"]["id"]).one()
    assert saved.user_id == test_user.id
    assert saved.source_type == "memory"
    assert saved.source_doc_id == "audio-for-stt:0"
    assert Path(saved.audio_path).read_bytes() == b"audio-data"


def test_memory_audio_transcription_rejects_cross_user_doc(db_session, test_user, other_user):
    db_session.add(_memory_doc(other_user.id, "private-audio"))
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(transcribe_memory_audio_file(
            "private-audio",
            _request(),
            BackgroundTasks(),
            source_index=0,
            current_user=test_user,
            db=db_session,
        ))

    assert exc.value.status_code == 404


def test_memory_audio_source_parser_ignores_plain_text_memory():
    row = _memory_doc(1, "plain-memory")
    row.meta = {"uploaded": True}
    assert _memory_audio_sources(row) == []


def test_recorder_detail_uses_a_separate_view_with_result_tabs():
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    assert 'id="recorderDetailView"' in html
    assert 'data-recorder-detail-tab="summary"' in html
    assert 'data-recorder-detail-tab="transcript"' in html
    assert 'id="recorderAudio"' in html
    assert 'switchTab("recorderDetail")' in script
    assert 'data-recorder-copy="summary"' in html
    assert 'data-recorder-export="transcript"' in html
    assert "renameRecorderSpeaker" in script


def test_h5_api_hides_gateway_html_and_retries_transient_gets():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    assert "function readableApiError" in script
    assert "服务器暂时不可用，请稍后重试" in script
    assert "transientStatuses.has(resp.status)" in script
