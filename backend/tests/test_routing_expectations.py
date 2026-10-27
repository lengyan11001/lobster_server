from backend.app.services.mastra_online_capabilities import mastra_online_capabilities

CAPS = mastra_online_capabilities()

# (用户消息, 期望能力, 不该被选中的对照能力)
CASES = [
    ("今天有什么热搜", "online.discover_hotlist", "online.browser_extract_page"),
    ("最近 AI 行业有什么新闻", "online.discover_news", "online.browser_extract_page"),
    ("未来一个月有什么节日可以蹭", "online.discover_calendar", "online.discover_news"),
    ("家居收纳有什么蓝海选题", "online.discover_gap", "online.discover_hotlist"),
    ("这个选题值不值得做", "plan_topic_eval", "plan_hook"),
    ("帮我写 6 个开头钩子", "plan_hook", "plan_topic_eval"),
    ("这个月内容怎么排", "plan_calendar", "online.discover_calendar"),
    ("我的定位是什么，怎么和竞品区分", "plan_positioning", "online.discover_competitor"),
    ("拆解一下这个竞品账号", "online.discover_competitor", "plan_positioning"),
    ("把这条内容发到小红书", "online.publish_content", "online.discover_ugc"),
    ("生成一条数字人口播视频", "online.digital_human_v2", "online.image_studio"),
    ("打开这个网页看看正文", "online.browser_extract_page", "online.discover_hotlist"),
]


def _score(message: str, cid: str) -> int:
    cfg = CAPS.get(cid) or {}
    text = message.lower()
    score = 0
    for kw in cfg.get("keywords") or []:
        k = str(kw).lower()
        if k and k in text:
            score += 3 if len(k) >= 3 else 2
    desc = str(cfg.get("description") or "").lower()
    if desc and any(len(p) >= 3 and p in text for p in desc.replace("，", " ").replace("。", " ").split()):
        score += 1
    return score


def test_routing_prefers_expected_capability():
    failures = []
    for message, expected, decoy in CASES:
        exp = _score(message, expected)
        dec = _score(message, decoy)
        if exp <= dec:
            failures.append((message, expected, exp, decoy, dec))
    assert not failures, failures


def test_hotlist_beats_browser_for_hot_search():
    assert _score("今天有什么热搜", "online.discover_hotlist") > _score("今天有什么热搜", "online.browser_extract_page")


def test_browser_only_when_page_requested():
    assert _score("打开这个网页看看正文", "online.browser_extract_page") > _score("打开这个网页看看正文", "online.discover_hotlist")
    assert _score("今天有什么热搜", "online.browser_extract_page") == 0
