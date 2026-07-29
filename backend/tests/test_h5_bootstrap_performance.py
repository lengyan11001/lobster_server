from pathlib import Path


def test_h5_bootstrap_reuses_office_summary_request():
    js = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    start = js.index("async function loadMe()")
    end = js.index("function renderProfileDeviceSelect()", start)
    load_me = js[start:end]

    assert "refreshOfficeSummary()" in load_me
    assert "loadHistory({ includeEvents: false })" in load_me
    assert "loadTasks({ reset: true })" not in load_me
    assert "loadRuns({ reset: true, limit: 20, compact: true })" not in load_me


def test_h5_message_history_defers_event_payloads_until_messages_view():
    js = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert 'include_events=${includeEvents ? "true" : "false"}' in js
    assert 'loadHistory({ includeEvents: true }).catch(() => {});' in js
