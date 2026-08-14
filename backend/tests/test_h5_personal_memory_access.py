from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.auth import get_current_user
from backend.app.api.h5_personal_settings import router as h5_personal_settings_router
from backend.app.db import get_db
from backend.app.models import H5AgentMemoryGrant, OpenClawMemoryDocument, User


def _client_for_user(db_session_factory, user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(h5_personal_settings_router, prefix="")

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    def _get_current_user_override():
        s = db_session_factory()
        try:
            return s.query(User).filter(User.id == user_id).first()
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _get_current_user_override
    return TestClient(app)


def _agent_user(db_session) -> User:
    agent = User(
        email="agent@test.local",
        hashed_password="x",
        credits=Decimal("0.0000"),
        role="user",
        preferred_model="sutui",
        is_agent=True,
        agent_level=1,
        agent_openclaw_memory_enabled=True,
        created_at=datetime.utcnow(),
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent


def _sub_user(db_session, agent_id: int) -> User:
    sub = User(
        email="13800000000@sms.lobster.local",
        hashed_password="x",
        credits=Decimal("0.0000"),
        role="user",
        preferred_model="sutui",
        parent_user_id=agent_id,
        created_at=datetime.utcnow(),
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)
    return sub


def _shared_memory_doc(agent_id: int) -> OpenClawMemoryDocument:
    now = datetime.utcnow()
    return OpenClawMemoryDocument(
        doc_id="agent-memory-001",
        target_user_id=agent_id,
        installation_id="agent-install-01",
        origin="agent_memory",
        title="上级记忆",
        filename="上级记忆.txt",
        content_text="这是上级授权的记忆内容。",
        status="active",
        meta={"source": "h5_personal_settings"},
        created_at=now,
        updated_at=now,
    )


def test_subordinate_can_see_and_preview_agent_memory_document(
    db_session,
    db_session_factory,
):
    agent = _agent_user(db_session)
    sub = _sub_user(db_session, agent.id)
    db_session.add_all([
        _shared_memory_doc(agent.id),
        H5AgentMemoryGrant(
            memory_doc_id="agent-memory-001",
            owner_user_id=agent.id,
            target_user_id=sub.id,
            status="active",
        ),
    ])
    db_session.commit()

    client = _client_for_user(db_session_factory, sub.id)
    headers = {"X-Installation-Id": "install-sub-001"}

    listing = client.get("/api/personal-settings/memory-documents/list", headers=headers)
    assert listing.status_code == 200
    docs = listing.json()["documents"]
    assert any(doc["doc_id"] == "agent-memory-001" and doc["source"] == "agent" for doc in docs)

    preview = client.get("/api/personal-settings/memory-documents/agent-memory-001/preview", headers=headers)
    assert preview.status_code == 200
    payload = preview.json()["document"]
    assert payload["doc_id"] == "agent-memory-001"
    assert payload["source"] == "agent"
    assert "上级授权的记忆内容" in preview.json()["content_text"]
