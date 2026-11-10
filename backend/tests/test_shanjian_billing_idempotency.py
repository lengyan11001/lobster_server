"""数字人2.0 结算/退款幂等：重复轮询不能重复退款，漏掉的结算要能补偿。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from backend.app.api import shanjian_digital_human as dh
from backend.app.models import ShanjianDigitalHumanVideoTask


class _User:
    id = 385


def _make_session():
    engine = create_engine("sqlite://")
    ShanjianDigitalHumanVideoTask.__table__.create(engine)
    return sessionmaker(bind=engine)()


def _add_row(db, *, status="succeed", seed="seed-1", billing_status="pre_deducted"):
    billing = {
        "billing_status": billing_status,
        "capability_id": "hifly.video.create_by_tts",
        "unit_credits": 15,
        "estimated_seconds": 30,
        "credits_pre_deducted": 450.0,
        "billing_version": 2,
    }
    if seed:
        billing["idem_seed"] = seed
    row = ShanjianDigitalHumanVideoTask(
        user_id=385,
        title="t",
        status=status,
        task_id="task-" + str(datetime.utcnow().timestamp()),
        duration=20,
        video_url="https://example.com/a.mp4",
        submit_payload={"stage": "clip", "billing": billing},
        created_at=datetime.utcnow() - timedelta(minutes=30),
        updated_at=datetime.utcnow() - timedelta(minutes=30),
    )
    db.add(row)
    db.commit()
    return row


def _headers_request(token="abc"):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"authorization", ("Bearer " + token).encode())],
        }
    )


def test_billing_headers_carry_idempotency_key():
    headers = dh._billing_headers(_headers_request(), None, idem_key="seed:settle")
    assert headers["X-Billing-Idempotency-Key"] == "seed:settle"
    plain = dh._billing_headers(_headers_request(), None)
    assert "X-Billing-Idempotency-Key" not in plain


def test_finalize_persists_settled_status(monkeypatch):
    db = _make_session()
    row = _add_row(db)
    calls = []

    async def fake_settle(**kwargs):
        calls.append(kwargs)
        billing = dict(kwargs["billing"])
        billing["billing_status"] = "settled"
        return billing

    monkeypatch.setattr(dh, "_shanjian_settle_billing", fake_settle)
    asyncio.run(
        dh._finalize_row_billing(
            acting_user=_User(),
            request=None,
            row=row,
            status="succeed",
            duration_value=20,
            video_url=row.video_url or "",
            stage="clip",
        )
    )
    db.commit()
    assert len(calls) == 1

    fresh = db.query(ShanjianDigitalHumanVideoTask).filter(ShanjianDigitalHumanVideoTask.id == row.id).one()
    assert fresh.submit_payload["billing"]["billing_status"] == "settled"

    # 第二次轮询：状态已落库，不能再结算一次
    asyncio.run(
        dh._finalize_row_billing(
            acting_user=_User(),
            request=None,
            row=fresh,
            status="succeed",
            duration_value=20,
            video_url=fresh.video_url or "",
            stage="clip",
        )
    )
    assert len(calls) == 1
    db.close()


def test_legacy_failed_task_is_not_refunded_again(monkeypatch):
    db = _make_session()
    row = _add_row(db, status="failed", seed="")

    async def boom(*args, **kwargs):
        raise AssertionError("legacy rows must not be auto-refunded")

    monkeypatch.setattr(dh, "_shanjian_refund_billing", boom)
    asyncio.run(
        dh._finalize_row_billing(
            acting_user=_User(),
            request=None,
            row=row,
            status="failed",
            duration_value=None,
            video_url="",
            stage="clip",
            error_message="boom",
        )
    )
    fresh = db.query(ShanjianDigitalHumanVideoTask).filter(ShanjianDigitalHumanVideoTask.id == row.id).one()
    assert fresh.submit_payload["billing"]["billing_status"] == "pre_deducted"
    db.close()


def test_refund_key_is_stable_per_task(monkeypatch):
    captured = {}

    async def fake_post(self, url, json=None, headers=None):  # noqa: A002
        captured["url"] = url
        captured["headers"] = headers or {}

        class _Resp:
            status_code = 200
            content = b"{}"

            def json(self):
                return {}

            text = ""

        return _Resp()

    monkeypatch.setattr(dh.httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(dh, "_billing_base", lambda: "http://billing.local")
    billing = {"billing_status": "pre_deducted", "credits_pre_deducted": 450.0, "idem_seed": "seed-9"}
    out = asyncio.run(
        dh._shanjian_refund_billing(_headers_request(), billing, reason="x", idem_key="seed-9:refund")
    )
    assert captured["headers"]["X-Billing-Idempotency-Key"] == "seed-9:refund"
    assert out["billing_status"] == "refunded"
