"""shop 分佣/归因/状态机 单测（P0）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from backend.app.services import shop_commission as sc


def test_rate_precedence_product_over_merchant_default():
    assert sc.effective_rate_bp(2500, 1000) == 2500
    assert sc.effective_rate_bp(0, 1000) == 1000
    assert sc.effective_rate_bp(None, 1000) == 1000
    assert sc.effective_rate_bp(0, 1000, commission_type="fixed") == 0


def test_commission_percent_excludes_shipping():
    plan = sc.compute_commission(pay_amount_cents=20000, shipping_cents=1000, product_bp=1000, qty=1)
    assert plan.base_cents == 19000
    assert plan.rate_bp == 1000
    assert plan.amount_cents == 1900
    assert plan.applied == "product"


def test_commission_uses_merchant_default_when_product_zero():
    plan = sc.compute_commission(pay_amount_cents=10000, product_bp=0, merchant_default_bp=1500)
    assert plan.applied == "merchant_default"
    assert plan.rate_bp == 1500
    assert plan.amount_cents == 1500


def test_commission_fixed_times_qty():
    plan = sc.compute_commission(pay_amount_cents=99900, commission_type="fixed", commission_fixed_cents=800, qty=3)
    assert plan.applied == "fixed"
    assert plan.rate_bp == 0
    assert plan.amount_cents == 2400


def test_commission_cap_and_floor():
    capped = sc.compute_commission(pay_amount_cents=100000, product_bp=5000, cap_cents=2000)
    assert capped.amount_cents == 2000
    floored = sc.compute_commission(pay_amount_cents=10000, product_bp=100, min_cents=500)
    assert floored.amount_cents == 500
    zero_base = sc.compute_commission(pay_amount_cents=1000, shipping_cents=1000, product_bp=1000, min_cents=500)
    assert zero_base.amount_cents == 0


def test_refund_proration():
    assert sc.prorate_commission_on_refund(1000, 10000, 0) == 1000
    assert sc.prorate_commission_on_refund(1000, 10000, 5000) == 500
    assert sc.prorate_commission_on_refund(1000, 10000, 10000) == 0
    assert sc.prorate_commission_on_refund(1000, 10000, 99999) == 0


def test_platform_fee_and_merchant_income():
    assert sc.platform_fee_cents(100000, 300) == 3000
    assert sc.merchant_income_cents(100000, platform_fee_bp=300, commission_cents=10000) == 87000


def test_attribution_gates():
    assert sc.should_attribute() == (True, "ok")
    assert sc.should_attribute(self_buy=True) == (False, "self_buy")
    assert sc.should_attribute(within_window=False) == (False, "attribution_expired")
    assert sc.should_attribute(repeated_visitor=True, block_repeated=True) == (False, "repeated_visitor")


def test_commission_status_transitions():
    now = datetime(2026, 9, 28, 12, 0, 0)
    assert sc.commission_status_for_order("pending", "none") == "none"
    assert sc.commission_status_for_order("paid", "none") == "pending"
    assert sc.commission_status_for_order("shipped", "pending") == "pending"
    assert sc.commission_status_for_order("refunded", "pending") == "invalid"
    assert sc.commission_status_for_order("cancelled", "pending") == "invalid"
    completed = now - timedelta(days=8)
    assert sc.commission_status_for_order("completed", "pending", completed_at=completed, now=now) == "confirmed"
    fresh = now - timedelta(days=1)
    assert sc.commission_status_for_order("completed", "pending", completed_at=fresh, now=now) == "pending"
    assert sc.commission_status_for_order("completed", "settled", completed_at=completed, now=now) == "settled"


def test_deadlines_and_link():
    t0 = datetime(2026, 9, 28, 10, 0, 0)
    assert sc.cancel_deadline(t0) == t0 + timedelta(minutes=30)
    assert sc.confirm_deadline(t0) == t0 + timedelta(days=7)
    assert sc.attribution_expire_at(t0, days=30) == t0 + timedelta(days=30)
    assert sc.referral_link("https://shop.bhzn.top/", 88, 26, "abc123") == "https://shop.bhzn.top/p/88?r=26&rf=abc123"
    assert sc.referral_link("https://shop.bhzn.top", 88, 26) == "https://shop.bhzn.top/p/88?r=26"


def test_order_no_shape():
    no = sc.make_order_no(7, now=datetime(2026, 9, 28, 10, 30, 0), seq=12)
    assert no.startswith("SH20260928103000")
    assert no.endswith("000700012")
