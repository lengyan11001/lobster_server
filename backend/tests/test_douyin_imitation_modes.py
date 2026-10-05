"""热门视频跟创（原「抖音信息台做同款」）：模式提示词 + 视频来源校验。

2026-10-05 需求：信息台改名「热门视频跟创」，新增两种模式（复刻特效 / 复刻单人动作），
不同模式用不同提示词；视频来源支持用户粘贴链接或上传本地视频。
"""
from __future__ import annotations

import asyncio

from backend.app.services.douyin_imitation_video import (
    MODE_PROMPTS,
    mode_label,
    normalize_mode,
    prepare_imitation,
    prompt_for_mode,
)


def test_mode_prompts_are_distinct_and_normalized():
    assert set(MODE_PROMPTS) == {"person_swap", "effect_copy", "action_copy"}
    assert normalize_mode("effect_copy") == "effect_copy"
    assert normalize_mode("ACTION_COPY") == "action_copy"
    assert normalize_mode("谁也不是") == "person_swap"

    assert prompt_for_mode("effect_copy") == "编辑视频，把视频1中的特效应用到图片1中的人物身上，场景、镜头与节奏跟随视频1，画面其余部分保持不变。"
    assert prompt_for_mode("action_copy") == "编辑视频，让图片1中的人物做出视频1中人物的动作，视频1的镜头、节奏与场景保持不变。"
    assert prompt_for_mode("person_swap") == MODE_PROMPTS["person_swap"]
    # 用户自己填了就用他的
    assert prompt_for_mode("effect_copy", "  我的提示词  ") == "我的提示词"

    assert mode_label("effect_copy") == "复刻特效"
    assert mode_label("action_copy") == "复刻单人动作"
    assert mode_label(None) == "复刻人物（换人）"


def test_prepare_imitation_needs_a_video_source():
    result = asyncio.run(prepare_imitation("https://cdn.example.com/a.jpg", "", "", mode="effect_copy"))
    assert result["ok"] is False
    assert "视频" in result["error"]


def test_prepare_imitation_rejects_bad_video_and_image_urls():
    result = asyncio.run(
        prepare_imitation("https://cdn.example.com/a.jpg", "", "", video_url="ftp://cdn.example.com/a.mp4")
    )
    assert result["ok"] is False
    assert "视频地址无效" in result["error"]

    result = asyncio.run(prepare_imitation("not-a-url", "123456", ""))
    assert result["ok"] is False
    assert "参考图" in result["error"]


def test_extract_douyin_item_id_handles_page_links_and_plain_urls():
    """用户贴 https://www.douyin.com/video/7688685833386071653 这种作品页链接要能识别。"""
    from backend.app.services.douyin_imitation_video import extract_douyin_item_id

    assert asyncio.run(extract_douyin_item_id("https://www.douyin.com/video/7688685833386071653")) == "7688685833386071653"
    assert asyncio.run(extract_douyin_item_id("https://www.iesdouyin.com/share/video/7688685833386071653/?a=1")) == "7688685833386071653"
    assert asyncio.run(extract_douyin_item_id("7688685833386071653")) == "7688685833386071653"
    # 普通直链 / 本地文件名不该被当成抖音作品
    assert asyncio.run(extract_douyin_item_id("https://cdn.example.com/a.mp4")) == ""
    assert asyncio.run(extract_douyin_item_id("")) == ""


def test_prepare_imitation_resolves_douyin_page_link_through_tikhub(monkeypatch):
    """贴抖音作品链接时，不该去下载网页，而是用作品 id 走 TikHub 解析真实播放地址。"""
    from backend.app.services import douyin_imitation_video as svc

    seen = {}

    async def fake_resolve(item_id):
        seen["item_id"] = item_id
        return {"ok": True, "urls": ["https://cdn.example.com/real.mp4"], "url": "https://cdn.example.com/real.mp4",
                "desc": "抖音作品"}

    async def fake_download_first(urls, limit=None):
        seen["urls"] = list(urls)
        return b"\x00\x00\x00\x18ftypmp42", "https://cdn.example.com/real.mp4", ""

    async def fake_upload(data, suffix, content_type):
        return f"https://tos.test/up{suffix}", ""

    async def fake_submit(image_url, video_url, *, mode="wan-std", prompt="", resolution="720P",
                        model="", duration_seconds=0):
        seen["prompt"] = prompt
        return {"ok": True, "task_id": "t-1", "model": "m", "provider": "p"}

    async def fake_download(url, limit=None):
        seen["direct_download"] = url
        return b"<html></html>", ""

    monkeypatch.setattr(svc, "resolve_source_video", fake_resolve)
    monkeypatch.setattr(svc, "_download_first", fake_download_first)
    monkeypatch.setattr(svc, "_download", fake_download)
    monkeypatch.setattr(svc, "_upload_tos", fake_upload)
    monkeypatch.setattr(svc, "submit_imitation", fake_submit)
    monkeypatch.setattr(svc, "trim_video", lambda data, seconds: (data, ""))

    result = asyncio.run(svc.prepare_imitation(
        "https://tos.test/ref.png", "", "", video_url="https://www.douyin.com/video/7688685833386071653",
        mode="action_copy",
    ))

    assert result["ok"] is True, result
    assert seen["item_id"] == "7688685833386071653"
    # _download 还会被用来取参考图，这里只确认「抖音作品页链接」没走直链下载
    assert seen.get("direct_download") != "https://www.douyin.com/video/7688685833386071653"
    assert result["prompt"] == "编辑视频，让图片1中的人物做出视频1中人物的动作，视频1的镜头、节奏与场景保持不变。"
    assert result["mode_label"] == "复刻单人动作"


def test_prepare_imitation_rejects_html_for_direct_links(monkeypatch):
    """非抖音的网页地址：明确提示这不是视频，而不是丢给 ffmpeg 报错。"""
    from backend.app.services import douyin_imitation_video as svc

    async def fake_download(url, limit=None):
        return b"<!DOCTYPE html><html><body>not a video</body></html>", ""

    monkeypatch.setattr(svc, "_download", fake_download)

    result = asyncio.run(svc.prepare_imitation(
        "https://tos.test/ref.png", "", "", video_url="https://example.com/watch?v=1"
    ))

    assert result["ok"] is False
    assert "网页不是视频" in result["error"]


def test_resolution_option_changes_price_and_has_no_480p():
    """分辨率放给用户选：720P 0.6 元/秒、1080P 1 元/秒；官方没有 480P 档。"""
    from backend.app.services.douyin_desk_billing import estimate_imitation, normalize_resolution
    from backend.app.services.douyin_imitation_video import normalize_resolution as svc_norm

    p720 = estimate_imitation(6, "720P")
    p1080 = estimate_imitation(6, "1080P")

    assert p720["billable_seconds"] == 12 and p720["credits"] == 1080
    assert p720["yuan_per_second"] == 0.6
    assert p1080["resolution"] == "1080P" and p1080["yuan_per_second"] == 1.0
    assert p1080["credits"] == 1800, p1080

    # 没有 480P：不认识的档位一律回落 720P
    assert normalize_resolution("480P") == "720P"
    assert normalize_resolution("1080p") == "1080P"
    assert svc_norm("480P") == "720P"
    assert svc_norm("1080p") == "1080P"


def test_prepare_imitation_passes_resolution_to_submit(monkeypatch):
    from backend.app.services import douyin_imitation_video as svc

    seen = {}

    async def fake_resolve(item_id):
        return {"ok": True, "urls": ["https://cdn.example.com/real.mp4"], "url": "https://cdn.example.com/real.mp4",
                "desc": "抖音作品"}

    async def fake_download_first(urls, limit=None):
        return b"\x00\x00\x00\x18ftypmp42", "https://cdn.example.com/real.mp4", ""

    async def fake_download(url, limit=None):
        return b"\xff\xd8\xff", ""

    async def fake_upload(data, suffix, content_type):
        return f"https://tos.test/up{suffix}", ""

    async def fake_submit(image_url, video_url, *, mode="wan-std", prompt="", resolution="720P",
                        model="", duration_seconds=0):
        seen["resolution"] = resolution
        return {"ok": True, "task_id": "t-r", "model": "m", "provider": "videoedit", "resolution": resolution}

    monkeypatch.setattr(svc, "resolve_source_video", fake_resolve)
    monkeypatch.setattr(svc, "_download_first", fake_download_first)
    monkeypatch.setattr(svc, "_download", fake_download)
    monkeypatch.setattr(svc, "_upload_tos", fake_upload)
    monkeypatch.setattr(svc, "submit_imitation", fake_submit)
    monkeypatch.setattr(svc, "trim_video", lambda data, seconds: (data, ""))
    monkeypatch.setattr(svc, "probe_video_seconds", lambda data: 6.0)

    result = asyncio.run(svc.prepare_imitation(
        "https://tos.test/ref.png", "7688685833386071653", "", mode="effect_copy", resolution="1080P"
    ))

    assert seen["resolution"] == "1080P"
    assert result["resolution"] == "1080P"
    assert result["video_seconds"] == 6


def _stub_pipeline(monkeypatch, svc, *, source_seconds, sent_seconds):
    """把下载/上传/提交都换成假实现，只观察「送审时长」怎么算。"""
    async def fake_resolve(item_id):
        return {"ok": True, "urls": ["https://cdn.example.com/v.mp4"], "url": "https://cdn.example.com/v.mp4",
                "desc": "抖音作品"}

    async def fake_download_first(urls, limit=None):
        return b"video-bytes", "https://cdn.example.com/v.mp4", ""

    async def fake_download(url, limit=None):
        return b"\xff\xd8\xff", ""

    async def fake_upload(data, suffix, content_type):
        return f"https://tos.test/up{suffix}", ""

    calls = {"submit": 0}

    async def fake_submit(image_url, video_url, *, mode="wan-std", prompt="", resolution="720P",
                        model="", duration_seconds=0):
        calls["submit"] += 1
        return {"ok": True, "task_id": "t-sent", "model": "m", "provider": "videoedit", "resolution": resolution}

    probes = {"count": 0}

    def fake_probe(data):
        probes["count"] += 1
        return float(source_seconds if probes["count"] == 1 else sent_seconds)

    monkeypatch.setattr(svc, "resolve_source_video", fake_resolve)
    monkeypatch.setattr(svc, "_download_first", fake_download_first)
    monkeypatch.setattr(svc, "_download", fake_download)
    monkeypatch.setattr(svc, "_upload_tos", fake_upload)
    monkeypatch.setattr(svc, "submit_imitation", fake_submit)
    monkeypatch.setattr(svc, "probe_video_seconds", fake_probe)
    monkeypatch.setattr(svc, "trim_video", lambda data, seconds: (data, ""))
    return calls


def test_long_source_is_trimmed_and_charged_by_sent_seconds(monkeypatch):
    """用户传 60 秒：我们裁到 15 秒再送，就按 15 秒收（2700），不会亏。"""
    from backend.app.services import douyin_imitation_video as svc

    monkeypatch.setenv("DOUYIN_IMITATION_MAX_SECONDS", "15")
    calls = _stub_pipeline(monkeypatch, svc, source_seconds=60.0, sent_seconds=15.0)

    result = asyncio.run(svc.prepare_imitation("https://tos.test/ref.png", "7688685833386071653"))

    assert result["ok"] is True, result
    assert calls["submit"] == 1
    assert result["source_seconds"] == 60.0
    assert result["sent_seconds"] == 15.0
    assert result["video_seconds"] == 15

    from backend.app.services.douyin_desk_billing import estimate_imitation
    plan = estimate_imitation(result["video_seconds"], "720P")
    assert plan["cost_yuan"] == 18.0            # 上游成本
    assert plan["credits"] == 2700              # 收用户 27 元 → 不亏


def test_untrimmable_long_video_is_refused_instead_of_submitting_original(monkeypatch):
    """裁剪没生效（送审的还是 60 秒）时直接拒单，避免按 15 秒收却按 60 秒付上游。"""
    from backend.app.services import douyin_imitation_video as svc

    monkeypatch.setenv("DOUYIN_IMITATION_MAX_SECONDS", "15")
    calls = _stub_pipeline(monkeypatch, svc, source_seconds=60.0, sent_seconds=60.0)

    result = asyncio.run(svc.prepare_imitation("https://tos.test/ref.png", "7688685833386071653"))

    assert result["ok"] is False
    assert "已取消提交" in result["error"]
    assert calls["submit"] == 0, "没裁成功就不该提交"


def test_short_source_charged_by_its_own_length(monkeypatch):
    """6 秒原视频：按 6 秒收 1080。"""
    from backend.app.services import douyin_imitation_video as svc

    monkeypatch.setenv("DOUYIN_IMITATION_MAX_SECONDS", "15")
    _stub_pipeline(monkeypatch, svc, source_seconds=6.0, sent_seconds=6.0)

    result = asyncio.run(svc.prepare_imitation("https://tos.test/ref.png", "7688685833386071653"))

    assert result["video_seconds"] == 6
