"""画布代理：登录态鉴权、白名单/黑名单、占位 key、转发行为。

2026-09-29：入口不再要单独的 CANVAS_SELFTEST_KEY，改成 online 账号 JWT；
上游 apiz 继续用服务器配置的速推 key（这里 monkeypatch 成 sk-server-key 验证注入）。
"""
from __future__ import annotations

import json
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
    app.include_router(canvas_hub.router)
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
    """生成类中转：带服务器速推 key、状态码原样透传。"""
    seen = patch_upstream(monkeypatch, FakeResponse(status_code=402, content=b'{"detail": "insufficient credits"}'))
    resp = client.post("/canvas-api/api/create_video_task", json={"model": "x"})
    assert resp.status_code == 402
    assert resp.text == '{"detail": "insufficient credits"}'
    assert seen["method"] == "POST"
    assert seen["url"] == "https://api.apiz.ai/api/create_video_task"
    assert seen["headers"]["Authorization"] == "Bearer sk-server-key"


def test_task_create_body_is_forwarded_with_server_key(client, monkeypatch):
    from decimal import Decimal

    monkeypatch.setattr(
        "backend.app.services.sutui_billing_gate.assert_pricing_pre_deduct_allows_upstream_or_http",
        lambda *a, **k: Decimal("1"),
    )
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
    for path in ["/canvas-api/api/create_video_task", "/canvas-api/api/v2/tasks/create", "/canvas-api/api/fal/tasks/create"]:
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

    # 画布上传只做临时素材：不进素材库（列表为空），但文件本体必须取得到
    listing = client.post("/canvas-api/api/get_file_list", json={"page": 1, "page_size": 30})
    assert listing.json()["list"] == []

    rel = url.split("/canvas-media/", 1)[1] if "/canvas-media/" in url else url.split("/canvas-api/media/", 1)[1]
    media = client.get(f"/canvas-media/{rel}")
    assert media.status_code == 200
    assert media.content == b"hello-canvas-bytes"
    assert client.get("/canvas-api/media/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_task_lists_come_from_our_library(client, monkeypatch):
    """任务记录/最近任务：读我们自己的库（下单时记的），不再空表、也不打 apiz。"""

    def boom(*args, **kwargs):
        raise AssertionError("任务列表不该请求 apiz")

    monkeypatch.setattr(canvas_proxy.httpx, "AsyncClient", boom)
    for path in ["/canvas-api/api/fal/tasks/list", "/canvas-api/api/task_list"]:
        resp = client.post(path, json={"page": 1, "page_size": 30})
        assert resp.status_code == 200 and resp.json()["code"] == 200, path
        assert "list" in resp.json()
    resp = client.post("/canvas-api/api/get_draft_template", json={"page": 1, "page_size": 12})
    assert resp.status_code == 200 and resp.json()["code"] == 200


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


def test_draft_templates_are_stored_in_our_library(client, monkeypatch):
    """Coze 草稿模板：用速推 key 拉一次 -> 入我们的库 -> 之后读我们的库。"""

    async def fake_apiz(method, path, body=None, **kwargs):
        return {"code": 200, "data": [
            {"id": 164, "title": "一键生成儿童绘本视频", "img_url": "https://img/1.png",
             "video_url": "https://v/1.mp4", "url": "https://www.coze.cn/s/abc/", "user_name": "coze",
             "sort": 999999},
        ]}

    monkeypatch.setattr(canvas_hub, "apiz_json", fake_apiz)
    monkeypatch.setattr(canvas_hub, "_draft_sync_at", 0.0)

    first = client.post("/canvas-api/api/get_draft_template", json={"page": 1, "page_size": 12})
    assert first.status_code == 200, first.text
    rows = first.json()["data"]
    assert rows[0]["title"] == "一键生成儿童绘本视频"
    assert rows[0]["img_url"] == "https://img/1.png"

    monkeypatch.setattr(canvas_hub, "apiz_json", None)
    again = client.post("/canvas-api/api/get_draft_template", json={"page": 1, "page_size": 12})
    assert again.json()["data"][0]["url"] == "https://www.coze.cn/s/abc/"


def test_generation_precheck_blocks_when_no_credits(client, monkeypatch):
    """生成类：余额不足/无定价时先拦下来，不能白花服务器的 key。"""
    from fastapi import HTTPException

    def deny(*args, **kwargs):
        raise HTTPException(status_code=402, detail="积分不足")

    monkeypatch.setattr(
        "backend.app.services.sutui_billing_gate.assert_pricing_pre_deduct_allows_upstream_or_http", deny
    )

    def boom(*args, **kwargs):
        raise AssertionError("预检没过就不该调用上游")

    monkeypatch.setattr(canvas_proxy.httpx, "AsyncClient", boom)
    resp = client.post("/canvas-api/api/create_video_task", json={"model": "minimax/h3", "prompt": "x"})
    assert resp.status_code == 402


def test_generation_relay_goes_to_apiz_with_server_key(client, monkeypatch):
    from decimal import Decimal

    monkeypatch.setattr(
        "backend.app.services.sutui_billing_gate.assert_pricing_pre_deduct_allows_upstream_or_http",
        lambda *a, **k: Decimal("1"),
    )
    seen = patch_upstream(monkeypatch, FakeResponse(content=b'{"code": 200, "data": {"task_id": "t-9"}}'))
    resp = client.post("/canvas-api/api/create_video_task", json={"model": "minimax/h3"})
    assert resp.status_code == 200
    assert seen["url"] == "https://api.apiz.ai/api/create_video_task"
    assert seen["headers"]["Authorization"] == "Bearer sk-server-key"


def test_upload_voucher_points_at_our_server(client, monkeypatch):
    """上传凭证必须是「相对上传地址 + 我们服务器的公开 URL」，不能指到外站（否则浏览器 PUT 过去没登录态 -> 没授权）。"""
    monkeypatch.setattr(canvas_proxy.httpx, "AsyncClient", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不许打 apiz")))
    token = client.post("/canvas-api/api/get_cf_r2_token", json={"file_name": "pic.png", "content_type": "image/png"})
    assert token.status_code == 200, token.text
    data = token.json()["data"]
    assert data["upload_url"].startswith("/canvas-api/api/upload?key=")
    assert data["public_url"].endswith(data["file_key"])
    assert data["public_url"].startswith("http")

    put = client.put(data["upload_url"], content=b"png-bytes", headers={"Content-Type": "image/png"})
    assert put.status_code == 200, put.text
    assert put.json()["url"] == data["public_url"]

    media = client.get(data["public_url"].split("/canvas-media/", 1)[1].join(["/canvas-media/", ""]))
    assert media.status_code == 200 and media.content == b"png-bytes"


def test_quote_is_answered_locally(client, monkeypatch):
    """报价由我们本地算：不发 apiz，返回里就是我们自己的价。"""

    def boom(*args, **kwargs):
        raise AssertionError("报价不该请求 apiz")

    monkeypatch.setattr(canvas_proxy.httpx, "AsyncClient", boom)
    resp = client.post("/canvas-api/api/v3/tasks/quote", json={"model": "openai/gpt-image-2"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 200
    assert body["credits"] == body["data"]["credits"]
    assert canvas_proxy._is_quote_path("api/v3/tasks/quote") is True
    assert canvas_proxy._is_quote_path("api/v3/tasks/create") is False
    assert canvas_proxy._path_relayable("api/v3/mcp/models") is False
    assert canvas_proxy._path_relayable("api/v3/tasks/create") is True


def test_clone_keeps_full_snapshot(client, monkeypatch):
    """克隆必须原样带上模板快照（多组控件不能丢）；不能出现「字符串套 JSON」。"""
    def boom(*args, **kwargs):
        raise AssertionError("克隆不该再打 apiz")

    monkeypatch.setattr(canvas_proxy.httpx, "AsyncClient", boom)

    snapshot = {
        "nodes": [{"id": "n1", "type": "promptInput"}, {"id": "n2", "type": "videoTask"},
                  {"id": "n3", "type": "sora2Video"}],
        "edges": [{"id": "e1", "source": "n1", "target": "n3"}],
    }
    created = client.post("/canvas-api/api/v1/projects/", json={"name": "多组模板", "is_public": True})
    uid = created.json()["project"]["uuid"]
    saved = client.post(f"/canvas-api/api/v1/projects/{uid}/canvas", json={"snapshot": snapshot})
    assert saved.status_code == 200, saved.text

    cloned = client.post(f"/canvas-api/api/v1/projects/{uid}/clone", json={})
    assert cloned.status_code == 200, cloned.text
    copy_uuid = cloned.json()["project"]["uuid"]

    loaded = client.post(f"/canvas-api/api/v1/projects/{copy_uuid}/canvas/load", json={})
    body = loaded.json()["snapshot"]
    assert isinstance(body, dict), "副本 snapshot 不能是字符串（画布会读不出 nodes）"
    assert body["nodes"] == snapshot["nodes"]
    assert body["edges"] == snapshot["edges"]


def test_save_snapshot_accepts_json_text(client):
    """调用方塞 JSON 文本也只能存一层（幂等），避免把画布存成字符串套 JSON。"""
    created = client.post("/canvas-api/api/v1/projects/", json={"name": "文本快照"})
    uid = created.json()["project"]["uuid"]
    saved = client.post(f"/canvas-api/api/v1/projects/{uid}/canvas",
                        json={"snapshot": json.dumps({"nodes": [{"id": "n9"}], "edges": []})})
    assert saved.status_code == 200, saved.text
    loaded = client.post(f"/canvas-api/api/v1/projects/{uid}/canvas/load", json={})
    assert loaded.json()["snapshot"]["nodes"] == [{"id": "n9"}]


def test_clone_pulls_missing_template_snapshot(client, monkeypatch):
    """模板没打开过（库里快照为空）时，克隆要先按 canvas_url 拉快照，副本不能是空的。"""
    import httpx

    canvas = {"nodes": [{"id": "g1", "type": "promptInput"}, {"id": "g2", "type": "videoTask"}],
              "edges": [{"id": "e1"}]}

    async def fake_apiz(method, path, body=None, **kwargs):
        return {"projects": [{"uuid": "tpl-clone-1", "name": "多组模板", "is_public": True,
                              "canvas_url": "https://example.invalid/canvas/tpl-clone-1.json"}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            assert "tpl-clone-1.json" in url

            class Resp:
                status_code = 200

                def json(self):
                    return canvas

            return Resp()

        async def request(self, *args, **kwargs):
            raise AssertionError("克隆/加载不该转发到 apiz")

    monkeypatch.setattr(canvas_hub, "apiz_json", fake_apiz)
    monkeypatch.setattr(canvas_hub, "_template_sync_at", 0.0)
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    public = client.post("/canvas-api/api/v1/projects/public?skip=0&limit=20", json={})
    tpl = public.json()["projects"][0]
    assert tpl["name"] == "多组模板"

    cloned = client.post(f"/canvas-api/api/v1/projects/{tpl['uuid']}/clone", json={})
    assert cloned.status_code == 200, cloned.text
    copy_uuid = cloned.json()["project"]["uuid"]
    loaded = client.post(f"/canvas-api/api/v1/projects/{copy_uuid}/canvas/load", json={})
    assert loaded.json()["snapshot"]["nodes"] == canvas["nodes"]


def test_own_project_exposes_canvas_url_once_it_has_canvas(client):
    """画布前端只在 canvas_url 非空时才 load 快照；自有作品有内容后必须给非空 canvas_url。"""
    created = client.post("/canvas-api/api/v1/projects/", json={"name": "多组作品"})
    uid = created.json()["project"]["uuid"]

    before = client.post(f"/canvas-api/api/v1/projects/{uid}", json={}).json()["project"]
    assert before["canvas_url"] == "", "新项目还没有画布，canvas_url 应为空（前端会用默认画布）"

    snapshot = {"nodes": [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}, {"id": "n4"}],
                "edges": [{"id": "e1"}]}
    assert client.post(f"/canvas-api/api/v1/projects/{uid}/canvas",
                       json={"snapshot": snapshot}).status_code == 200

    after = client.post(f"/canvas-api/api/v1/projects/{uid}", json={}).json()["project"]
    assert after["canvas_url"], "有画布之后 canvas_url 必须非空，否则前端不会去 load（只会渲染默认一组）"

    listed = client.post("/canvas-api/api/v1/projects/my", json={}).json()["projects"][0]
    assert listed["canvas_url"]

    loaded = client.post(f"/canvas-api/api/v1/projects/{uid}/canvas/load", json={})
    assert [n["id"] for n in loaded.json()["snapshot"]["nodes"]] == ["n1", "n2", "n3", "n4"]


def test_task_info_404_is_swallowed(client, monkeypatch):
    """画布轮询上游不存在的 task_id（本地合成任务）时，不能把 404「任务不存在」透给前端。"""
    patch_upstream(
        monkeypatch,
        FakeResponse(status_code=404, content='{"detail":"任务不存在"}'.encode("utf-8")),
    )
    res = client.post("/canvas-api/api/fal/tasks/info", json={"task_id": "videoConcat-1768202287843"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == 200 and body["data"] is None

    # v2 通道同样处理
    res2 = client.post("/canvas-api/api/v2/tasks/info", json={"task_id": "videoConcat-1768202287843"})
    assert res2.status_code == 200 and res2.json()["data"] is None


def test_other_404_still_reaches_client(client, monkeypatch):
    """别的路径的 404 该透还是透，别被这条规则一起吞掉。"""
    patch_upstream(
        monkeypatch,
        FakeResponse(status_code=404, content='{"detail":"作品不存在"}'.encode("utf-8")),
    )
    res = client.post("/canvas-api/api/v1/projects/not-exist/canvas/load", json={})
    assert res.status_code == 404


def test_task_query_settles_without_undefined_uid(client, monkeypatch):
    """回归 2026-10-05：轮询 tasks/query 拿到产物后登记内容时用了未定义的 uid。

    线上表现（diag/本地 backend.log）：轮询一直 200，等出图那一刻变 500，
    画布前端弹「暂时无法读取任务状态，请稍后重试」；服务端 journal 里是
    canvas_proxy.py:1065 NameError: name 'uid' is not defined。
    """
    body = {
        "code": 200,
        "data": {
            "task_id": "task-1",
            "status": "completed",
            "output": {"images": [{"url": "https://cdn.example.com/done.png"}]},
        },
    }
    patch_upstream(monkeypatch, FakeResponse(content=json.dumps(body).encode("utf-8")))

    resp = client.post("/canvas-api/api/v3/tasks/query", json={"task_id": "task-1"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["task_id"] == "task-1"


def test_canvas_proxy_defines_uid_for_record_step():
    """静态兜底：canvas_proxy 里用到 uid 的地方必须在函数内先赋值（别再引用 _hub_route 的局部变量）。"""
    import inspect

    source = inspect.getsource(canvas_proxy.canvas_proxy)
    assert "uid = int(getattr(user" in source, source[:400]
