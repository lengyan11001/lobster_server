from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_chat_proxy_prepares_remote_image_url_before_upstream(
    monkeypatch,
    db_session_factory,
    patch_fuiou_settings,
):
    from backend.app.api import comfly_proxy
    from backend.app.api.auth import create_access_token
    from backend.app.models import User

    session = db_session_factory()
    try:
        user = User(
            email="chat-image-prepare@test.local",
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

    monkeypatch.setattr(comfly_proxy, "SessionLocal", db_session_factory)
    monkeypatch.setattr(comfly_proxy, "_check_request_authorized_for_billing", lambda request: None)
    monkeypatch.setattr(comfly_proxy, "_require_model_entry", lambda model: {"token_group": "comfly"})
    monkeypatch.setattr(comfly_proxy, "_body_for_upstream_model", lambda body, model, entry: dict(body))
    monkeypatch.setattr(comfly_proxy, "_comfly_url", lambda endpoint, model: f"https://example.test{endpoint}")
    monkeypatch.setattr(comfly_proxy, "_comfly_headers", lambda model: {"Authorization": "Bearer fake"})
    monkeypatch.setattr(comfly_proxy, "estimate_comfly_credits", lambda model, payload, for_user=True: 1)

    def fake_pre_deduct(*args, **kwargs):
        observed["pre_deduct_meta"] = kwargs.get("extra_meta")
        return Decimal("1")

    def fake_settle(*args, **kwargs):
        observed["settle_meta"] = kwargs.get("extra_meta")

    monkeypatch.setattr(comfly_proxy, "_do_pre_deduct_by_user_id", fake_pre_deduct)
    monkeypatch.setattr(comfly_proxy, "_do_settle_by_user_id", fake_settle)
    monkeypatch.setattr(comfly_proxy, "_audit", lambda *args, **kwargs: None)

    async def fake_download_image_bytes(url: str):
        observed["download_url"] = url
        return b"image-bytes", "image/png", ".png"

    async def fake_comfly_request(method, url, body, headers, timeout):
        observed["request"] = {
            "method": method,
            "url": url,
            "body": body,
            "headers": headers,
            "timeout": timeout,
        }
        return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

    monkeypatch.setattr(comfly_proxy, "_download_image_bytes", fake_download_image_bytes)
    monkeypatch.setattr(comfly_proxy, "_comfly_request", fake_comfly_request)

    app = FastAPI()
    app.include_router(comfly_proxy.router, prefix="")
    client = TestClient(app)

    source_url = "https://cdn.example.test/ref.png"
    token = create_access_token(data={"sub": str(user_id)})
    response = client.post(
        "/api/comfly-proxy/v1/chat/completions",
        json={
            "model": "gpt-5.4",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "analyze this image"},
                        {"type": "image_url", "image_url": {"url": source_url}},
                    ],
                }
            ],
            "max_tokens": 1000,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    assert observed["download_url"] == source_url
    sent_body = observed["request"]["body"]
    sent_image_url = sent_body["messages"][0]["content"][1]["image_url"]["url"]
    assert sent_image_url.startswith("data:image/png;base64,")
    assert source_url not in json.dumps(sent_body)
    assert observed["pre_deduct_meta"] == {"prepared_image_count": 1}
    assert observed["settle_meta"]["prepared_image_count"] == 1
    assert source_url not in json.dumps(observed["pre_deduct_meta"])
    assert "data:image/" not in json.dumps(observed["settle_meta"])
