from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest


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
