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

def test_large_preview_player_prefers_direct_url_and_keeps_proxy_fallback():
    """2026-10-16：内容记录/素材大预览必须「公开 https 直链优先」。

    以前 assetPreviewLargeHtml 一律走 /api/h5-chat/media 同源代理，服务端那层 SSRF 校验
    会把厂商域名（腾讯云 COS 桶域名在生产机解析成 169.254.x 的云内网回源地址）判成内网链接
    返 400，用户看到的就是「无法预览和播放」。改成浏览器直连 + 代理兜底。
    """
    src = _src()
    assert "const source = libraryMediaSource(url, filename);" in src
    assert (
        'src="${escapeHtml(videoFirstFrameUrl(source.src))}"${libraryMediaFallbackAttr(source)}'
        in src
    ), "大播放器要直链优先、并带上代理兜底属性"
    assert 'return `<img class="asset-preview-large" ${libraryMediaSourceHtml(url, filename)} alt="">`;' in src


def test_content_record_and_avatar_preview_do_not_force_proxy():
    src = _src()
    assert 'mediaProxyUrl(coverUrl, "inline", filenameFromUrl(coverUrl, "cover"))' not in src, (
        "内容记录 PPT 封面不要再强制走代理"
    )
    assert (
        'const src = mediaProxyUrl(mediaUrl, "inline", filenameFromUrl(mediaUrl, isVoice ? "voice.mp3" : "avatar"));'
        not in src
    ), "形象/声音分身的图片预览不要再强制走代理"
