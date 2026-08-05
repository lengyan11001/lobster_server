from backend.app.api.h5_recorder import _segments
from backend.app.h5_main import app as h5_app


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
