from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
H5_APP = ROOT / "h5_static" / "h5-app.js"


def _src() -> str:
    return H5_APP.read_text(encoding="utf-8")


def test_run_detail_video_prefers_direct_url_and_keeps_proxy_fallback():
    src = _src()
    assert 'libraryMediaSourceHtml(url, filenameFromUrl(url, "lobster-video.mp4"))' in src
    assert 'mediaProxyUrl(url, "inline", filenameFromUrl(url, "lobster-video.mp4"))' not in src
    assert "function libraryMediaSourceHtml(" in src
    assert "data-library-media-fallback" in src


def test_run_detail_audio_prefers_direct_url():
    src = _src()
    assert 'libraryMediaSourceHtml(url, filenameFromUrl(url, "lobster-audio.mp3"))' in src
    assert 'mediaProxyUrl(url, "inline", filenameFromUrl(url, "lobster-audio.mp3"))' not in src


def test_run_media_toolbar_open_action_uses_direct_url_for_playback():
    src = _src()
    assert '["video", "audio"].includes(mediaKind) ? openSource.src : mediaProxyUrl(url, "inline", filename)' in src


def test_run_detail_video_has_single_controls_attribute():
    src = _src()
    assert "<video controls controls" not in src
    assert '<video controls playsinline preload="metadata"' in src