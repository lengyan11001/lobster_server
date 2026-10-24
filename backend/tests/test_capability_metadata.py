import json
from pathlib import Path

from backend.app.services.mastra_online_capabilities import mastra_online_capabilities


ALIASES = {"read", "write", "external"}


def test_server_catalog_declares_side_effect():
    root = Path(__file__).resolve().parents[2]
    catalog = json.loads((root / "mcp" / "capability_catalog.json").read_text(encoding="utf-8"))
    missing = [cid for cid, cfg in catalog.items() if not isinstance(cfg, dict) or cfg.get("side_effect") not in ALIASES]
    assert not missing, missing
    assert catalog["web.page_analyze"]["side_effect"] == "read"
    assert catalog["web.site_crawl"]["side_effect"] == "read"


def test_online_capabilities_declare_side_effect():
    capabilities = mastra_online_capabilities()
    missing = [cid for cid, cfg in capabilities.items() if cfg.get("side_effect") not in ALIASES]
    assert not missing, missing
    assert capabilities["online.browser_extract_page"]["side_effect"] == "read"
    assert capabilities["online.publish_content"]["side_effect"] == "external"
