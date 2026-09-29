"""画布代理：凭据、白名单/黑名单、占位 key、转发行为。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import canvas_proxy

KEY = "canvas-selftest-test-key"
AUTH = {"Authorization": "Bearer " + KEY}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CANVAS_SELFTEST_KEY", KEY)
    app = FastAPI()
    app.include_router(canvas_proxy.router)
    return TestClient(app)


def test_requires_selftest_key(client):
    assert client.get("/canvas-api/api/v3/mcp/models").status_code == 401
    assert client.get("/canvas-api/api/v3/mcp/models", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_site_account_endpoints_are_blocked(client):
    for path, method in [
        ("/canvas-api/api/user_info", "post"),
        ("/canvas-api/api/login", "post"),
        ("/canvas-api/api/v3/account/pay", "post"),
        ("/canvas-api/api/v3/keys", "post"),
        ("/canvas-api/api/v3/account/packages", "post"),
    ]:
        resp = getattr(client, method)(path, headers=AUTH, json={})
        assert resp.status_code == 403, path


def test_apikeys_returns_placeholder_not_real_key(client):
    resp = client.post("/canvas-api/api/v3/apikeys/list", headers=AUTH, json={})
    assert resp.status_code == 200
    key = resp.json()["data"]["items"][0]["key"]
    assert key.startswith("sk-lobster-canvas")


def test_forwarding_allowlist_and_key_injection(client, monkeypatch):
    import asyncio

    seen = {}

    class FakeResponse:
        status_code = 200
        content = b'{"ok": true}'
        headers = {"content-type": "application/json"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, content=None, headers=None, params=None):
            seen["method"] = method
            seen["url"] = url
            seen["headers"] = headers
            seen["params"] = params
            return FakeResponse()

    monkeypatch.setattr(canvas_proxy.httpx, "AsyncClient", FakeClient)

    async def fake_headers():
        return {"Authorization": "Bearer sk-server-key", "Accept": "application/json"}

    monkeypatch.setattr(canvas_proxy, "_apiz_headers", fake_headers)
    resp = client.get("/canvas-api/api/v3/mcp/models?lang=zh-CN", headers=AUTH)
    assert resp.status_code == 200
    assert seen["url"] == "https://api.apiz.ai/api/v3/mcp/models"
    assert seen["params"] == {"lang": "zh-CN"}
    assert seen["headers"]["Authorization"] == "Bearer sk-server-key"
    assert "sk-server-key" not in str(seen.get("params"))


def test_unknown_path_is_rejected(client):
    resp = client.get("/canvas-api/api/whatever", headers=AUTH)
    assert resp.status_code == 403
