from __future__ import annotations

import sys
import types

import pytest


@pytest.mark.asyncio
async def test_sutui_generate_stops_when_pre_deduct_has_no_charge(monkeypatch):
    from mcp import http_server

    monkeypatch.setattr(
        http_server,
        "_load_capability_catalog",
        lambda: {
            "video.generate": {
                "upstream": "sutui",
                "upstream_tool": "generate",
                "enabled": True,
            }
        },
    )
    monkeypatch.setattr(http_server, "_load_upstream_urls", lambda: {"sutui": "https://sutui.test/mcp"})
    monkeypatch.setattr(http_server, "resolve_brand_mark_for_request", lambda _auth: "bihuo")
    monkeypatch.setattr(http_server, "sutui_token_ref_from_secret", lambda _secret: "token-ref")

    async def _allowed(_token):
        return None

    async def _not_admin(_token):
        return False

    async def _server_token(*, brand_mark):
        return "sutui-secret", brand_mark

    upstream_calls = []

    async def _call_upstream(*args, **kwargs):
        upstream_calls.append((args, kwargs))
        return {"ok": True}

    class _PreDeductZeroResponse:
        status_code = 200
        content = b"{}"
        text = "{}"

        def json(self):
            return {"credits_charged": 0}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _PreDeductZeroResponse()

    monkeypatch.setattr(http_server, "_fetch_user_allowed_capability_ids", _allowed)
    monkeypatch.setattr(http_server, "_fetch_is_skill_store_admin", _not_admin)
    monkeypatch.setattr(http_server, "next_sutui_server_token_with_pool", _server_token)
    monkeypatch.setattr(http_server, "_call_upstream_mcp_tool", _call_upstream)
    monkeypatch.setattr(http_server.httpx, "AsyncClient", _FakeAsyncClient)

    content, is_error = await http_server._call_tool(
        "invoke_capability",
        {
            "capability_id": "video.generate",
            "payload": {
                "prompt": "test video",
                "model": "xai/grok-imagine-video/text-to-video",
                "duration": 5,
            },
        },
        token="user-token",
        request=None,
    )

    assert is_error is True
    assert content and content[0]["type"] == "text"
    assert upstream_calls == []


@pytest.mark.asyncio
async def test_comfly_generate_stops_when_pre_deduct_has_no_charge(monkeypatch):
    from mcp import http_server

    monkeypatch.setattr(
        http_server,
        "_load_capability_catalog",
        lambda: {
            "video.generate": {
                "upstream": "sutui",
                "upstream_tool": "generate",
                "enabled": True,
            }
        },
    )
    monkeypatch.setattr(http_server, "_load_upstream_urls", lambda: {"sutui": "https://sutui.test/mcp"})
    monkeypatch.setattr(http_server, "resolve_brand_mark_for_request", lambda _auth: "bihuo")
    monkeypatch.setattr(http_server, "sutui_token_ref_from_secret", lambda _secret: "token-ref")

    async def _allowed(_token):
        return None

    async def _not_admin(_token):
        return False

    async def _server_token(*, brand_mark):
        return "sutui-secret", brand_mark

    async def _unexpected_comfly_call(*args, **kwargs):
        raise AssertionError("Comfly upstream should not be called without positive pre-deduct")

    fake_comfly = types.SimpleNamespace(
        should_route_to_comfly=lambda *args, **kwargs: True,
        is_comfly_task=lambda _task_id: False,
        is_comfly_configured=lambda: True,
        estimate_comfly_credits=lambda *args, **kwargs: 200,
        call_comfly_image_generate=_unexpected_comfly_call,
        call_comfly_video_generate=_unexpected_comfly_call,
        call_comfly_task_query=_unexpected_comfly_call,
        call_comfly_chat_completions=_unexpected_comfly_call,
        format_comfly_image_response_as_sutui=lambda resp: resp,
        format_comfly_video_response_as_sutui=lambda resp, fallback_task_id=None: resp,
        register_comfly_task=lambda *args, **kwargs: None,
        get_comfly_task_token_group=lambda _task_id: "",
        get_comfly_task_api_format=lambda _task_id: "",
        _get_model_token_group=lambda _model: "",
    )

    upstream_calls = []

    async def _call_upstream(*args, **kwargs):
        upstream_calls.append((args, kwargs))
        return {"ok": True}

    class _PreDeductZeroResponse:
        status_code = 200
        content = b"{}"
        text = "{}"

        def json(self):
            return {"credits_charged": 0}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _PreDeductZeroResponse()

    monkeypatch.setitem(sys.modules, "comfly_upstream", fake_comfly)
    monkeypatch.setattr(http_server, "_fetch_user_allowed_capability_ids", _allowed)
    monkeypatch.setattr(http_server, "_fetch_is_skill_store_admin", _not_admin)
    monkeypatch.setattr(http_server, "next_sutui_server_token_with_pool", _server_token)
    monkeypatch.setattr(http_server, "_call_upstream_mcp_tool", _call_upstream)
    monkeypatch.setattr(http_server.httpx, "AsyncClient", _FakeAsyncClient)

    content, is_error = await http_server._call_tool(
        "invoke_capability",
        {
            "capability_id": "video.generate",
            "payload": {
                "prompt": "test video",
                "model": "veo3.1",
                "_prefer_comfly": True,
                "duration": 5,
            },
        },
        token="user-token",
        request=None,
    )

    assert is_error is True
    assert content and content[0]["type"] == "text"
    assert upstream_calls == []
