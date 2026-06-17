from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_video_submit_uses_request_token_without_route_db(monkeypatch, db_session_factory, patch_fuiou_settings):
    from backend.app.api import comfly_proxy
    from backend.app.api.auth import create_access_token
    from backend.app.db import get_db
    from backend.app.models import User

    session = db_session_factory()
    try:
        user = User(
            email="video-submit@test.local",
            hashed_password="x",
            credits=Decimal("100.0000"),
            role="user",
            preferred_model="sutui",
            created_at=datetime.utcnow(),
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        user_id = int(user.id)
    finally:
        session.close()

    observed: dict[str, object] = {}

    monkeypatch.setattr(comfly_proxy, "_check_request_authorized_for_billing", lambda request: None)
    monkeypatch.setattr(comfly_proxy, "_require_model_entry", lambda model: {"token_group": "comfly"})
    monkeypatch.setattr(comfly_proxy, "_body_for_upstream_model", lambda body, model, entry: dict(body))
    monkeypatch.setattr(comfly_proxy, "_is_grok_api_format", lambda entry: False)
    monkeypatch.setattr(comfly_proxy, "_comfly_url", lambda endpoint, model: f"https://example.test{endpoint}")
    monkeypatch.setattr(comfly_proxy, "_comfly_headers", lambda model: {"Authorization": "Bearer fake"})
    monkeypatch.setattr(comfly_proxy, "_remember_proxy_video_task", lambda task_id, api_kind, model: observed.setdefault("remembered", (task_id, api_kind, model)))
    monkeypatch.setattr(comfly_proxy, "_audit", lambda *args, **kwargs: None)

    async def fake_comfly_request(method, url, body, headers, timeout):
        observed["request"] = {
            "method": method,
            "url": url,
            "body": dict(body or {}),
            "headers": dict(headers or {}),
            "timeout": timeout,
        }
        return {"task_id": "task_123"}

    monkeypatch.setattr(comfly_proxy, "_comfly_request", fake_comfly_request)

    def fake_pre_deduct(user_id_value, credits, **kwargs):
        observed["pre_deduct_user_id"] = int(user_id_value)
        observed["pre_deduct_credits"] = int(credits)
        return Decimal("1")

    monkeypatch.setattr(comfly_proxy, "_do_pre_deduct_by_user_id", fake_pre_deduct)

    usage_events = []

    def fake_log_model_usage_event(db, **kwargs):
        usage_events.append({"db": db, **kwargs})
        return None

    monkeypatch.setattr(comfly_proxy, "log_model_usage_event", fake_log_model_usage_event)

    app = FastAPI()
    app.include_router(comfly_proxy.router, prefix="")

    def _forbidden_get_db_override():
        raise AssertionError("route-level get_db should not be used by video submit")
        yield

    app.dependency_overrides[get_db] = _forbidden_get_db_override
    client = TestClient(app)

    token = create_access_token(data={"sub": str(user_id)})
    response = client.post(
        "/api/comfly-proxy/v2/videos/generations",
        json={"model": "veo3.1-fast", "prompt": "test prompt"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["task_id"] == "task_123"
    assert observed["pre_deduct_user_id"] == user_id
    assert observed["request"]["url"] == "https://example.test/v2/videos/generations"
    assert usage_events and usage_events[0]["db"] is None
