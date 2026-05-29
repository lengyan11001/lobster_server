from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


def _billing_client(db_session_factory, user_id: int, monkeypatch):
    from backend.app.api.auth import get_current_user
    from backend.app.api.billing import router as billing_router
    from backend.app.core.config import settings
    from backend.app.db import get_db

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)

    app = FastAPI()
    app.include_router(billing_router)

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    def _get_current_user_override(db=Depends(get_db)):
        from backend.app.models import User

        return db.query(User).filter(User.id == user_id).first()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _get_current_user_override
    return TestClient(app)


def test_daily_limit_status_and_update(db_session, db_session_factory, monkeypatch):
    from backend.app.models import CreditLedger, User

    user = User(
        email="daily-limit@test.local",
        hashed_password="x",
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        CreditLedger(
            user_id=user.id,
            delta=Decimal("-12.0000"),
            balance_after=Decimal("88.0000"),
            entry_type="pre_deduct",
            created_at=datetime.utcnow(),
        )
    )
    db_session.add(
        CreditLedger(
            user_id=user.id,
            delta=Decimal("2.0000"),
            balance_after=Decimal("90.0000"),
            entry_type="refund",
            created_at=datetime.utcnow(),
        )
    )
    db_session.commit()
    db_session.refresh(user)

    client = _billing_client(db_session_factory, user.id, monkeypatch)
    resp = client.put("/api/billing/daily-limit", json={"daily_limit": 20})
    assert resp.status_code == 200
    assert resp.json()["daily_limit"] == 20.0
    assert resp.json()["used_today"] == 10.0
    assert resp.json()["remaining_today"] == 10.0


def test_chat_turn_pre_deduct_blocks_over_daily_limit(db_session, monkeypatch):
    from fastapi import HTTPException

    from backend.app.api import sutui_chat_proxy
    from backend.app.core.config import settings
    from backend.app.models import CreditLedger, UserDailyCreditLimit

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)

    from backend.tests.test_sutui_chat_billing import _user

    user = _user("100.0000")
    db_session.add(user)
    db_session.flush()
    db_session.add(UserDailyCreditLimit(user_id=user.id, daily_limit=Decimal("15.0000")))
    db_session.add(
        CreditLedger(
            user_id=user.id,
            delta=Decimal("-10.0000"),
            balance_after=Decimal("90.0000"),
            entry_type="chat_turn",
            created_at=datetime.utcnow(),
        )
    )
    db_session.commit()
    db_session.refresh(user)

    with pytest.raises(HTTPException) as exc:
        sutui_chat_proxy.charge_chat_turn_once(db_session, user, "turn-over-limit")

    assert exc.value.status_code == 429
    assert "每日算力消耗上限" in str(exc.value.detail)


def test_capability_pre_deduct_blocks_over_daily_limit(db_session, db_session_factory, monkeypatch):
    from backend.app.models import CapabilityConfig, CreditLedger, User, UserDailyCreditLimit
    from backend.tests.test_capability_billing import _client

    user = User(
        email="daily-limit-cap@test.local",
        hashed_password="x",
        credits=Decimal("200.0000"),
        role="user",
        preferred_model="sutui",
        brand_mark="bihuo",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserDailyCreditLimit(user_id=user.id, daily_limit=Decimal("60.0000")))
    db_session.add(
        CreditLedger(
            user_id=user.id,
            delta=Decimal("-10.0000"),
            balance_after=Decimal("190.0000"),
            entry_type="chat_turn",
            created_at=datetime.utcnow(),
        )
    )
    db_session.add(
        CapabilityConfig(
            capability_id="image.generate",
            description="image",
            upstream="sutui",
            upstream_tool="generate",
            unit_credits=0,
            enabled=True,
        )
    )
    db_session.commit()
    db_session.refresh(user)

    client = _client(db_session_factory, user.id, monkeypatch, multiplier="2")
    resp = client.post(
        "/capabilities/pre-deduct",
        headers={"X-Installation-Id": "test-install-1", "X-Lobster-Mcp-Billing": "test-billing-key"},
        json={
            "capability_id": "image.generate",
            "model": "openai/gpt-image-2",
            "params": {"prompt": "test"},
        },
    )
    assert resp.status_code == 429
    assert "每日算力消耗上限" in resp.text

