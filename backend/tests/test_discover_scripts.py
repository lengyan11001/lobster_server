from backend.app.services.discover import (
    calendar as calendar_mod,
    competitor as competitor_mod,
    gap as gap_mod,
    hotlist,
    knowledge as knowledge_mod,
    news,
    rss,
    scoring,
    suggestions,
    ugc as ugc_mod,
)
from backend.app.services.discover import cache as discover_cache
from backend.app.services.mastra_online_capabilities import (
    mastra_online_capabilities,
    validate_capability_catalog,
)


def test_hotlist_selftest():
    assert hotlist.selftest() == []


def test_rss_selftest():
    assert rss.selftest() == []


def test_news_selftest():
    assert news.selftest() == []


def test_calendar_selftest():
    assert calendar_mod.selftest() == []


def test_scoring_and_gap_selftest():
    assert scoring.selftest() == []
    assert suggestions.selftest() == []
    assert gap_mod.selftest() == []


def test_competitor_and_ugc_selftest():
    assert competitor_mod.selftest() == []
    assert ugc_mod.selftest() == []


def test_knowledge_selftest():
    assert knowledge_mod.selftest() == []


def test_cache_roundtrip():
    discover_cache.clear()
    discover_cache.set("k", {"v": 1}, 60)
    assert discover_cache.get("k") == {"v": 1}
    discover_cache.clear()


def test_discover_capabilities_registered_and_complete():
    caps = mastra_online_capabilities()
    for cid in ("online.discover_hotlist", "online.discover_news", "online.discover_rss",
                "online.discover_calendar", "online.discover_gap",
                "online.discover_competitor", "online.discover_ugc",
                "online.discover_algorithm", "online.discover_platform_diff"):
        assert cid in caps, cid
        cfg = caps[cid]
        assert cfg["task_kind"] == "server_action"
        assert cfg["side_effect"] == "read"
        assert cfg["layer"] == "discover"
    assert validate_capability_catalog() == []
