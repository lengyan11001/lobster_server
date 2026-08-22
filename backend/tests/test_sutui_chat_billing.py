from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

import httpx
import pytest
from starlette.requests import Request


def _user(credits: str = "100.0000"):
    from backend.app.models import User

    return User(
        email=f"chat-{credits}@test.local",
        hashed_password="x",
        credits=Decimal(credits),
        role="user",
        preferred_model="sutui",
        brand_mark="bihuo",
        created_at=datetime.utcnow(),
    )


def test_sutui_chat_balance_precheck_requires_min_10(db_session, monkeypatch):
    from fastapi import HTTPException

    from backend.app.api import sutui_chat_proxy
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)

    user = _user("9.0000")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    with pytest.raises(HTTPException) as exc:
        sutui_chat_proxy._require_balance_before_upstream_chat(
            db_session,
            user,
            "gpt-4o",
            {"messages": [{"role": "user", "content": "hi"}]},
        )

    assert exc.value.status_code == 402
    assert "最低需 10" in str(exc.value.detail)


def test_sutui_chat_deduct_has_min_10_charge(db_session, monkeypatch):
    from backend.app.api import sutui_chat_proxy
    from backend.app.core.config import settings
    from backend.app.models import CreditLedger, User

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)
    monkeypatch.setattr(
        sutui_chat_proxy,
        "_credits_for_sutui_chat",
        lambda *args, **kwargs: (Decimal("3.0000"), "test_low_price"),
    )

    user = _user("100.0000")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    sutui_chat_proxy._apply_chat_deduct(
        db_session,
        user,
        "gpt-4o",
        {"prompt_tokens": 1, "completion_tokens": 1},
        {"usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        trace_id="test-trace",
    )

    db_session.refresh(user)
    assert user.credits == Decimal("90.0000")
    row = db_session.query(CreditLedger).filter(CreditLedger.user_id == user.id).one()
    assert row.delta == Decimal("-10.0000")
    assert row.entry_type == "sutui_chat"
    assert row.meta["deduct_credits"] == 10.0
    assert row.meta["raw_computed_credits"] == 3.0
    assert row.meta["min_charge_credits"] == 10.0
    assert row.meta["billing_src"] == "test_low_price+min_10"


def test_sutui_chat_deduct_keeps_price_above_min(db_session, monkeypatch):
    from backend.app.api import sutui_chat_proxy
    from backend.app.core.config import settings
    from backend.app.models import CreditLedger

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)
    monkeypatch.setattr(
        sutui_chat_proxy,
        "_credits_for_sutui_chat",
        lambda *args, **kwargs: (Decimal("12.5000"), "test_high_price"),
    )

    user = _user("100.0000")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    sutui_chat_proxy._apply_chat_deduct(
        db_session,
        user,
        "gpt-4o",
        {"prompt_tokens": 1, "completion_tokens": 1},
        {"usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        trace_id="test-trace",
    )

    db_session.refresh(user)
    assert user.credits == Decimal("87.5000")
    row = db_session.query(CreditLedger).filter(CreditLedger.user_id == user.id).one()
    assert row.delta == Decimal("-12.5000")
    assert row.meta["deduct_credits"] == 12.5
    assert row.meta["raw_computed_credits"] == 12.5
    assert row.meta["billing_src"] == "test_high_price"


def test_sutui_chat_turn_precharged_requires_internal_key(monkeypatch):
    from starlette.datastructures import Headers
    from starlette.requests import Request

    from backend.app.api import sutui_chat_proxy
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "lobster_mcp_billing_internal_key", "internal-key", raising=False)

    def _request(headers: dict[str, str]) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/sutui-chat/completions",
                "headers": Headers(headers).raw,
            }
        )

    assert sutui_chat_proxy._is_chat_turn_precharged_request(
        _request(
            {
                "X-Lobster-Mcp-Billing": "internal-key",
                "X-Lobster-Chat-Turn-Charged": "1",
            }
        )
    )
    assert not sutui_chat_proxy._is_chat_turn_precharged_request(
        _request({"X-Lobster-Chat-Turn-Charged": "1"})
    )


def test_charge_chat_turn_once_is_idempotent(db_session, monkeypatch):
    from backend.app.api import sutui_chat_proxy
    from backend.app.core.config import settings
    from backend.app.models import CreditLedger

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)

    user = _user("100.0000")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    first = sutui_chat_proxy.charge_chat_turn_once(
        db_session,
        user,
        "turn-1",
        source="test",
    )
    second = sutui_chat_proxy.charge_chat_turn_once(
        db_session,
        user,
        "turn-1",
        source="test",
    )

    db_session.refresh(user)
    assert first["charged"] is True
    assert second["charged"] is True
    assert user.credits == Decimal("90.0000")
    rows = db_session.query(CreditLedger).filter(CreditLedger.user_id == user.id).all()
    assert len(rows) == 1
    assert rows[0].entry_type == "chat_turn"
    assert rows[0].delta == Decimal("-10.0000")


def test_charge_chat_turn_accepts_legacy_long_source(db_session, monkeypatch):
    from backend.app.api import sutui_chat_proxy
    from backend.app.core.config import settings
    from backend.app.models import CreditLedger

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)

    user = _user("100.0000")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    out = sutui_chat_proxy.charge_chat_turn_once(
        db_session,
        user,
        "turn-long-source",
        source="online_chat_stream_task_status_fast",
    )

    db_session.refresh(user)
    assert out["charged"] is True
    assert user.credits == Decimal("90.0000")
    row = db_session.query(CreditLedger).filter(CreditLedger.user_id == user.id).one()
    assert row.meta["source"] == "online_chat_stream_task_status_f"
    assert len(row.meta["source"]) == 32


@pytest.mark.asyncio
async def test_stream_disconnect_after_usage_chunk_does_not_deduct(db_session, db_session_factory, monkeypatch):
    from backend.app.api import sutui_chat_proxy
    from backend.app.core.config import settings
    from backend.app.models import CreditLedger

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)
    monkeypatch.setattr(sutui_chat_proxy, "next_sutui_server_token_with_pool", lambda **_: _async_value(("test-token", "default")))
    monkeypatch.setattr(sutui_chat_proxy, "SessionLocal", db_session_factory)

    class FakeStreamResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b'data: {"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n'
            raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")

    @asynccontextmanager
    async def fake_stream_chat_upstream(*args, **kwargs):
        yield FakeStreamResponse()

    monkeypatch.setattr(sutui_chat_proxy, "_stream_chat_upstream", fake_stream_chat_upstream)

    user = _user("100.0000")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    body = json.dumps(
        {
            "model": "openai/gpt-5.6-sol",
            "stream": True,
            "messages": [{"role": "user", "content": "生成一张图片"}],
        }
    ).encode("utf-8")
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/sutui-chat/completions",
            "headers": [],
        },
        receive,
    )

    response = await sutui_chat_proxy.sutui_chat_completions(request, current_user=user, db=db_session)

    try:
        async for _chunk in response.body_iterator:
            pass
    except httpx.RemoteProtocolError:
        pass

    db_session.refresh(user)
    assert user.credits == Decimal("100.0000")
    assert db_session.query(CreditLedger).filter(CreditLedger.user_id == user.id).count() == 0


async def _async_value(value):
    return value


def test_yyapi_usage_billing_uses_customer_multiplier(monkeypatch):
    from backend.app.services import sutui_pricing
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "yyapi_input_price_yuan_per_1m", 5.0, raising=False)
    monkeypatch.setattr(settings, "yyapi_cached_input_price_yuan_per_1m", 0.5, raising=False)
    monkeypatch.setattr(settings, "yyapi_output_price_yuan_per_1m", 30.0, raising=False)
    monkeypatch.setattr(settings, "yyapi_upstream_multiplier", 0.23, raising=False)
    monkeypatch.setattr(settings, "yyapi_customer_multiplier", 0.4, raising=False)
    monkeypatch.setattr(settings, "yyapi_credits_per_yuan", 100.0, raising=False)

    out = sutui_pricing.yyapi_usage_billing(
        {
            "prompt_tokens": 181229,
            "prompt_cache_hit_tokens": 7258,
            "prompt_cache_miss_tokens": 173971,
            "completion_tokens": 9467,
        }
    )
    assert out is not None
    assert out["upstream_cost_yuan"] == Decimal("0.26622362")
    assert out["customer_charge_yuan"] == Decimal("0.4629976")
    assert out["customer_credits"] == Decimal("46.2998")


def test_yyapi_turn_precharge_defers_until_usage(db_session, monkeypatch):
    from backend.app.api import sutui_chat_proxy
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)
    monkeypatch.setattr(sutui_chat_proxy, "_yyapi_chat_configured", lambda: True)
    user = _user("1.0000")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    out = sutui_chat_proxy.charge_chat_turn_once(db_session, user, "yyapi-turn")
    db_session.refresh(user)
    assert out["pricing_deferred"] is True
    assert out["credits_charged"] == 0
    assert user.credits == Decimal("1.0000")
