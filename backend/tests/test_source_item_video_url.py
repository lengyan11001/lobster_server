"""作品提取：从已入库帖子里取「可下载视频地址/封面」（对齐线上真实 raw 结构）。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.api.ip_content_studio import pick_item_cover_url, pick_item_video_url  # noqa: E402

# 视频号 home_page 的真实结构（2026-10-13 生产库取样）
WX_RAW = {
    "objectDesc": {
        "spec": [
            {
                "url": "http://wxapp.tc.qq.com/251/20302/stodownload?encfilekey=ABC&hy=SZ&idx=1",
                "urlToken": "&token=XYZ123&basedata=1",
                "codingFormat": "h264",
                "thumbUrl": "http://wxapp.tc.qq.com/251/20304/stodownload?encfilekey=COVER",
                "thumbUrlToken": "&token=COVERTOK",
            }
        ]
    }
}


def test_wechat_channels_video_url_uses_spec_and_camel_token():
    """视频号：地址在 objectDesc.spec[0].url，token 是驼峰 urlToken（值形如 &token=…）。
    之前只找下划线 url_token，导致下载报 400 token not exist!"""
    out = pick_item_video_url("http://wxapp.tc.qq.com/251/20302/stodownload?encfilekey=ABC&hy=SZ&idx=1", WX_RAW)
    assert out.startswith("http://wxapp.tc.qq.com/251/20302/stodownload?encfilekey=ABC")
    assert "token=XYZ123" in out


def test_wechat_channels_cover_fallback_uses_thumb_token():
    out = pick_item_cover_url("", WX_RAW)
    assert out.startswith("http://wxapp.tc.qq.com/251/20304/stodownload?encfilekey=COVER")
    assert "token=COVERTOK" in out


def test_douyin_uses_play_addr_from_raw():
    play = "https://v3-search.douyinvod.com/2f7482/video/tos/cn/abc/"
    raw = {"video": {"play_addr": {"url_list": [play, "https://backup"]}}}
    assert pick_item_video_url("https://www.iesdouyin.com/share/video/7650/", raw) == play


def test_share_page_never_used_as_direct_link():
    assert pick_item_video_url("https://www.douyin.com/video/7650102846263725321", {}) == ""
    assert pick_item_video_url("", {}) == ""


def test_cover_prefers_existing_value():
    assert pick_item_cover_url("https://p26-sign.douyinpic.com/x.heic", {}) == "https://p26-sign.douyinpic.com/x.heic"
