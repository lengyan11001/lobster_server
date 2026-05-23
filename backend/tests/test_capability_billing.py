from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


def _client(db_session_factory, user_id: int, monkeypatch, *, multiplier: str = "2"):
    from backend.app.api.auth import get_current_user
    from backend.app.api.capabilities import router as capabilities_router
    from backend.app.core.config import settings
    from backend.app.db import get_db

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)
    monkeypatch.setattr(settings, "lobster_mcp_billing_internal_key", "test-billing-key", raising=False)
    monkeypatch.setenv("USER_PRICE_MULTIPLIER", multiplier)

    app = FastAPI()
    app.include_router(capabilities_router)

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


def test_sutui_generate_direct_charge_uses_user_multiplier(db_session, db_session_factory, monkeypatch):
    from backend.app.models import CapabilityConfig, CreditLedger, User

    user = User(
        email="billing@test.local",
        hashed_password="x",
        credits=Decimal("500.0000"),
        role="user",
        preferred_model="sutui",
        brand_mark="bihuo",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        CapabilityConfig(
            capability_id="video.generate",
            description="video",
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
        "/capabilities/record-call",
        headers={"X-Installation-Id": "test-install-1", "X-Lobster-Mcp-Billing": "test-billing-key"},
        json={
            "capability_id": "video.generate",
            "success": True,
            "credits_charged": 100,
            "request_payload": {"model": "xai/grok-imagine-video/text-to-video", "duration": 5},
            "response_payload": {"price": 100, "task_id": "task-1"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["credits_charged"] == 200.0

    with db_session_factory() as s:
        u = s.query(User).filter(User.id == user.id).first()
        assert u.credits == Decimal("300.0000")
        row = s.query(CreditLedger).filter(CreditLedger.user_id == user.id).one()
        assert row.entry_type == "direct_charge"
        assert row.delta == Decimal("-200.0000")
        assert row.meta["upstream_reported_credits"] == 100.0
        assert row.meta["price_multiplier"] == 2.0
        assert row.meta["credits_charged"] == 200.0


def test_pre_deduct_force_credits_charges_exact_amount(db_session, db_session_factory, monkeypatch):
    from backend.app.models import CapabilityConfig, CreditLedger, User

    user = User(
        email="force-billing@test.local",
        hashed_password="x",
        credits=Decimal("500.0000"),
        role="user",
        preferred_model="sutui",
        brand_mark="bihuo",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        CapabilityConfig(
            capability_id="image.generate",
            description="image",
            upstream="comfly",
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
            "model": "nano-banana-2",
            "force_credits": 90,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["credits_charged"] == 90.0

    with db_session_factory() as s:
        u = s.query(User).filter(User.id == user.id).first()
        assert u.credits == Decimal("410.0000")
        row = s.query(CreditLedger).filter(CreditLedger.user_id == user.id).one()
        assert row.entry_type == "pre_deduct"
        assert row.delta == Decimal("-90.0000")
        assert row.meta["upstream"] == "comfly"
        assert row.meta["pre_estimated"] == 90.0


def test_sutui_generate_pre_deduct_uses_user_multiplier(db_session, db_session_factory, monkeypatch):
    from backend.app.models import CapabilityConfig, CreditLedger, User
    from backend.app.services import sutui_billing_gate

    user = User(
        email="pre-billing@test.local",
        hashed_password="x",
        credits=Decimal("500.0000"),
        role="user",
        preferred_model="sutui",
        brand_mark="bihuo",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        CapabilityConfig(
            capability_id="video.generate",
            description="video",
            upstream="sutui",
            upstream_tool="generate",
            unit_credits=0,
            enabled=True,
        )
    )
    db_session.commit()
    db_session.refresh(user)

    monkeypatch.setattr(
        sutui_billing_gate,
        "assert_pricing_pre_deduct_allows_upstream_or_http",
        lambda *args, **kwargs: Decimal("100.0000"),
    )

    client = _client(db_session_factory, user.id, monkeypatch, multiplier="2")
    resp = client.post(
        "/capabilities/pre-deduct",
        headers={"X-Installation-Id": "test-install-1", "X-Lobster-Mcp-Billing": "test-billing-key"},
        json={
            "capability_id": "video.generate",
            "model": "xai/grok-imagine-video/text-to-video",
            "params": {"duration": 5},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["credits_charged"] == 200.0

    with db_session_factory() as s:
        u = s.query(User).filter(User.id == user.id).first()
        assert u.credits == Decimal("300.0000")
        row = s.query(CreditLedger).filter(CreditLedger.user_id == user.id).one()
        assert row.entry_type == "pre_deduct"
        assert row.delta == Decimal("-200.0000")
        assert row.meta["price_multiplier"] == 2.0
        assert row.meta["pre_estimated"] == 200.0
