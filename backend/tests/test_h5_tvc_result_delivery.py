from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "h5_static" / "h5-app.js"


def test_h5_reads_tvc_final_video_from_nested_mcp_result():
    source = APP_JS.read_text(encoding="utf-8")

    assert "addPriority(mcpPipeline.final_video);" in source
    assert "addSaved(mcpJob.saved_assets);" in source
    assert "walk(mcpJob.saved_assets);" in source


def test_h5_never_displays_raw_tvc_result_json():
    source = APP_JS.read_text(encoding="utf-8")

    assert "function runDisplayResult(row)" in source
    assert "爆款TVC已生成，点击查看成片。" in source
    assert "const result = runDisplayResult(row);" in source
    assert "const text = runDisplayResult(run);" in source
