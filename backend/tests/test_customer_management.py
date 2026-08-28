from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import admin as admin_api
from backend.app.api.auth import get_current_user
from backend.app.api.customer_management import router as customer_router
from backend.app.db import get_db
from backend.app.models import RecorderAudioRecord, User


def _user_client(db_session_factory, user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(customer_router)

    def get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    def current_user_override():
        with db_session_factory() as session:
            return session.get(User, user_id)

    app.dependency_overrides[get_db] = get_db_override
    app.dependency_overrides[get_current_user] = current_user_override
    return TestClient(app)


def test_customer_crud_and_recording_summary_association(db_session_factory, db_session, test_user):
    recording = RecorderAudioRecord(
        user_id=test_user.id,
        file_name="meeting.wav",
        display_name="客户会议",
        status="completed",
        summary_text="客户关注交付时间和报价。",
        audio_path="",
        created_at=datetime.utcnow(),
    )
    db_session.add(recording)
    db_session.commit()
    db_session.refresh(recording)
    client = _user_client(db_session_factory, test_user.id)

    created = client.post("/api/customers", json={"name": "张三", "company": "甲公司", "phone": "13800000000", "tags": ["重点", "重点"]})
    assert created.status_code == 200
    customer_id = created.json()["customer"]["id"]
    communication = client.post(
        f"/api/customers/{customer_id}/communications",
        json={"communication_type": "call", "content": "确认报价", "recording_id": recording.id},
    )
    assert communication.status_code == 200
    assert communication.json()["communication"]["summary"] == "客户关注交付时间和报价。"

    detail = client.get(f"/api/customers/{customer_id}")
    assert detail.status_code == 200
    assert len(detail.json()["communications"]) == 1
    assert detail.json()["communications"][0]["recording"]["id"] == recording.id

    recordings = client.get(f"/api/customers/{customer_id}/recordings?page=1&page_size=100")
    assert recordings.status_code == 200
    assert recordings.json()["items"][0]["id"] == recording.id

    listing = client.get("/api/customers?q=13800000000&page=1&page_size=20")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["tags"] == ["重点"]


def test_customer_isolation_between_users(db_session_factory, db_session, test_user, other_user):
    client = _user_client(db_session_factory, test_user.id)
    created = client.post("/api/customers", json={"name": "只属于 Alice"})
    customer_id = created.json()["customer"]["id"]
    other_client = _user_client(db_session_factory, other_user.id)
    assert other_client.get(f"/api/customers/{customer_id}").status_code == 404
    assert other_client.get("/api/customers").json()["total"] == 0


def test_agent_customer_scope_only_includes_descendants(db_session_factory, db_session):
    agent = User(email="customer-agent@test.local", hashed_password="x", credits=Decimal("10"), role="user", preferred_model="sutui", is_agent=True, agent_level=1, brand_mark="bihuo", created_at=datetime.utcnow())
    child = User(email="customer-child@test.local", hashed_password="x", credits=Decimal("10"), role="user", preferred_model="sutui", parent_user_id=None, brand_mark="bihuo", created_at=datetime.utcnow())
    unrelated = User(email="customer-unrelated@test.local", hashed_password="x", credits=Decimal("10"), role="user", preferred_model="sutui", brand_mark="bihuo", created_at=datetime.utcnow())
    db_session.add_all([agent, child, unrelated])
    db_session.commit()
    child.parent_user_id = agent.id
    db_session.commit()
    client = TestClient(_admin_app(db_session_factory, admin_api.AdminContext(role="agent", user_id=agent.id, brand_mark="bihuo")))
    own = client.post("/admin/api/customers", json={"owner_user_id": child.id, "name": "下级客户"})
    assert own.status_code == 200
    blocked = client.post("/admin/api/customers", json={"owner_user_id": unrelated.id, "name": "无关客户"})
    assert blocked.status_code == 403
    assert client.get("/admin/api/customers").json()["total"] == 1


def _admin_app(db_session_factory, context: admin_api.AdminContext) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_api.router)

    def get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_db_override
    app.dependency_overrides[admin_api._verify_admin_token] = lambda: context
    return app
