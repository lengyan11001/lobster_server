"""record-call（结算）幂等：同一 X-Billing-Idempotency-Key 只动一次账。"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
