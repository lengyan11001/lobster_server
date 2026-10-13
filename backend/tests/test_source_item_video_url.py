"""作品提取：条目里「可下载视频地址」的取值（对齐线上真实数据形状）。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.api.ip_content_studio import pick_item_video_url  # noqa: E402


def test_wechat_channels_uses_public_url_stodownload():
    """视频号 home_page：真实地址就在 public_url（wxapp.tc.qq.com/...stodownload?...）。"""
    url = "http://wxapp.tc.qq.com/251/20302/stodownload?encfilekey=Cvvj5Ix3eez3Y7"
    assert pick_item_video_url(url, {}) == url


def test_douyin_uses_play_addr_from_raw():
    """抖音 user_post：public_url 是分享页，真实地址在 raw.video.play_addr.url_list[0]。"""
    play = "https://v3-search.douyinvod.com/2f7482/video/tos/cn/abc/"
    raw = {"video": {"play_addr": {"url_list": [play, "https://backup"]}}}
    assert pick_item_video_url("https://www.iesdouyin.com/share/video/7650102846263725321/", raw) == play


def test_returns_empty_when_nothing_usable():
    assert pick_item_video_url("", {}) == ""
    assert pick_item_video_url("https://example.com/page", {}) == ""


def test_video_channels_media_url_with_token():
    """视频号某些条目走 object_desc.media[0].url + url_token。"""
    raw = {"object_desc": {"media": [{"url": "https://finder.video.qq.com/xyz", "url_token": "token=abc"}]}}
    out = pick_item_video_url("", raw)
    assert out.startswith("https://finder.video.qq.com/xyz")
    assert "token=abc" in out
