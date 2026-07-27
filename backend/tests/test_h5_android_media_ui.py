from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_h5_includes_internal_media_preview_dialog():
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")

    assert 'id="mobileMediaPreviewDialog"' in html
    assert 'id="mobileMediaPreviewCloseBtn"' in html
    assert 'id="mobileMediaPreviewDownloadBtn"' in html
    assert "20260727-android-media" in html


def test_generated_media_actions_use_preview_and_download_handlers():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert "data-media-preview-url" in script
    assert "data-media-download-url" in script
    assert "window.LobsterAndroid.downloadFile" in script
    assert "window.__lobsterHandleBack" in script
    assert 'open.target = "_blank"' not in script


def test_media_preview_is_full_screen_and_preserves_media_aspect_ratio():
    styles = (ROOT / "h5_static" / "h5-app.css").read_text(encoding="utf-8")

    assert ".mobile-media-preview-panel" in styles
    assert "object-fit: contain" in styles
    assert ".mobile-media-preview-back" in styles
