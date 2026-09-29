"""画布代理：登录态鉴权、白名单/黑名单、占位 key、转发行为。

2026-09-29：入口不再要单独的 CANVAS_SELFTEST_KEY，改成 online 账号 JWT；
上游 apiz 继续用服务器配置的速推 key（这里 monkeypatch 成 sk-server-key 验证注入）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import canvas_proxy
from backend.app.api.auth import get_current_user


class FakeUser:
    id = 42
    username = "tester"


class FakeResponse:
    def __init__(self, status_code: int = 200, content: bytes = b'{"ok": true}', headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {"content-type": "application/json"}


def patch_upstream(monkeypatch, response: FakeResponse | None = None) -> dict:
    seen: dict = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, content=None, headers=None, params=None):
            seen.update({"method": method, "url": url, "content": content, "headers": headers, "params": params})
            return response or FakeResponse()

    monkeypatch.setattr(canvas_proxy.httpx, "AsyncClient", FakeClient)

    async def fake_headers():
        return {"Authorization": "Bearer sk-server-key", "Accept": "application/json"}

    monkeypatch.setattr(canvas_proxy, "_apiz_headers", fake_headers)
    return seen


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(canvas_proxy.router)
    app.dependency_overrides[get_current_user] = lambda: FakeUser()
    return TestClient(app)


@pytest.fixture
def anon_client():
    app = FastAPI()
    app.include_router(canvas_proxy.router)
    return TestClient(app)


def test_requires_online_login(anon_client):
    assert anon_client.get("/canvas-api/api/v3/mcp/models").status_code == 401
    assert anon_client.get("/canvas-api/api/v3/mcp/models", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_selftest_key_is_gone():
    """不要再出现单独的画布 key。"""
    text = Path(canvas_proxy.__file__).read_text(encoding="utf-8")
    assert "CANVAS_SELFTEST_KEY" not in text


def test_works_without_any_extra_key(client, monkeypatch):
    monkeypatch.delenv("CANVAS_SELFTEST_KEY", raising=False)
    patch_upstream(monkeypatch)
    assert client.get("/canvas-api/api/v3/mcp/models").status_code == 200


def test_site_account_endpoints_are_blocked(client):
    for path, method in [
        ("/canvas-api/api/user_info", "post"),
        ("/canvas-api/api/login", "post"),
        ("/canvas-api/api/v3/account/pay", "post"),
        ("/canvas-api/api/v3/keys", "post"),
        ("/canvas-api/api/v3/account/packages", "post"),
    ]:
        resp = getattr(client, method)(path, json={})
        assert resp.status_code == 403, path


def test_apikeys_returns_placeholder_not_real_key(client):
    resp = client.post("/canvas-api/api/v3/apikeys/list", json={})
    assert resp.status_code == 200
    key = resp.json()["data"]["items"][0]["key"]
    assert key.startswith("sk-lobster-canvas")


def test_forwarding_injects_server_key_and_passes_status_through(client, monkeypatch):
    seen = patch_upstream(monkeypatch, FakeResponse(status_code=402, content=b'{"detail": "insufficient credits"}'))
    resp = client.get("/canvas-api/api/v3/mcp/models?lang=zh-CN")
    assert resp.status_code == 402
    assert resp.text == '{"detail": "insufficient credits"}'
    assert seen["method"] == "GET"
    assert seen["url"] == "https://api.apiz.ai/api/v3/mcp/models"
    assert seen["params"] == {"lang": "zh-CN"}
    assert seen["headers"]["Authorization"] == "Bearer sk-server-key"
    assert "sk-server-key" not in str(seen.get("params"))


def test_task_create_body_is_forwarded_with_server_key(client, monkeypatch):
    seen = patch_upstream(monkeypatch, FakeResponse(content=b'{"code": 200, "data": {"task_id": "t-1"}}'))
    resp = client.post(
        "/canvas-api/api/v3/tasks/create",
        json={"model": "minimax/h3", "prompt": "hi"},
        headers={"Authorization": "Bearer sk-browser-placeholder"},
    )
    assert resp.status_code == 200
    assert b"minimax/h3" in seen["content"]
    assert seen["headers"]["Authorization"] == "Bearer sk-server-key"
    assert "sk-browser-placeholder" not in str(seen["headers"])


def test_business_paths_are_forwarded(client, monkeypatch):
    """画布业务接口（项目模板、素材、任务）默认放行——白名单曾经把它们全 403 了。"""
    for path in ["/canvas-api/api/v1/projects/public", "/canvas-api/api/get_draft_template", "/canvas-api/api/get_file_list"]:
        seen = patch_upstream(monkeypatch)
        resp = client.post(path, json={})
        assert resp.status_code == 200, path
        assert seen["url"].startswith("https://api.apiz.ai/"), path


def test_money_and_login_paths_are_blocked(client):
    for path in ["/canvas-api/api/login", "/canvas-api/api/get_qrcode", "/canvas-api/api/user_info",
                 "/canvas-api/api/get_file_list", "/canvas-api/api/user_oss_upload",
                 "/canvas-api/api/upload-token", "/canvas-api/api/get_cf_r2_token",
                 "/canvas-api/api/get_user_money", "/canvas-api/api/create_wx_order_info",
                 "/canvas-api/api/v3/account/pay", "/canvas-api/api/admin/get_all_tickets"]:
        assert client.post(path, json={}).status_code == 403, path


def test_path_traversal_is_rejected(client):
    assert client.get("/canvas-api/..%2F..%2Fetc%2Fpasswd").status_code in (403, 404)


def test_create_app_still_compiles():
    """防止又把静态挂载插进多行语句里：create_app.py 必须能编译。"""
    import py_compile

    root = Path(__file__).resolve().parents[2]
    py_compile.compile(str(root / "backend" / "app" / "create_app.py"), doraise=True)
