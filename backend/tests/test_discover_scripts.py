from backend.app.services.discover import hotlist, news, rss
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


def test_cache_roundtrip():
    discover_cache.clear()
    discover_cache.set("k", {"v": 1}, 60)
    assert discover_cache.get("k") == {"v": 1}
    discover_cache.set("expired", {"v": 1}, 1)
    discover_cache.clear()


def test_discover_capabilities_registered_and_complete():
    caps = mastra_online_capabilities()
    for cid in ("online.discover_hotlist", "online.discover_news", "online.discover_rss"):
        assert cid in caps, cid
        cfg = caps[cid]
        assert cfg["task_kind"] == "server_action"
        assert cfg["side_effect"] == "read"
        assert cfg["layer"] == "discover"
    assert validate_capability_catalog() == []
