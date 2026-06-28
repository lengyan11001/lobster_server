from __future__ import annotations

import sys
import types

import pytest


class _Request:
    headers = {"Authorization": "Bearer user-token"}


class _JsonResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}"
        self.text = "{}"

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_task_get_result_rejects_local_pipeline_job_id(monkeypatch):
    from mcp import http_server

    monkeypatch.setattr(
        http_server,
        "_load_capability_catalog",
        lambda: {
            "task.get_result": {
                "upstream": "sutui",
                "upstream_tool": "get_result",
                "enabled": True,
            }
        },
    )
    monkeypatch.setattr(http_server, "_load_upstream_urls", lambda: {"sutui": "https://sutui.test/mcp"})
    monkeypatch.setattr(http_server, "resolve_brand_mark_for_request", lambda _auth: "bihuo")

    async def _allowed(*_args, **_kwargs):
        return None

    async def _not_admin(_token):
        return False

    async def _server_token(*, brand_mark):
        return "sutui-secret", brand_mark

    upstream_calls = []

    async def _call_upstream(*args, **kwargs):
        upstream_calls.append((args, kwargs))
        return {"unexpected": True}

    monkeypatch.setattr(http_server, "_fetch_user_allowed_capability_ids", _allowed)
    monkeypatch.setattr(http_server, "_fetch_is_skill_store_admin", _not_admin)
    monkeypatch.setattr(http_server, "next_sutui_server_token_with_pool", _server_token)
    monkeypatch.setattr(http_server, "_call_upstream_mcp_tool", _call_upstream)

    items, is_err = await http_server._call_tool(
        "invoke_capability",
        {
            "capability_id": "task.get_result",
            "payload": {"task_id": "86cbfb322b3a481bbe29be7574ba7b12"},
        },
        token="user-token",
        request=_Request(),
    )

    assert is_err is True
    assert "pipeline job_id" in items[0]["text"]
    assert upstream_calls == []


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

    async def _allowed(*_args, **_kwargs):
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
async def test_gpt_image_2_defaults_high_quality_and_uses_server_price(monkeypatch):
    from mcp import http_server

    monkeypatch.setattr(
        http_server,
        "_load_capability_catalog",
        lambda: {
            "image.generate": {
                "upstream": "sutui",
                "upstream_tool": "generate",
                "enabled": True,
            }
        },
    )
    monkeypatch.setattr(http_server, "_load_upstream_urls", lambda: {"sutui": "https://sutui.test/mcp"})
    monkeypatch.setattr(http_server, "resolve_brand_mark_for_request", lambda _auth: "bihuo")
    monkeypatch.setattr(http_server, "sutui_token_ref_from_secret", lambda _secret: "token-ref")

    async def _allowed(*_args, **_kwargs):
        return None

    async def _not_admin(_token):
        return False

    async def _server_token(*, brand_mark):
        return "sutui-secret", brand_mark

    upstream_payloads = []

    async def _call_upstream(_url, _tool, payload, **_kwargs):
        upstream_payloads.append(payload)
        return {"task_id": "img-task-1", "price": 32}

    record_bodies = []

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *args, **kwargs):
            body = kwargs.get("json") or {}
            if str(url).endswith("/capabilities/pre-deduct"):
                return _JsonResponse(200, {"credits_charged": 80, "billing_rule": "gpt_image_2_flat"})
            if str(url).endswith("/capabilities/record-call"):
                record_bodies.append(body)
                return _JsonResponse(200, {"credits_charged": 80})
            raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr(http_server, "_fetch_user_allowed_capability_ids", _allowed)
    monkeypatch.setattr(http_server, "_fetch_is_skill_store_admin", _not_admin)
    monkeypatch.setattr(http_server, "next_sutui_server_token_with_pool", _server_token)
    monkeypatch.setattr(http_server, "_call_upstream_mcp_tool", _call_upstream)
    monkeypatch.setattr(http_server.httpx, "AsyncClient", _FakeAsyncClient)

    content, is_error = await http_server._call_tool(
        "invoke_capability",
        {
            "capability_id": "image.generate",
            "payload": {
                "prompt": "test image",
                "model": "openai/gpt-image-2",
            },
        },
        token="user-token",
        request=_Request(),
    )

    assert is_error is False
    assert upstream_payloads[0]["quality"] == "high"
    assert upstream_payloads[0]["resolution"] == "4K"
    assert upstream_payloads[0]["output_format"] == "png"
    assert upstream_payloads[0]["image_size"] == "1:1"
    assert upstream_payloads[0]["num_images"] == 1
    assert record_bodies
    assert record_bodies[0]["credits_charged"] == 80.0
    assert record_bodies[0]["credits_pre_deducted"] == 80.0
    assert "credits_final" not in record_bodies[0]
    assert content and content[0]["type"] == "text"
    compact_text = content[0]["text"].replace(" ", "")
    assert '"credits_used":80.0' in compact_text
    assert '"price":80.0' in compact_text
    assert '"upstream_credits_used":80.0' not in compact_text


def test_gpt_image_2_high_quality_intent_overrides_stale_low():
    from mcp import http_server

    payload = http_server._normalize_image_generate_payload({
        "prompt": "test image",
        "model": "openai/gpt-image-2",
        "image_size": {"width": 1088, "height": 608},
        "quality": "low",
        "quality_preset": "highest",
        "render_quality": "production",
        "output_quality": 100,
        "output_format": "png",
    })

    assert payload["image_size"] == "16:9"
    assert payload["quality"] == "high"
    assert payload["resolution"] == "4K"
    assert payload["output_format"] == "png"
    assert payload["num_images"] == 1


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

    async def _allowed(*_args, **_kwargs):
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
