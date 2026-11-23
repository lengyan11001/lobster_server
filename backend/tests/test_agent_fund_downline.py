"""一级代理统一下级充值扣费：开关、层级解析、库存预扣。"""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api import billing
from backend.app.models import CreditLedger, RechargeOrder, User


def _db():
    engine = create_engine("sqlite://")
    User.__table__.create(engine)
    CreditLedger.__table__.create(engine)
    RechargeOrder.__table__.create(engine)
    return sessionmaker(bind=engine)()


def _user(db, *, email, credits="0", is_agent=False, level=0, parent=None, fund=False):
    u = User(
        email=email,
        hashed_password="x",
        credits=Decimal(credits),
        role="user",
        preferred_model="sutui",
        brand_mark="bihuo",
        is_agent=is_agent,
        agent_level=level,
        parent_user_id=parent.id if parent else None,
        agent_fund_downline=fund,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_funding_agent_respects_switch_and_hierarchy():
    db = _db()
    agent = _user(db, email="agent@test.local", credits="1000", is_agent=True, level=1)
    level2 = _user(db, email="l2@test.local", is_agent=True, level=2, parent=agent)
    child = _user(db, email="child@test.local", parent=level2)
    assert billing._funding_agent_for_user(db, child) is None

    agent.agent_fund_downline = True
    db.commit()
    db.refresh(agent)
    assert billing._funding_agent_for_user(db, child).id == agent.id
    # 代理自己的充值不由自己代付
    assert billing._funding_agent_for_user(db, agent) is None
    db.close()


def test_reserve_deducts_level1_agent_and_refuses_when_short():
    db = _db()
    agent = _user(db, email="agent2@test.local", credits="1000", is_agent=True, level=1, fund=True)
    child = _user(db, email="child2@test.local", parent=agent)

    reserved = billing._reserve_oem_inventory(db, child, 100)
    assert reserved is not None and reserved.id == agent.id
    db.refresh(agent)
    assert agent.credits == Decimal("900.0000")
    row = db.query(CreditLedger).filter(CreditLedger.user_id == agent.id).order_by(CreditLedger.id.desc()).first()
    assert row.entry_type == "pre_deduct"
    assert row.delta == Decimal("-100.0000")
    assert row.meta["inventory"] is True
    assert row.meta["agent_funded"] is True

    agent.credits = Decimal("50.0000")
    db.commit()
    with pytest.raises(HTTPException) as exc:
        billing._reserve_oem_inventory(db, child, 100)
    assert exc.value.status_code == 409
    db.close()


def test_system_pool_payload_and_agent_toggle():
    from backend.app.api import admin

    db = _db()
    agent = _user(db, email="agent3@test.local", credits="123.4500", is_agent=True, level=1)
    ctx = admin.AdminContext(role="agent", user_id=agent.id, brand_mark="bihuo")
    payload = admin._system_pool_payload(db, ctx)
    assert payload["enabled"] is True
    assert payload["agent_user_id"] == agent.id
    assert payload["balance"] == 123.45
    assert payload["agent_fund_downline"] is False

    out = admin.set_agent_fund_downline(
        admin.AgentFundDownlineBody(user_id=agent.id, enabled=True),
        ctx=ctx,
        db=db,
    )
    assert out["agent_fund_downline"] is True
    db.refresh(agent)
    assert agent.agent_fund_downline is True
    db.close()


def test_admin_system_pool_sees_hikong_fixed_agent():
    from backend.app.api import admin

    db = _db()
    hk = _user(db, email="13510019898+brand-hikong@sms.lobster.local", credits="500", is_agent=True, level=1)
    hk.brand_mark = "hikong"
    db.commit()
    payload = admin._system_pool_payload(db, admin.AdminContext(role="admin", brand_mark="bihuo"))
    assert payload["enabled"] is True
    assert payload["agent_user_id"] == hk.id
    assert payload["brand_mark"] == "hikong"
    assert any(o["agent_user_id"] == hk.id for o in payload["options"])
    db.close()


def test_release_inventory_description_labels():
    assert "订单超时未支付" in billing._release_inventory_description("hikong", "timeout")
    assert "充值下单失败" in billing._release_inventory_description("hikong", "create_failed")
    assert "订单已取消" in billing._release_inventory_description("hikong", "cancelled")
    assert "充值订单关闭" in billing._release_inventory_description("hikong", "")


def test_release_oem_inventory_timeout_writes_correct_description():
    db = _db()
    agent = _user(db, email="agent4@test.local", credits="100", is_agent=True, level=1)
    order = RechargeOrder(
        user_id=agent.id,
        amount_yuan=100,
        amount_fen=0,
        credits=100,
        status="pending",
        out_trade_no="test-order-timeout-1",
        payment_method="fuiou_wechat",
        brand_mark="hikong",
        agent_user_id=agent.id,
        agent_reserved_credits=Decimal("100.0000"),
    )
    db.add(order)
    db.commit()
    billing._release_oem_inventory(db, order, reason="timeout")
    db.refresh(agent)
    db.refresh(order)
    assert agent.credits == Decimal("200.0000")
    row = (
        db.query(CreditLedger)
        .filter(CreditLedger.user_id == agent.id, CreditLedger.entry_type == "refund")
        .order_by(CreditLedger.id.desc())
        .first()
    )
    assert "订单超时未支付" in row.description
    assert "退回库存预扣" in row.description
    assert order.agent_reserved_credits == Decimal("0.0000")
    db.close()
