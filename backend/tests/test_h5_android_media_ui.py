from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_h5_includes_internal_media_preview_dialog():
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")

    assert 'id="mobileMediaPreviewDialog"' in html
    assert 'id="mobileMediaPreviewCloseBtn"' in html
    assert 'id="mobileMediaPreviewDownloadBtn"' in html
    assert "20260826-library-media-gallery-v1" in html


def test_generated_media_actions_use_preview_and_download_handlers():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert "data-media-preview-url" in script
    assert "data-media-download-url" in script
    assert "window.LobsterAndroid.downloadFile" in script
    assert "window.LobsterAndroid.saveMediaToGallery" in script
    assert "data-media-download-kind" in script
    assert "download.dataset.mediaDownloadKind = kind" in script
    assert 'IS_ANDROID_APP && ["image", "video"].includes(mediaKind) ? "保存到相册"' in script
    assert "window.__lobsterHandleBack" in script
    assert 'open.target = "_blank"' not in script


def test_media_preview_is_full_screen_and_preserves_media_aspect_ratio():
    styles = (ROOT / "h5_static" / "h5-app.css").read_text(encoding="utf-8")

    assert ".mobile-media-preview-panel" in styles
    assert "object-fit: contain" in styles
    assert ".mobile-media-preview-back" in styles
    assert "grid-template-columns: minmax(0, 1fr)" in styles


def test_library_video_cards_and_avatar_details_have_visual_previews():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert "function assetThumbnailUrl(asset)" in script
    assert "function videoFirstFrameUrl(url)" in script
    assert "function videoThumbnailSource(source)" in script
    assert "#t=0.1" in script
    assert 'muted playsinline preload="metadata"' in script
    assert "posterUrl = assetThumbnailUrl(asset)" in script
    assert 'mediaType === "video"' in script
    assert '<video class="asset-preview-large"' in script
