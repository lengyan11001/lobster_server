"""H5 媒体代理的素材域名白名单（2026-10-16 修「H5 内容记录 视频无法预览和播放」）。

生产机是腾讯云广州 CVM，会把下面这些厂商 CDN 的公网域名解析成 169.254.0.0/16 的链路本地
地址、走云内网回源（同机 curl 实测 200/206、支持 Range）。_assert_public_remote_url 里那个
「内网/链路本地」判定不认识这种回源方式，把它们一律 400 掉，H5 播放器就拿不到数据。

这里锁死四件事：
  1) 厂商素材域名放行；
  2) 公网素材域名照旧放行；
  3) 白名单之外照旧按 IP 拦（云元数据 / 本机 / 伪装后缀）；
  4) 白名单只认域名样式，不放开整段 169.254.0.0/16。
"""
from __future__ import annotations

import socket

import pytest

from backend.app.api import h5_chat

COS_MP4 = (
    "https://ai-res-bhb-1300308946.cos.ap-guangzhou.myqcloud.com/virtualman/caches/results/"
    "2026/1016000643/fastFigure_1792080071565_7613fd6eccb8446289aad8882352fac1_a1bc56/"
    "version/60e100efa33b767f4dbee63f9b603aab-final.mp4"
)

# 生产机 getaddrinfo 实测答案（2026-10-16），本机复现时用它打桩，不用本机 DNS。
PROD_DNS = {
    "ai-res-bhb-1300308946.cos.ap-guangzhou.myqcloud.com": "169.254.0.47",
    "cdn-video.51sux.com": "169.254.254.254",
    "cdn-ali-hk.51sux.com": "169.254.254.254",
    "lobster-online-assets-2103871705.tos-cn-guangzhou.volces.com": "106.126.14.217",
    "metadata.tencentyun.com": "169.254.0.23",
}


def _stub_prod_dns(monkeypatch) -> None:
    def fake(host, port=0, *args, **kwargs):
        key = str(host).strip().lower()
        if key in PROD_DNS:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PROD_DNS[key], port or 443))]
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_tencent_cos_bucket_is_allowed(monkeypatch):
    _stub_prod_dns(monkeypatch)
    assert h5_chat._assert_public_remote_url(COS_MP4) == COS_MP4


def test_51sux_cdn_is_allowed(monkeypatch):
    _stub_prod_dns(monkeypatch)
    url = "https://cdn-video.51sux.com/x/a.mp4"
    assert h5_chat._assert_public_remote_url(url) == url


def test_public_media_host_still_allowed(monkeypatch):
    _stub_prod_dns(monkeypatch)
    url = "https://lobster-online-assets-2103871705.tos-cn-guangzhou.volces.com/a/final.mp4"
    assert h5_chat._assert_public_remote_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://metadata.tencentyun.com/latest/meta-data/",
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/api/x",
        "https://ai-res-bhb-1300308946.cos.ap-guangzhou.myqcloud.com.evil.myqcloud.com/x.mp4",
    ],
)
def test_ssrf_guard_is_not_weakened(monkeypatch, url):
    _stub_prod_dns(monkeypatch)
    with pytest.raises(h5_chat.HTTPException) as exc:
        h5_chat._assert_public_remote_url(url)
    assert exc.value.status_code == 400


def test_trusted_host_matcher_only_matches_media_vendors():
    assert h5_chat._is_trusted_media_host("ai-res-bhb-1300308946.cos.ap-guangzhou.myqcloud.com")
    assert h5_chat._is_trusted_media_host("cdn-ali-hk.51sux.com")
    assert h5_chat._is_trusted_media_host("AI-HELLO-1.COS.AP-GUANGZHOU.MYQCLOUD.COM.")
    for bad in (
        "evil.myqcloud.com",
        "169.254.0.47",
        "localhost",
        "",
        "myqcloud.com",
        "ai-res-bhb-1300308946.cos.ap-guangzhou.myqcloud.com.evil.com",
    ):
        assert not h5_chat._is_trusted_media_host(bad), bad
