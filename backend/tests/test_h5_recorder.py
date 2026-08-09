import asyncio
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from backend.app.api import h5_recorder
from backend.app.api.h5_recorder import (
    RecorderSpeakerRenameBody,
    _merge_stt_parts,
    _memory_audio_sources,
    _recorded_at,
    _recorder_tos_object_key,
    _run_stt,
    _extract_stt_output,
    _segments,
    _stt_chunk_plan,
    _transcribe_stt_range,
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


def test_recorder_segments_reads_gateway_nested_speaker_and_keeps_ids():
    rows = _segments({
        "utterances": [
            {"additions": {"speaker": "1"}, "text": "甲"},
            {"additions": {"speaker": "2"}, "text": "乙"},
            {"additions": {"speaker": "1"}, "text": "丙"},
        ]
    })

    assert [row["speaker"] for row in rows] == ["A", "B", "A"]
    assert [row["speaker_id"] for row in rows] == ["1", "2", "1"]


def test_recorder_chunk_merge_keeps_speaker_labels_across_chunk_boundaries():
    _text, rows = _merge_stt_parts([
        (0.0, {"utterances": [{"additions": {"speaker": "2"}, "text": "乙"}, {"additions": {"speaker": "1"}, "text": "甲"}]}),
        (600.0, {"utterances": [{"additions": {"speaker": "1"}, "text": "甲二"}, {"additions": {"speaker": "2"}, "text": "乙二"}]}),
    ])

    assert [row["speaker"] for row in rows] == ["A", "B", "B", "A"]
    assert [row["speaker_id"] for row in rows] == ["2", "1", "1", "2"]


def test_recorder_segments_do_not_invent_unknown_speaker():
    rows = _segments({"utterances": [{"text": "只有一段文本"}]})
    assert rows[0]["speaker"] == "未知"


def test_stt_output_decodes_nested_json_string():
    nested = json.dumps({"text": "decoded transcript"}, ensure_ascii=False)

    output = _extract_stt_output({"data": {"output": nested}})

    assert output["text"] == "decoded transcript"


def test_stt_output_recovers_text_from_truncated_json_string():
    truncated = '{"text": "partial transcript'

    output = _extract_stt_output({"data": {"result": truncated}})

    assert output["text"] == "partial transcript"
    assert output["_stt_partial"] is True


def test_long_audio_is_split_into_bounded_stt_chunks():
    assert _stt_chunk_plan(1300, chunk_seconds=600) == [
        (0.0, 600.0),
        (600.0, 600.0),
        (1200.0, 100.0),
    ]


def test_recorder_stt_default_chunk_size_is_two_minutes():
    assert h5_recorder.DEFAULT_STT_CHUNK_SECONDS == 120


def test_stt_chunk_merge_restores_original_timeline_order():
    text, segments = _merge_stt_parts([
        (600.0, {
            "text": "second chunk",
            "utterances": [{"speaker_id": "speaker-1", "text": "second", "start_time": 100, "end_time": 500}],
        }),
        (0.0, {
            "text": "first chunk",
            "utterances": [{"speaker_id": "speaker-1", "text": "first", "start_time": 50, "end_time": 300}],
        }),
    ])

    assert text == "first chunk\nsecond chunk"
    assert [item["text"] for item in segments] == ["first", "second"]
    assert [item["start_ms"] for item in segments] == [50, 600100]


def test_truncated_stt_chunk_is_retried_as_smaller_ranges(monkeypatch, tmp_path):
    poll_count = 0

    def fake_extract(**kwargs):
        kwargs["out_path"].write_bytes(b"x" * 256)

    def fake_create(_token, _audio_url, *, job_dir, enable_speaker_info):
        assert enable_speaker_info is True
        return {"task_id": f"task-{job_dir.name}"}

    def fake_poll(_token, _task_id, *, job_dir, timeout_seconds):
        nonlocal poll_count
        assert timeout_seconds == 1800
        poll_count += 1
        if poll_count == 1:
            return {"status": "completed", "output": '{"text": "truncated'}
        return {"status": "completed", "output": {"text": f"part-{poll_count}"}}

    monkeypatch.setattr(h5_recorder, "STT_MIN_SPLIT_SECONDS", 90)
    monkeypatch.setattr(h5_recorder, "_extract_audio_wav_chunk", fake_extract)
    monkeypatch.setattr(h5_recorder, "_upload_job_file_to_tos", lambda *_args, **_kwargs: "https://audio.test/chunk.wav")
    monkeypatch.setattr(h5_recorder, "_stt_create_task", fake_create)
    monkeypatch.setattr(h5_recorder, "_stt_poll_task", fake_poll)
    source = tmp_path / "source.mp3"
    source.write_bytes(b"source")

    parts, task_ids = _transcribe_stt_range(
        token="test-token",
        ffmpeg="ffmpeg",
        source=source,
        job_dir=tmp_path / "stt",
        user_id=1,
        record_id=2,
        start_seconds=0,
        duration_seconds=180,
    )

    assert [start for start, _output in parts] == [0, 90]
    assert [output["text"] for _start, output in parts] == ["part-2", "part-3"]
    assert len(task_ids) == 3
    assert poll_count == 3


def test_run_stt_merges_all_chunks_and_persists_batch_progress(
    monkeypatch,
    tmp_path,
    db_session_factory,
    test_user,
):
    source = tmp_path / "long-audio.mp3"
    source.write_bytes(b"source")
    with db_session_factory() as db:
        row = RecorderAudioRecord(
            user_id=test_user.id,
            file_name=source.name,
            display_name=source.name,
            device_name="local",
            source_type="local",
            file_size=source.stat().st_size,
            audio_path=str(source),
            status="processing",
            process_stage="uploaded",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        record_id = row.id

    observed: list[tuple[float, float]] = []

    def fake_transcribe(**kwargs):
        start = float(kwargs["start_seconds"])
        duration = float(kwargs["duration_seconds"])
        observed.append((start, duration))
        part_number = int(start // 600) + 1
        return [(
            start,
            {
                "text": f"chunk-{part_number}",
                "utterances": [{"speaker_id": "speaker-1", "text": f"line-{part_number}", "start_time": 0, "end_time": 100}],
            },
        )], [f"task-{part_number}"]

    monkeypatch.setattr(h5_recorder, "SessionLocal", db_session_factory)
    monkeypatch.setattr(h5_recorder, "STT_CHUNK_SECONDS", 600)
    monkeypatch.setattr(h5_recorder, "_find_ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(h5_recorder, "_audio_duration_seconds", lambda _source: 1300)
    monkeypatch.setattr(h5_recorder, "_load_sutui_token_for_stt", lambda _db, _user_id: ("token", "test"))
    monkeypatch.setattr(h5_recorder, "_transcribe_stt_range", fake_transcribe)

    text, segments, task_reference = _run_stt(record_id)

    assert sorted(observed) == [(0.0, 600.0), (600.0, 600.0), (1200.0, 100.0)]
    assert text == "chunk-1\nchunk-2\nchunk-3"
    assert [item["start_ms"] for item in segments] == [0, 600000, 1200000]
    assert task_reference.startswith("batch:3:")
    with db_session_factory() as db:
        saved = db.query(RecorderAudioRecord).filter(RecorderAudioRecord.id == record_id).one()
        assert saved.process_stage == "transcribing:3/3"
        assert saved.stt_task_id == task_reference


def test_run_stt_limits_parallel_chunks_and_merges_by_timeline(
    monkeypatch,
    tmp_path,
    db_session_factory,
    test_user,
):
    source = tmp_path / "parallel-audio.mp3"
    source.write_bytes(b"source")
    with db_session_factory() as db:
        row = RecorderAudioRecord(
            user_id=test_user.id,
            file_name=source.name,
            display_name=source.name,
            device_name="local",
            source_type="local",
            file_size=source.stat().st_size,
            audio_path=str(source),
            status="processing",
            process_stage="uploaded",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        record_id = row.id

    lock = threading.Lock()
    three_workers_started = threading.Event()
    active = 0
    max_active = 0
    completion_order: list[int] = []

    def fake_transcribe(**kwargs):
        nonlocal active, max_active
        start = float(kwargs["start_seconds"])
        part_index = int(start // 120)
        with lock:
            active += 1
            max_active = max(max_active, active)
            if active == 3:
                three_workers_started.set()
        assert three_workers_started.wait(timeout=2)
        time.sleep({0: 0.06, 1: 0.04, 2: 0.01}.get(part_index, 0.005))
        with lock:
            active -= 1
            completion_order.append(part_index)
        return [(
            start,
            {
                "text": f"chunk-{part_index + 1}",
                "utterances": [{
                    "speaker_id": "speaker-1",
                    "text": f"line-{part_index + 1}",
                    "start_time": 0,
                    "end_time": 100,
                }],
            },
        )], [f"task-{part_index + 1}"]

    monkeypatch.setattr(h5_recorder, "SessionLocal", db_session_factory)
    monkeypatch.setattr(h5_recorder, "STT_CHUNK_SECONDS", 120)
    monkeypatch.setattr(h5_recorder, "STT_MAX_PARALLEL_CHUNKS", 3)
    monkeypatch.setattr(h5_recorder, "_STT_GLOBAL_CHUNK_GATE", threading.BoundedSemaphore(8))
    monkeypatch.setattr(h5_recorder, "_find_ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(h5_recorder, "_audio_duration_seconds", lambda _source: 480)
    monkeypatch.setattr(h5_recorder, "_load_sutui_token_for_stt", lambda _db, _user_id: ("token", "test"))
    monkeypatch.setattr(h5_recorder, "_transcribe_stt_range", fake_transcribe)

    text, segments, _task_reference = _run_stt(record_id)

    assert max_active == 3
    assert completion_order[0] != 0
    assert text == "chunk-1\nchunk-2\nchunk-3\nchunk-4"
    assert [item["start_ms"] for item in segments] == [0, 120000, 240000, 360000]


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


def test_local_audio_upload_reports_progress_and_can_escape_a_stall():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert "async function uploadFormDataWithProgress" in script
    assert "xhr.upload.onprogress" in script
    assert "上传长时间没有进度，请检查网络后重试" in script
    assert 'uploadFormDataWithProgress("/api/h5/recorder/files"' in script
    assert 'blockingFetch(apiUrl("/api/h5/recorder/files")' not in script
    assert 'uploaded ? "正在创建记录..." : `上传 ${percent}%`' in script


def test_local_audio_upload_persists_before_background_transcription(
    monkeypatch,
    tmp_path,
    db_session,
    test_user,
):
    process_calls: list[int] = []

    async def fake_process(record_id, _request, _installation_id):
        process_calls.append(record_id)

    class FakeUpload:
        filename = "meeting.mp3"
        content_type = "audio/mpeg"

        def __init__(self):
            self.payload = b"local-audio"
            self.offset = 0

        async def read(self, size):
            chunk = self.payload[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk

    monkeypatch.setattr(h5_recorder, "DATA_ROOT", tmp_path / "recorder-audio")
    monkeypatch.setattr(h5_recorder, "_process", fake_process)
    background = BackgroundTasks()
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/h5/recorder/files",
        "headers": [],
    })

    result = asyncio.run(h5_recorder.upload_recording(
        request=request,
        background=background,
        file=FakeUpload(),
        device_name="",
        source_type="local",
        source_name="本地音频",
        installation_id="",
        current_user=test_user,
        db=db_session,
    ))

    row = db_session.query(RecorderAudioRecord).filter_by(id=result["record"]["id"]).one()
    assert result["ok"] is True
    assert row.status == "processing"
    assert row.process_stage == "uploaded"
    assert Path(row.audio_path).read_bytes() == b"local-audio"
    assert process_calls == []
    assert len(background.tasks) == 1
    assert background.tasks[0].func is fake_process
    assert background.tasks[0].args[0] == row.id


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


def test_speaker_rename_prefers_stable_speaker_id_over_duplicate_display_names(db_session, test_user):
    row = _recorder_record(test_user.id)
    row.transcript_segments = [
        {"speaker": "张老师", "speaker_id": "1", "text": "甲"},
        {"speaker": "张老师", "speaker_id": "2", "text": "乙"},
    ]
    db_session.add(row)
    db_session.commit()

    result = rename_recording_speaker(
        row.id,
        RecorderSpeakerRenameBody(speaker="张老师", speaker_id="1", display_name="甲方"),
        current_user=test_user,
        db=db_session,
    )

    assert result["updated_segments"] == 1
    assert [item["speaker"] for item in result["record"]["segments"]] == ["甲方", "张老师"]


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
    assert "data-recorder-speaker-id" in script
    assert "speaker_id" in script


def test_h5_api_hides_gateway_html_and_retries_transient_gets():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    assert "function readableApiError" in script
    assert "服务器暂时不可用，请稍后重试" in script
    assert "transientStatuses.has(resp.status)" in script
