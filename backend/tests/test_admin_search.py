from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import admin as admin_api
from backend.app.db import get_db
from backend.app.models import CreditLedger, User


def _client_for_admin_context(db_session_factory, context: admin_api.AdminContext) -> TestClient:
    app = FastAPI()
    app.include_router(admin_api.router)

    def _get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[admin_api._verify_admin_token] = lambda: context
    return TestClient(app)


def test_admin_search_large_numeric_phone_does_not_overflow(db_session_factory, db_session, monkeypatch):
    monkeypatch.setattr(admin_api.settings, "lobster_admin_username", "admin", raising=False)
    monkeypatch.setattr(admin_api.settings, "lobster_admin_password", "secret", raising=False)

    user = User(
        email="13828824168@sms.lobster.local",
        hashed_password="x",
        credits=Decimal("1.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()

    app = FastAPI()
    app.include_router(admin_api.router)

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    client = TestClient(app)
    res = client.get(
        "/admin/api/search?q=13828824168",
        headers={"X-Admin-Token": "lobster-admin-secret"},
    )

    assert res.status_code == 200
    users = res.json()["users"]
    assert len(users) == 1
    assert users[0]["email"] == "13828824168@sms.lobster.local"


def test_agent_user_list_includes_self_first_without_expanding_subordinate_permissions(
    db_session_factory,
    db_session,
):
    agent = User(
        email="agent-list@test.local",
        hashed_password="x",
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        is_agent=True,
        agent_level=1,
        brand_mark="daka",
        created_at=datetime.utcnow(),
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    child = User(
        email="agent-list-child@test.local",
        hashed_password="x",
        credits=Decimal("10.0000"),
        role="user",
        preferred_model="sutui",
        parent_user_id=agent.id,
        brand_mark="daka",
        created_at=datetime.utcnow(),
    )
    unrelated = User(
        email="agent-list-unrelated@test.local",
        hashed_password="x",
        credits=Decimal("10.0000"),
        role="user",
        preferred_model="sutui",
        brand_mark="daka",
        created_at=datetime.utcnow(),
    )
    db_session.add_all([child, unrelated])
    db_session.commit()
    db_session.refresh(child)

    client = _client_for_admin_context(
        db_session_factory,
        admin_api.AdminContext(role="agent", user_id=agent.id, brand_mark="daka"),
    )
    response = client.get("/admin/api/users")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert [row["id"] for row in data["users"]] == [agent.id, child.id]
    assert agent.id not in admin_api._agent_visible_user_ids(db_session, agent.id)

    search = client.get("/admin/api/search?q=agent-list@test.local")
    assert search.status_code == 200
    assert [row["id"] for row in search.json()["users"]] == [agent.id]

    detail = client.get(f"/admin/api/user/{agent.id}")
    assert detail.status_code == 200
    assert detail.json()["user"]["id"] == agent.id


def test_admin_skill_visibility_lists_and_saves_social_leads_permissions(db_session_factory, db_session, monkeypatch):
    monkeypatch.setattr(admin_api.settings, "lobster_admin_username", "admin", raising=False)
    monkeypatch.setattr(admin_api.settings, "lobster_admin_password", "secret", raising=False)

    user = User(
        email="social-leads@test.local",
        hashed_password="x",
        credits=Decimal("1.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    app = FastAPI()
    app.include_router(admin_api.router)

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    client = TestClient(app)
    headers = {"X-Admin-Token": "lobster-admin-secret"}

    res = client.get(f"/admin/api/user-skill-visibility/{user.id}", headers=headers)
    assert res.status_code == 200
    data = res.json()
    packages = data["all_packages"]
    package_by_id = {pkg["id"]: pkg for pkg in packages}

    assert [pkg["id"] for pkg in packages[:4]] == [
        "douyin_leads",
        "reddit_leads",
        "x_leads",
        "tiktok_leads",
    ]
    assert package_by_id["tiktok_leads"]["store_visibility"] == "入口权限"
    assert package_by_id["tiktok_leads"]["feature_key"] == "tiktok_leads_access"

    remove_res = client.post(
        f"/admin/api/user-skill-visibility/{user.id}",
        headers=headers,
        json={"remove": ["tiktok_leads"]},
    )
    assert remove_res.status_code == 200
    assert "tiktok_leads" in remove_res.json()["removed"]

    after_remove = client.get(f"/admin/api/user-skill-visibility/{user.id}", headers=headers)
    assert after_remove.status_code == 200
    assert "tiktok_leads" not in after_remove.json()["visible_ids"]

    add_res = client.post(
        f"/admin/api/user-skill-visibility/{user.id}",
        headers=headers,
        json={"add": ["tiktok_leads"]},
    )
    assert add_res.status_code == 200
    assert "tiktok_leads" in add_res.json()["added"]

    after_add = client.get(f"/admin/api/user-skill-visibility/{user.id}", headers=headers)
    assert after_add.status_code == 200
    assert "tiktok_leads" in after_add.json()["visible_ids"]


def test_admin_add_credits_accepts_negative_adjustment(db_session_factory, db_session, monkeypatch):
    monkeypatch.setattr(admin_api.settings, "lobster_admin_username", "admin", raising=False)
    monkeypatch.setattr(admin_api.settings, "lobster_admin_password", "secret", raising=False)

    user = User(
        email="credit-adjust@test.local",
        hashed_password="x",
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    app = FastAPI()
    app.include_router(admin_api.router)

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    client = TestClient(app)
    headers = {"X-Admin-Token": "lobster-admin-secret"}

    res = client.post(
        "/admin/api/add-credits",
        headers=headers,
        json={"user_id": user.id, "amount": -25, "description": "管理员手动扣减积分"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["old_credits"] == 100.0
    assert data["new_credits"] == 75.0
    assert data["delta"] == -25.0

    with db_session_factory() as s:
        saved = s.query(User).filter(User.id == user.id).one()
        assert saved.credits == Decimal("75.0000")
        ledger = s.query(CreditLedger).filter(CreditLedger.user_id == user.id).order_by(CreditLedger.id.desc()).first()
        assert ledger.delta == Decimal("-25.0000")
        assert ledger.entry_type == "admin_deduct"
        assert ledger.balance_after == Decimal("75.0000")

    too_much = client.post(
        "/admin/api/add-credits",
        headers=headers,
        json={"user_id": user.id, "amount": -1000, "description": "测试超额下分"},
    )
    assert too_much.status_code == 400
    assert "积分不足" in too_much.json()["detail"]


def test_agent_credit_addition_transfers_from_agent_balance(db_session_factory, db_session):
    agent = User(
        email="agent-credit@test.local",
        hashed_password="x",
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        is_agent=True,
        agent_level=1,
        brand_mark="daka",
        created_at=datetime.utcnow(),
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    child = User(
        email="agent-child@test.local",
        hashed_password="x",
        credits=Decimal("10.0000"),
        role="user",
        preferred_model="sutui",
        parent_user_id=agent.id,
        brand_mark="daka",
        created_at=datetime.utcnow(),
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)

    client = _client_for_admin_context(
        db_session_factory,
        admin_api.AdminContext(role="agent", user_id=agent.id, brand_mark="daka"),
    )
    response = client.post(
        "/admin/api/add-credits",
        json={"user_id": child.id, "amount": 60, "description": "代理商给下级划转"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["old_credits"] == 10.0
    assert data["new_credits"] == 70.0
    assert data["agent_old_credits"] == 100.0
    assert data["agent_new_credits"] == 40.0
    assert data["transfer_id"]

    with db_session_factory() as session:
        saved_agent = session.get(User, agent.id)
        saved_child = session.get(User, child.id)
        assert saved_agent.credits == Decimal("40.0000")
        assert saved_child.credits == Decimal("70.0000")
        ledger = session.query(CreditLedger).filter(CreditLedger.ref_id == data["transfer_id"]).all()
        assert len(ledger) == 2
        by_user = {row.user_id: row for row in ledger}
        assert by_user[agent.id].entry_type == "agent_transfer_out"
        assert by_user[agent.id].delta == Decimal("-60.0000")
        assert by_user[agent.id].balance_after == Decimal("40.0000")
        assert by_user[child.id].entry_type == "agent_transfer_in"
        assert by_user[child.id].delta == Decimal("60.0000")
        assert by_user[child.id].balance_after == Decimal("70.0000")

    insufficient = client.post(
        "/admin/api/add-credits",
        json={"user_id": child.id, "amount": 50, "description": "余额不足时不能超发"},
    )
    assert insufficient.status_code == 400
    assert "代理商余额不足" in insufficient.json()["detail"]

    with db_session_factory() as session:
        assert session.get(User, agent.id).credits == Decimal("40.0000")
        assert session.get(User, child.id).credits == Decimal("70.0000")
        assert session.query(CreditLedger).count() == 2


def test_admin_positive_credit_adjustment_does_not_use_agent_transfer(db_session_factory, db_session):
    user = User(
        email="admin-credit@test.local",
        hashed_password="x",
        credits=Decimal("10.0000"),
        role="user",
        preferred_model="sutui",
        brand_mark="bihuo",
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    client = _client_for_admin_context(
        db_session_factory,
        admin_api.AdminContext(role="admin", brand_mark="bihuo"),
    )

    response = client.post(
        "/admin/api/add-credits",
        json={"user_id": user.id, "amount": 25, "description": "管理员调账"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["new_credits"] == 35.0
    assert data["transfer_id"] is None
    assert data["agent_new_credits"] is None
    with db_session_factory() as session:
        ledger = session.query(CreditLedger).filter(CreditLedger.user_id == user.id).one()
        assert ledger.entry_type == "recharge"
        assert ledger.delta == Decimal("25.0000")
