import asyncio
from pathlib import Path


def _clear_pool_env(monkeypatch):
    for name in (
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


def test_oem_brand_inherits_default_physical_pool(monkeypatch):
    from mcp.sutui_tokens import next_sutui_server_token_with_pool

    _clear_pool_env(monkeypatch)
    monkeypatch.setenv("SUTUI_SERVER_TOKENS_BIHUO", "token-a,token-b")

    token, pool = asyncio.run(next_sutui_server_token_with_pool(brand_mark="daka"))

    assert token == "token-a"
    assert pool == "bihuo"


def test_oem_brand_can_map_to_a_dedicated_or_shared_pool(monkeypatch):
    from mcp.sutui_tokens import next_sutui_server_token_with_pool

    _clear_pool_env(monkeypatch)
    monkeypatch.setenv("SUTUI_BRAND_POOL_MAP", '{"daka":"yingshi"}')
    monkeypatch.setenv("SUTUI_SERVER_TOKEN_YINGSHI", "token-y")
    monkeypatch.setenv("SUTUI_SERVER_TOKEN_WHITE_LABEL", "token-w")

    mapped_token, mapped_pool = asyncio.run(next_sutui_server_token_with_pool(brand_mark="daka"))
    own_token, own_pool = asyncio.run(next_sutui_server_token_with_pool(brand_mark="white-label"))

    assert (mapped_token, mapped_pool) == ("token-y", "yingshi")
    assert (own_token, own_pool) == ("token-w", "white_label")


def test_missing_brand_never_uses_a_user_fallback(monkeypatch):
    from mcp.sutui_tokens import next_sutui_server_token_with_pool

    _clear_pool_env(monkeypatch)
    monkeypatch.setenv("SUTUI_SERVER_TOKEN_BIHUO", "token-a")

    token, pool = asyncio.run(next_sutui_server_token_with_pool(brand_mark=""))

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
