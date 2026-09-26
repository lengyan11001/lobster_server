from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
H5_APP = ROOT / "h5_static" / "h5-app.js"


def _src() -> str:
    return H5_APP.read_text(encoding="utf-8")


def test_h5_run_list_reloads_once_auth_becomes_ready():
    src = _src()
    assert "state.runsPendingReload = true;" in src
    assert "flushPendingRunListReload();" in src
    assert "refreshDeviceStatus().catch(() => {});" in src
    assert "state.officeSummaryRetried" in src
    assert "function flushPendingRunListReload()" in src


def test_h5_run_list_is_not_refreshed_on_a_timer():
    src = _src()
    assert "}, 15000);" not in src
    assert "preserveExisting: true,\n          silent: true,\n        });\n      }, 15000);" not in src


def test_h5_office_summary_does_not_cache_failed_loads():
    src = _src()
    assert "if (ok === true) state.officeSummaryLoadedAt = Date.now();" in src


def test_h5_resume_pulls_the_run_list_again():
    src = _src()
    assert "flushPendingRunListReload" in src
    assert '["office", "runList", "workList", "workflow", "department", "secretary"].includes(resumeView)' in src