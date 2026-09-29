"""画布代理：登录态鉴权、白名单/黑名单、占位 key、转发行为。

2026-09-29：入口不再要单独的 CANVAS_SELFTEST_KEY，改成 online 账号 JWT；
上游 apiz 继续用服务器配置的速推 key（这里 monkeypatch 成 sk-server-key 验证注入）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import canvas_hub, canvas_proxy
from backend.app.api.auth import get_current_user
from backend.app.db import get_db


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
def client(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///" + str(tmp_path / "canvas_test.db").replace("\\", "/"))
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(canvas_hub, "_tables_ready", False)
    monkeypatch.setattr(canvas_hub, "UPLOAD_DIR", tmp_path / "uploads")

    app = FastAPI()
    app.include_router(canvas_proxy.router)
    app.dependency_overrides[get_current_user] = lambda: FakeUser()
    app.dependency_overrides[get_db] = lambda: session_factory()
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
    for path in ["/canvas-api/api/create_video_task", "/canvas-api/api/v2/tasks/create", "/canvas-api/api/v3/mcp/models"]:
        seen = patch_upstream(monkeypatch)
        resp = client.post(path, json={})
        assert resp.status_code == 200, path
        assert seen["url"].startswith("https://api.apiz.ai/"), path


def test_money_and_login_paths_are_blocked(client):
    """账号 / 钱 / Key / 后台：仍然一律 403（这些不自己做，也不转发）。"""
    for path in ["/canvas-api/api/login", "/canvas-api/api/get_qrcode", "/canvas-api/api/register2",
                 "/canvas-api/api/create_wx_order_info", "/canvas-api/api/get_order_info",
                 "/canvas-api/api/reset_api_token", "/canvas-api/api/v3/keys",
                 "/canvas-api/api/v3/account/pay", "/canvas-api/api/admin/get_all_tickets"]:
        assert client.post(path, json={}).status_code == 403, path


def test_path_traversal_is_rejected(client):
    assert client.get("/canvas-api/..%2F..%2Fetc%2Fpasswd").status_code in (403, 404)


def test_create_app_still_compiles():
    """防止又把静态挂载插进多行语句里：create_app.py 必须能编译。"""
    import py_compile

    root = Path(__file__).resolve().parents[2]
    py_compile.compile(str(root / "backend" / "app" / "create_app.py"), doraise=True)


def test_projects_are_ours_not_apiz(client, monkeypatch):
    """作品/项目走我们自己的库：创建 -> 我的 -> 公开 -> 存画布 -> 读画布。"""
    def boom(*args, **kwargs):
        raise AssertionError("项目接口不该再去打 apiz")

    monkeypatch.setattr(canvas_proxy.httpx, "AsyncClient", boom)

    created = client.post("/canvas-api/api/v1/projects/", json={"name": "我的第一个作品", "is_public": True})
    assert created.status_code == 200, created.text
    project = created.json()["project"]
    assert project["name"] == "我的第一个作品"
    uid = project["uuid"]

    mine = client.post("/canvas-api/api/v1/projects/my?skip=0&limit=20", json={})
    assert [p["uuid"] for p in mine.json()["projects"]] == [uid]

    public = client.post("/canvas-api/api/v1/projects/public?skip=0&limit=20", json={})
    assert [p["uuid"] for p in public.json()["projects"]] == [uid]

    saved = client.post(f"/canvas-api/api/v1/projects/{uid}/canvas",
                        json={"snapshot": {"nodes": [{"id": "n1"}], "edges": []}})
    assert saved.status_code == 200
    loaded = client.post(f"/canvas-api/api/v1/projects/{uid}/canvas/load", json={})
    assert loaded.json()["snapshot"]["nodes"] == [{"id": "n1"}]

    detail = client.post(f"/canvas-api/api/v1/projects/{uid}", json={})
    assert detail.json()["project"]["is_public"] is True

    deleted = client.post(f"/canvas-api/api/v1/projects/{uid}/delete", json={})
    assert deleted.status_code == 200
    assert client.post("/canvas-api/api/v1/projects/my", json={}).json()["projects"] == []


def test_assets_upload_and_media_are_ours(client, monkeypatch):
    """资产：上传 -> 入库 -> 列表 -> 按 URL 取回；同样不打 apiz。"""
    def boom(*args, **kwargs):
        raise AssertionError("资产接口不该再去打 apiz")

    monkeypatch.setattr(canvas_proxy.httpx, "AsyncClient", boom)

    upload = client.post("/canvas-api/api/upload?name=pic.png", content=b"hello-canvas-bytes",
                         headers={"Content-Type": "application/octet-stream"})
    assert upload.status_code == 200, upload.text
    url = upload.json()["url"]
    assert url.endswith(".png") or "/canvas-api/media/" in url

    listing = client.post("/canvas-api/api/get_file_list", json={"page": 1, "page_size": 30})
    items = listing.json()["list"]
    assert len(items) == 1 and items[0]["file_url"] == url

    rel = url.split("/canvas-api/media/", 1)[1]
    media = client.get(f"/canvas-api/media/{rel}")
    assert media.status_code == 200
    assert media.content == b"hello-canvas-bytes"
    assert client.get("/canvas-api/media/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_task_lists_and_templates_are_empty_not_errors(client, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("这些不该再去打 apiz")

    monkeypatch.setattr(canvas_proxy.httpx, "AsyncClient", boom)
    for path in ["/canvas-api/api/fal/tasks/list", "/canvas-api/api/get_draft_template",
                 "/canvas-api/api/v3/points-campaigns/active"]:
        resp = client.post(path, json={})
        assert resp.status_code == 200 and resp.json()["code"] == 200, path


def test_public_templates_are_synced_into_our_library(client, monkeypatch):
    """首页模板：用速推 key 从 apiz 拉公开作品 -> 入我们自己的库 -> 首页读我们的库。"""

    async def fake_apiz(method, path, body=None, **kwargs):
        return {"total": 1, "projects": [
            {"uuid": "tpl-1", "name": "官方模板一", "description": "d", "is_public": True,
             "thumbnail_url": "https://cdn.example/tpl1.png", "canvas_url": "https://tos.example/tpl1.json", "sort": 9999},
        ]}

    monkeypatch.setattr(canvas_hub, "apiz_json", fake_apiz)
    monkeypatch.setattr(canvas_hub, "_template_sync_at", 0.0)

    resp = client.post("/canvas-api/api/v1/projects/public?skip=0&limit=20", json={})
    assert resp.status_code == 200, resp.text
    projects = resp.json()["projects"]
    assert [p["uuid"] for p in projects] == ["tpl-1"]
    assert projects[0]["thumbnail_url"] == "https://cdn.example/tpl1.png"
    assert projects[0]["is_template"] is True

    # 第二次不再重复同步（TTL 内），但库里的数据照旧能读到
    monkeypatch.setattr(canvas_hub, "apiz_json", None)
    again = client.post("/canvas-api/api/v1/projects/public?skip=0&limit=20", json={})
    assert [p["uuid"] for p in again.json()["projects"]] == ["tpl-1"]
