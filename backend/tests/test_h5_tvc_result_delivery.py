from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "h5_static" / "h5-app.js"


def test_h5_reads_tvc_final_video_from_nested_mcp_result():
    source = APP_JS.read_text(encoding="utf-8")

    assert "addPriority(mcpPipeline.final_video);" in source
    assert "addSaved(mcpJob.saved_assets);" in source
    assert "mcpPipeline.final_video," in source


def test_h5_run_detail_shows_media_before_text_result_when_media_exists():
    source = APP_JS.read_text(encoding="utf-8")

    assert "let hasMediaResult = false;" in source
    assert "hasMediaResult = true;" in source
    media_first = "primarySections.push(...sections);\n        primarySections.push(resultSummaryHtml);"
    text_first = "primarySections.push(resultSummaryHtml);\n        primarySections.push(...sections);"
    assert media_first in source
    assert text_first in source


def test_h5_run_media_stops_at_primary_final_video_before_walking_intermediates():
    source = APP_JS.read_text(encoding="utf-8")

    assert "const primaryVideoEntries = () => out.filter" in source
    short_circuit = "if (primaryVideos.length) return primaryVideos.slice(0, 1);"
    assert short_circuit in source
    assert source.index(short_circuit) < source.index("walk(payload.saved_assets);")


def test_h5_run_media_actions_are_grouped_in_one_toolbar():
    source = APP_JS.read_text(encoding="utf-8")

    render_block = source.split("function renderRunMedia", 1)[1].split("function renderRunPublishActions", 1)[0]
    assert "function runMediaToolbarHtml" in source
    assert "run-media-toolbar" in render_block
    assert 'runMediaToolbarHtml(url, "下载视频", "lobster-video.mp4", actionMenu)' in render_block
    assert 'mediaActionHtml(url, "下载视频", "lobster-video.mp4")' not in render_block


def test_h5_run_media_reads_local_image_generation_outputs():
    source = APP_JS.read_text(encoding="utf-8")
    collect_block = source.split("function collectRunMediaEntries", 1)[1].split("function activeChatSession", 1)[0]

    for marker in (
        "const localRefs = local.result_refs",
        "walk(local.images);",
        "walk(local.media_urls);",
        "walk(localRefs.urls);",
        "walk(localRefs.saved_assets);",
        "walk(result.images);",
        "walk(payload.image_urls);",
    ):
        assert marker in collect_block


def test_h5_never_displays_raw_tvc_result_json():
    source = APP_JS.read_text(encoding="utf-8")

    assert "function runDisplayResult(row)" in source
    assert "爆款TVC已生成，点击查看成片。" in source
    assert "const result = runDisplayResult(row);" in source
    assert "const text = runDisplayResult(run);" in source
