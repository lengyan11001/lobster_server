from backend.app.api import media_proxy


def test_media_thumb_allows_51sux_host():
    url = "https://cdn-hk.51sux.com/v3-tasks/a.jpg"
    assert media_proxy._check_url(url) == url


def test_media_thumb_failure_returns_transparent_png():
    resp = media_proxy.media_thumb("ftp://not-allowed/a.jpg")
    assert resp.status_code == 200
    assert resp.media_type == "image/png"
    assert resp.body == media_proxy._TRANSPARENT_PNG
