import json
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import comfly_proxy


def test_image_edit_defaults_to_url_response_format(monkeypatch):
    captured = {}

    async def _fake_multipart_request(url, data, files, headers, timeout):
        captured["url"] = url
        captured["data"] = dict(data)
        captured["files"] = list(files)
        captured["headers"] = dict(headers)
        captured["timeout"] = timeout
        return {"data": [{"url": "https://example.com/out.png"}]}

    monkeypatch.setattr(comfly_proxy, "_check_request_authorized_for_billing", lambda _request: None)
    monkeypatch.setattr(comfly_proxy, "_require_model_entry", lambda _model: {"comfly_model": _model})
    monkeypatch.setattr(
        comfly_proxy,
        "_resolve_proxy_user_ids_from_request",
        lambda _request, map_to_online_user=True: (69, 69),
    )
    monkeypatch.setattr(comfly_proxy, "estimate_comfly_credits", lambda *_args, **_kwargs: 60)
    monkeypatch.setattr(comfly_proxy, "_do_pre_deduct_by_user_id", lambda *_args, **_kwargs: Decimal("4140.4387"))
    monkeypatch.setattr(comfly_proxy, "_do_full_refund_by_user_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(comfly_proxy, "_comfly_auth_headers", lambda _model: {"Authorization": "Bearer upstream"})
    monkeypatch.setattr(comfly_proxy, "_comfly_url", lambda path, _model: f"https://upstream.test{path}")
    monkeypatch.setattr(comfly_proxy, "_comfly_multipart_request", _fake_multipart_request)
    monkeypatch.setattr(comfly_proxy, "_audit", lambda *_args, **_kwargs: None)

    app = FastAPI()
    app.include_router(comfly_proxy.router)
    client = TestClient(app)

    response = client.post(
        "/api/comfly-proxy/v1/images/edits",
        data={"model": "gpt-image-2", "prompt": "x-default", "size": "1080x1920"},
        files={"image": ("reference.png", b"fake-image-bytes", "image/png")},
        headers={"Authorization": "Bearer user"},
    )

    assert response.status_code == 200, response.text
    assert captured["data"]["response_format"] == "url"
    assert captured["data"]["size"] == "1080x1920"
    assert captured["timeout"] == comfly_proxy._TIMEOUT_IMAGE


def test_image_edit_retries_transient_disconnect_before_refund(monkeypatch):
    captured = {"calls": 0, "refunds": 0, "audits": []}

    async def _fake_multipart_request(url, data, files, headers, timeout):
        captured["calls"] += 1
        if captured["calls"] == 1:
            raise RuntimeError("Server disconnected without sending a response.")
        return {"data": [{"url": "https://example.com/out.png"}]}

    async def _no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setenv("COMFLY_IMAGE_EDIT_RETRY_ATTEMPTS", "2")
    monkeypatch.setattr(comfly_proxy.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(comfly_proxy, "_check_request_authorized_for_billing", lambda _request: None)
    monkeypatch.setattr(comfly_proxy, "_require_model_entry", lambda _model: {"comfly_model": _model})
    monkeypatch.setattr(
        comfly_proxy,
        "_resolve_proxy_user_ids_from_request",
        lambda _request, map_to_online_user=True: (69, 69),
    )
    monkeypatch.setattr(comfly_proxy, "estimate_comfly_credits", lambda *_args, **_kwargs: 60)
    monkeypatch.setattr(comfly_proxy, "_do_pre_deduct_by_user_id", lambda *_args, **_kwargs: Decimal("4140.4387"))
    monkeypatch.setattr(
        comfly_proxy,
        "_do_full_refund_by_user_id",
        lambda *_args, **_kwargs: captured.__setitem__("refunds", captured["refunds"] + 1),
    )
    monkeypatch.setattr(comfly_proxy, "_comfly_auth_headers", lambda _model: {"Authorization": "Bearer upstream"})
    monkeypatch.setattr(comfly_proxy, "_comfly_url", lambda path, _model: f"https://upstream.test{path}")
    monkeypatch.setattr(comfly_proxy, "_comfly_multipart_request", _fake_multipart_request)
    monkeypatch.setattr(comfly_proxy, "_audit", lambda event, **kwargs: captured["audits"].append((event, kwargs)))

    app = FastAPI()
    app.include_router(comfly_proxy.router)
    client = TestClient(app)

    response = client.post(
        "/api/comfly-proxy/v1/images/edits",
        data={"model": "gpt-image-2", "prompt": "x-retry", "size": "1080x1920"},
        files={"image": ("reference.png", b"fake-image-bytes", "image/png")},
        headers={"Authorization": "Bearer user"},
    )

    assert response.status_code == 200, response.text
    assert captured["calls"] == 2
    assert captured["refunds"] == 0
    assert any(event == "image_edit_attempt_failed" for event, _ in captured["audits"])


def test_image_edit_reuses_cached_idempotent_result_without_charging(monkeypatch):
    captured = {"pre_deducts": 0, "upstream_calls": 0}
    cached = {"data": [{"url": "https://example.com/cached.png"}]}

    async def _fake_multipart_request(*_args, **_kwargs):
        captured["upstream_calls"] += 1
        return {"data": [{"url": "https://example.com/new.png"}]}

    def _fail_pre_deduct(*_args, **_kwargs):
        captured["pre_deducts"] += 1
        raise AssertionError("cached image edit must not charge again")

    monkeypatch.setattr(comfly_proxy, "_check_request_authorized_for_billing", lambda _request: None)
    monkeypatch.setattr(comfly_proxy, "_require_model_entry", lambda _model: {"comfly_model": _model})
    monkeypatch.setattr(
        comfly_proxy,
        "_resolve_proxy_user_ids_from_request",
        lambda _request, map_to_online_user=True: (69, 69),
    )
    monkeypatch.setattr(comfly_proxy, "_do_pre_deduct_by_user_id", _fail_pre_deduct)
    monkeypatch.setattr(comfly_proxy, "_comfly_multipart_request", _fake_multipart_request)
    monkeypatch.setattr(
        comfly_proxy,
        "cache_get",
        lambda key: json.dumps(cached) if key == "comfly:image_edit:result:69:job123" else None,
    )
    monkeypatch.setattr(comfly_proxy, "_audit", lambda *_args, **_kwargs: None)

    app = FastAPI()
    app.include_router(comfly_proxy.router)
    client = TestClient(app)

    response = client.post(
        "/api/comfly-proxy/v1/images/edits",
        data={"model": "gpt-image-2", "prompt": "x", "client_request_id": "job123"},
        files={"image": ("reference.png", b"fake-image-bytes", "image/png")},
        headers={"Authorization": "Bearer user"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["url"] == "https://example.com/cached.png"
    assert captured == {"pre_deducts": 0, "upstream_calls": 0}


def test_image_edit_refunds_when_upstream_returns_no_image_result(monkeypatch):
    captured = {"refunds": 0}

    async def _fake_multipart_request(*_args, **_kwargs):
        return {"data": []}

    monkeypatch.setattr(comfly_proxy, "_check_request_authorized_for_billing", lambda _request: None)
    monkeypatch.setattr(comfly_proxy, "_require_model_entry", lambda _model: {"comfly_model": _model})
    monkeypatch.setattr(
        comfly_proxy,
        "_resolve_proxy_user_ids_from_request",
        lambda _request, map_to_online_user=True: (69, 69),
    )
    monkeypatch.setattr(comfly_proxy, "estimate_comfly_credits", lambda *_args, **_kwargs: 60)
    monkeypatch.setattr(comfly_proxy, "_do_pre_deduct_by_user_id", lambda *_args, **_kwargs: Decimal("60"))
    monkeypatch.setattr(
        comfly_proxy,
        "_do_full_refund_by_user_id",
        lambda *_args, **_kwargs: captured.__setitem__("refunds", captured["refunds"] + 1),
    )
    monkeypatch.setattr(comfly_proxy, "_comfly_auth_headers", lambda _model: {"Authorization": "Bearer upstream"})
    monkeypatch.setattr(comfly_proxy, "_comfly_url", lambda path, _model: f"https://upstream.test{path}")
    monkeypatch.setattr(comfly_proxy, "_comfly_multipart_request", _fake_multipart_request)
    monkeypatch.setattr(comfly_proxy, "cache_get", lambda _key: None)
    monkeypatch.setattr(comfly_proxy, "cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(comfly_proxy, "cache_set_if_absent", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(comfly_proxy, "_audit", lambda *_args, **_kwargs: None)

    app = FastAPI()
    app.include_router(comfly_proxy.router)
    client = TestClient(app)

    response = client.post(
        "/api/comfly-proxy/v1/images/edits",
        data={"model": "gpt-image-2", "prompt": "x", "client_request_id": "job-empty"},
        files={"image": ("reference.png", b"fake-image-bytes", "image/png")},
        headers={"Authorization": "Bearer user"},
    )

    assert response.status_code == 502
    assert captured["refunds"] == 1


def test_image_edit_pending_same_request_does_not_charge(monkeypatch):
    captured = {"pre_deducts": 0, "upstream_calls": 0}
    result_key = "comfly:image_edit:result:69:job-pending"
    pending_key = f"{result_key}:pending"

    async def _fake_multipart_request(*_args, **_kwargs):
        captured["upstream_calls"] += 1
        return {"data": [{"url": "https://example.com/new.png"}]}

    def _cache_get(key):
        if key == pending_key:
            return "1"
        return None

    monkeypatch.setenv("COMFLY_IMAGE_EDIT_PENDING_WAIT_SECONDS", "0")
    monkeypatch.setattr(comfly_proxy, "_check_request_authorized_for_billing", lambda _request: None)
    monkeypatch.setattr(comfly_proxy, "_require_model_entry", lambda _model: {"comfly_model": _model})
    monkeypatch.setattr(
        comfly_proxy,
        "_resolve_proxy_user_ids_from_request",
        lambda _request, map_to_online_user=True: (69, 69),
    )
    monkeypatch.setattr(
        comfly_proxy,
        "_do_pre_deduct_by_user_id",
        lambda *_args, **_kwargs: captured.__setitem__("pre_deducts", captured["pre_deducts"] + 1),
    )
    monkeypatch.setattr(comfly_proxy, "_comfly_multipart_request", _fake_multipart_request)
    monkeypatch.setattr(comfly_proxy, "cache_get", _cache_get)
    monkeypatch.setattr(comfly_proxy, "cache_set_if_absent", lambda key, *_args, **_kwargs: key != pending_key)
    monkeypatch.setattr(comfly_proxy, "_audit", lambda *_args, **_kwargs: None)

    app = FastAPI()
    app.include_router(comfly_proxy.router)
    client = TestClient(app)

    response = client.post(
        "/api/comfly-proxy/v1/images/edits",
        data={"model": "gpt-image-2", "prompt": "x", "client_request_id": "job-pending"},
        files={"image": ("reference.png", b"fake-image-bytes", "image/png")},
        headers={"Authorization": "Bearer user"},
    )

    assert response.status_code == 409
    assert captured == {"pre_deducts": 0, "upstream_calls": 0}


def test_image_edit_pending_same_fingerprint_without_client_request_id_does_not_charge(monkeypatch):
    captured = {"pre_deducts": 0, "upstream_calls": 0}

    async def _fake_multipart_request(*_args, **_kwargs):
        captured["upstream_calls"] += 1
        return {"data": [{"url": "https://example.com/new.png"}]}

    def _cache_get(key):
        if key.startswith("comfly:image_edit:result:69:fp:") and key.endswith(":pending"):
            return "1"
        return None

    monkeypatch.setenv("COMFLY_IMAGE_EDIT_PENDING_WAIT_SECONDS", "0")
    monkeypatch.setattr(comfly_proxy, "_check_request_authorized_for_billing", lambda _request: None)
    monkeypatch.setattr(comfly_proxy, "_require_model_entry", lambda _model: {"comfly_model": _model})
    monkeypatch.setattr(
        comfly_proxy,
        "_resolve_proxy_user_ids_from_request",
        lambda _request, map_to_online_user=True: (69, 69),
    )
    monkeypatch.setattr(
        comfly_proxy,
        "_do_pre_deduct_by_user_id",
        lambda *_args, **_kwargs: captured.__setitem__("pre_deducts", captured["pre_deducts"] + 1),
    )
    monkeypatch.setattr(comfly_proxy, "_comfly_multipart_request", _fake_multipart_request)
    monkeypatch.setattr(comfly_proxy, "cache_get", _cache_get)
    monkeypatch.setattr(
        comfly_proxy,
        "cache_set_if_absent",
        lambda key, *_args, **_kwargs: not (key.startswith("comfly:image_edit:result:69:fp:") and key.endswith(":pending")),
    )
    monkeypatch.setattr(comfly_proxy, "_audit", lambda *_args, **_kwargs: None)

    app = FastAPI()
    app.include_router(comfly_proxy.router)
    client = TestClient(app)

    response = client.post(
        "/api/comfly-proxy/v1/images/edits",
        data={"model": "gpt-image-2", "prompt": "x"},
        files={"image": ("reference.png", b"fake-image-bytes", "image/png")},
        headers={"Authorization": "Bearer user"},
    )

    assert response.status_code == 409
    assert captured == {"pre_deducts": 0, "upstream_calls": 0}
