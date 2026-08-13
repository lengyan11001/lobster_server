import asyncio
from pathlib import Path


def _clear_pool_env(monkeypatch):
    for name in (
        "SUTUI_SERVER_TOKENS",
        "SUTUI_SERVER_TOKEN",
        "SUTUI_BRAND_POOL_MAP",
        "SUTUI_DEFAULT_BRAND_POOL",
        "SUTUI_SERVER_TOKENS_BIHUO",
        "SUTUI_SERVER_TOKEN_BIHUO",
        "SUTUI_SERVER_TOKENS_YINGSHI",
        "SUTUI_SERVER_TOKEN_YINGSHI",
        "SUTUI_SERVER_TOKENS_WHITE_LABEL",
        "SUTUI_SERVER_TOKEN_WHITE_LABEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_all_oem_brands_use_shared_sutui_pool(monkeypatch):
    from mcp.sutui_tokens import next_sutui_server_token_with_pool

    _clear_pool_env(monkeypatch)
    monkeypatch.setenv("SUTUI_SERVER_TOKENS", "shared-a,shared-b")

    bihuo_token, bihuo_pool = asyncio.run(next_sutui_server_token_with_pool(brand_mark="bihuo"))
    jinghai_token, jinghai_pool = asyncio.run(next_sutui_server_token_with_pool(brand_mark="jinghai"))

    assert (bihuo_token, bihuo_pool) == ("shared-a", "shared")
    assert (jinghai_token, jinghai_pool) == ("shared-a", "shared")


def test_brand_pool_map_is_ignored_when_shared_pool_exists(monkeypatch):
    from mcp.sutui_tokens import next_sutui_server_token_with_pool

    _clear_pool_env(monkeypatch)
    monkeypatch.setenv("SUTUI_BRAND_POOL_MAP", '{"daka":"yingshi"}')
    monkeypatch.setenv("SUTUI_SERVER_TOKEN", "shared-token")
    monkeypatch.setenv("SUTUI_SERVER_TOKEN_YINGSHI", "token-y")
    monkeypatch.setenv("SUTUI_SERVER_TOKEN_WHITE_LABEL", "token-w")

    mapped_token, mapped_pool = asyncio.run(next_sutui_server_token_with_pool(brand_mark="daka"))
    own_token, own_pool = asyncio.run(next_sutui_server_token_with_pool(brand_mark="white-label"))

    assert (mapped_token, mapped_pool) == ("shared-token", "shared")
    assert (own_token, own_pool) == ("shared-token", "shared")


def test_legacy_bihuo_env_is_only_shared_compatibility_fallback(monkeypatch):
    from mcp.sutui_tokens import next_sutui_server_token_with_pool

    _clear_pool_env(monkeypatch)
    monkeypatch.setenv("SUTUI_SERVER_TOKEN_BIHUO", "token-a")

    token, pool = asyncio.run(next_sutui_server_token_with_pool(brand_mark=""))
    jinghai_token, jinghai_pool = asyncio.run(next_sutui_server_token_with_pool(brand_mark="jinghai"))

    assert (token, pool) == ("token-a", "bihuo")
    assert (jinghai_token, jinghai_pool) == ("token-a", "bihuo")


def test_no_sutui_token_returns_none(monkeypatch):
    from mcp.sutui_tokens import next_sutui_server_token_with_pool

    _clear_pool_env(monkeypatch)

    token, pool = asyncio.run(next_sutui_server_token_with_pool(brand_mark="jinghai"))

    assert token is None
    assert pool == "none"


def test_sutui_entry_points_do_not_hardcode_brand_allowlists():
    root = Path(__file__).resolve().parents[2]
    paths = [
        root / "backend" / "app" / "api" / "capabilities.py",
        root / "backend" / "app" / "api" / "sutui_chat_proxy.py",
        root / "mcp" / "http_server.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert 'not in ("bihuo", "yingshi")' not in combined
    assert "未绑定必火/影视品牌" not in combined
