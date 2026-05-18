from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import admin as admin_api
from backend.app.db import get_db
from backend.app.models import User


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
