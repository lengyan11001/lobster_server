"""record-call（结算）幂等：同一 X-Billing-Idempotency-Key 只动一次账。"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from backend.app.api import capabilities as cap
from backend.app.models import BillingIdempotency


def test_record_call_idempotency_roundtrip():
    engine = create_engine("sqlite://")
    BillingIdempotency.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    assert cap._record_call_idempotent_cached(db, 7, "seed:settle") is None
    cap._record_call_idempotent_store(db, 7, "seed:settle", {"id": 11, "credits_charged": 300.0})
    cached = cap._record_call_idempotent_cached(db, 7, "seed:settle")
    assert cached == {"id": 11, "credits_charged": 300.0}
    # 不同用户 / 不同 key 不串
    assert cap._record_call_idempotent_cached(db, 8, "seed:settle") is None
    assert cap._record_call_idempotent_cached(db, 7, "other") is None
    db.close()


def test_record_call_defines_idem_key_before_store():
    """回归：2026-11-10 引入的 NameError（idem_key 未定义）会让已提交的结算接口 500。"""
    src = inspect.getsource(cap.record_call)
    assert "idem_key = _billing_idempotency_key(request)" in src
    assert src.index("idem_key = _billing_idempotency_key(request)") < src.index(
        "_record_call_idempotent_store(db, current_user.id, idem_key, response)"
    )


def test_record_call_returns_cached_settle_without_touching_balance():
    engine = create_engine("sqlite://")
    BillingIdempotency.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    cap._record_call_idempotent_store(db, 7, "seed:settle", {"id": 11, "credits_charged": 240.0})
    req = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/capabilities/record-call",
            "headers": [(b"x-billing-idempotency-key", b"seed:settle")],
        }
    )
    body = cap.RecordCallIn(capability_id="hifly.video.create_by_tts", success=True)
    out = cap.record_call(
        body=body,
        request=req,
        current_user=SimpleNamespace(id=7),
        db=db,
        x_installation_id=None,
    )
    assert out == {"id": 11, "credits_charged": 240.0}
    db.close()
