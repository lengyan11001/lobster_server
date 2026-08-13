import asyncio

from backend.app.api import meshy_proxy


def test_meshy_user_balance_returns_server_balance(monkeypatch) -> None:
    async def fake_meshy_request(method, path, *, timeout, json_body=None):
        assert method == "GET"
        assert path == "/balance"
        assert json_body is None
        return {"balance": "12.5", "extra": "not exposed"}

    monkeypatch.setattr(meshy_proxy, "_meshy_request", fake_meshy_request)

    result = asyncio.run(meshy_proxy.meshy_proxy_user_balance(object()))

    assert result == {
        "ok": True,
        "configured": True,
        "provider": "meshy",
        "balance": "12.5",
        "balance_unit": "credits",
    }
