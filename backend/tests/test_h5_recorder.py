from pathlib import Path

from backend.app.api.h5_recorder import _recorded_at, _recorder_tos_object_key, _segments
from backend.app.h5_main import app as h5_app


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
    assert ("/api/h5/recorder/files/{record_id}/audio", "GET") in routes
    assert ("/api/h5/recorder/files/{record_id}/retry", "POST") in routes
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


def test_recorder_page_explains_manual_per_file_sync():
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    assert 'id="recorderRefreshBtn" disabled>刷新列表</button>' in html
    assert "刷新只读取目录，选择未同步录音后再上传" in html


def test_recorder_detail_uses_a_separate_view_with_result_tabs():
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    assert 'id="recorderDetailView"' in html
    assert 'data-recorder-detail-tab="summary"' in html
    assert 'data-recorder-detail-tab="transcript"' in html
    assert 'id="recorderAudio"' in html
    assert 'switchTab("recorderDetail")' in script


def test_h5_api_hides_gateway_html_and_retries_transient_gets():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    assert "function readableApiError" in script
    assert "服务器暂时不可用，请稍后重试" in script
    assert "transientStatuses.has(resp.status)" in script
