"""能力路由回归测试：网页抓取意图必须只暴露网页能力，且禁止落到生成能力。

2026-11-01 事故：用户在客户端要求“打开 hexing.com 抓正文”，模型选了 comfly.daihuo（图生视频），
返回 Yunwu 502。根因是能力清单/工具面对多条链路各写一套，网页能力不可见。
"""
from backend.app.services.capability_surface import (
    all_capabilities,
    allowed_ids_for_intent,
    intent_of,
    web_read_guard,
)

WEB_Q = "在用户电脑浏览器中打开 https://www.hexing.com ，向下滚动 2 屏，抓取页面正文"


def test_web_capabilities_visible():
    caps = all_capabilities()
    assert "web.page_analyze" in caps
    assert "web.site_crawl" in caps
    assert "online.browser_extract_page" in caps


def test_web_intent_detected():
    assert intent_of(WEB_Q) == "web_read"


def test_web_intent_only_exposes_web_tools():
    ids = list(all_capabilities())
    allowed = allowed_ids_for_intent("web_read", ids)
    assert set(allowed) <= {"web.page_analyze", "web.site_crawl", "online.browser_extract_page"}
    assert "comfly.daihuo" not in allowed


def test_guard_blocks_generation_for_web_read():
    msg = web_read_guard("comfly.daihuo", {}, WEB_Q)
    assert msg and "web.page_analyze" in msg


def test_guard_allows_generation_for_video_request():
    assert web_read_guard("comfly.daihuo", {}, "帮我生成一段带货视频") is None
