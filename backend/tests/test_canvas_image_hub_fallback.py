"""画布图片生成：APIZ 图片通道不可用时兜底走图片共用台（2026-11-23）。"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import canvas_hub, canvas_proxy, comfly_proxy
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


def _patch_apiz(monkeypatch, response: FakeResponse) -> None:
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, content=None, headers=None, params=None):
            return response

    monkeypatch.setattr(canvas_proxy.httpx, "AsyncClient", FakeClient)

    async def fake_headers():
        return {"Authorization": "Bearer sk-server-key", "Accept": "application/json"}

    monkeypatch.setattr(canvas_proxy, "_apiz_headers", fake_headers)


@pytest.fixture
def client(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///" + str(tmp_path / "hub.db").replace("\\", "/"))
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(canvas_hub, "_tables_ready", False)
    monkeypatch.setattr(canvas_hub, "UPLOAD_DIR", tmp_path / "uploads")
    with session_factory() as db:
        canvas_hub.ensure_tables(db)

    app = FastAPI()
    app.include_router(canvas_hub.router)
    app.include_router(canvas_proxy.router)
    app.dependency_overrides[get_current_user] = lambda: FakeUser()
    app.dependency_overrides[get_db] = lambda: session_factory()

    async def _passthrough(model, params):
        return params

    monkeypatch.setattr(canvas_proxy, "prepare_params", _passthrough)
    monkeypatch.setattr(canvas_proxy, "estimate_our_price", lambda *a, **k: Decimal("6"))
    monkeypatch.setattr(canvas_proxy, "pre_deduct_canvas", lambda *a, **k: Decimal("6"))
    return TestClient(app)


QUOTA_BODY = json.dumps({
    "error": {
        "message": "quota exhausted",
        "error_code": "insufficient_user_quota",
        "error_status_code": 403,
        "error_type": "upstream_unavailable",
    }
}).encode()


def test_image_create_falls_back_to_shared_image_hub(client, monkeypatch):
    _patch_apiz(monkeypatch, FakeResponse(403, QUOTA_BODY))
    calls: dict = {}

    async def fake_execute(**kwargs):
        calls.update(kwargs)
        return {
            "created": 1,
            "data": [{"url": "https://cdn.example.com/fallback.png"}],
            "_lobster_fallback": {"used_model": "gpt-image-2-gaisc"},
        }

    monkeypatch.setattr(comfly_proxy, "_execute_image_generation_request", fake_execute)

    resp = client.post(
        "/canvas-api/api/v3/tasks/create",
        json={"model": "openai/gpt-image-2", "params": {"prompt": "一只猫", "size": "1024x1024"}},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "completed"
    assert data["task_id"].startswith("cf-")

    assert calls["model"] == "gpt-image-2", "画布图片模型要换成共用台注册的请求模型"
    assert calls["internal_fallback"] is True, "画布已按自己的价预扣，共用台这一跳不能再扣用户钱"
    assert calls["persist_assets"] is False, "产物入库由画布侧记账，避免重复内容记录"
    assert calls["body"]["prompt"] == "一只猫"
    assert calls["body"]["size"] == "1024x1024"

    query = client.post("/canvas-api/api/v3/tasks/query", json={"task_id": data["task_id"]})
    assert query.status_code == 200
    payload = query.json()["data"]
    assert payload["status"] == "completed"
    assert payload["output"]["image_url"] == "https://cdn.example.com/fallback.png"
    assert payload["output_params"]["output"] == "https://cdn.example.com/fallback.png"


def test_reference_image_is_passed_to_shared_hub(client, monkeypatch):
    _patch_apiz(monkeypatch, FakeResponse(503, b'{"error":"upstream_unavailable"}'))
    calls: dict = {}

    async def fake_execute(**kwargs):
        calls.update(kwargs)
        return {"data": [{"url": "https://cdn.example.com/edit.png"}]}

    monkeypatch.setattr(comfly_proxy, "_execute_image_generation_request", fake_execute)

    resp = client.post(
        "/canvas-api/api/v3/tasks/create",
        json={
            "model": "openai/gpt-image-2/edit",
            "params": {
                "prompt": "换成水彩风",
                "content": [
                    {"type": "text", "text": "换成水彩风"},
                    {"type": "image_url", "image_url": {"url": "https://cdn.example.com/src.png"}},
                ],
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert calls["model"] == "gpt-image-2"
    assert calls["body"]["image_urls"] == ["https://cdn.example.com/src.png"]
    assert calls["body"]["image"] == "https://cdn.example.com/src.png"


def test_bad_parameter_error_does_not_fall_back(client, monkeypatch):
    _patch_apiz(monkeypatch, FakeResponse(400, b'{"error":"bad param"}'))
    calls: list = []

    async def fake_execute(**kwargs):
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(comfly_proxy, "_execute_image_generation_request", fake_execute)

    resp = client.post(
        "/canvas-api/api/v3/tasks/create",
        json={"model": "openai/gpt-image-2", "params": {"prompt": "x"}},
    )
    assert resp.status_code == 400
    assert calls == [], "参数错误不是通道问题，不该换共用台重试"


def test_video_model_never_goes_to_shared_image_hub(client, monkeypatch):
    _patch_apiz(monkeypatch, FakeResponse(503, b'{"error":"upstream_unavailable"}'))
    calls: list = []

    async def fake_execute(**kwargs):
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(comfly_proxy, "_execute_image_generation_request", fake_execute)

    resp = client.post(
        "/canvas-api/api/v3/tasks/create",
        json={"model": "minimax/h3", "params": {"prompt": "x"}},
    )
    assert resp.status_code == 503
    assert calls == []


def test_internal_fallback_never_touches_user_billing(monkeypatch):
    """共用台这一跳是画布的兜底，画布已经扣过钱：预扣/退款一次都不能发生。"""
    billing_calls: list = []
    monkeypatch.setattr(comfly_proxy, "_openai_official_image_first_for_user", lambda uid: False)
    monkeypatch.setattr(comfly_proxy, "_image_generation_channel_available", lambda *a, **k: True)
    monkeypatch.setattr(comfly_proxy, "_mark_image_generation_channel_failure", lambda *a, **k: None)
    monkeypatch.setattr(comfly_proxy, "_do_pre_deduct_by_user_id",
                        lambda *a, **k: billing_calls.append("pre") or Decimal("1"))
    monkeypatch.setattr(comfly_proxy, "_do_full_refund_by_user_id",
                        lambda *a, **k: billing_calls.append("refund"))
    monkeypatch.setattr(comfly_proxy, "_do_settle_by_user_id",
                        lambda *a, **k: billing_calls.append("settle"))
    monkeypatch.setattr(comfly_proxy, "_queue_generated_image_asset_persistence", lambda *a, **k: False)
    monkeypatch.setattr(comfly_proxy, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(comfly_proxy, "log_model_usage_event", lambda *a, **k: None)

    async def fake_image_request(*args, **kwargs):
        return {"created": 1, "data": [{"url": "https://cdn.example.com/x.png"}]}

    for name in ("_comfly_request", "_sutui_image_request", "_openmind_image_request",
                 "_openai_official_image_request"):
        monkeypatch.setattr(comfly_proxy, name, fake_image_request)

    resp = asyncio.run(comfly_proxy._execute_image_generation_request(
        request_user_id=42,
        billing_user_id=42,
        model="gpt-image-2",
        body={"model": "gpt-image-2", "prompt": "cat"},
        persist_assets=False,
        internal_fallback=True,
    ))
    assert resp["data"][0]["url"] == "https://cdn.example.com/x.png"
    assert billing_calls == [], billing_calls
