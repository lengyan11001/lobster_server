from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client_for_user(db_session_factory, monkeypatch, user_id: int):
    from backend.app.api.auth import get_current_user
    from backend.app.api.skills import router as skills_router
    from backend.app.core.config import settings
    from backend.app.db import get_db

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)

    app = FastAPI()
    app.include_router(skills_router, prefix="")

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    def _get_current_user_override():
        s = db_session_factory()
        try:
            from backend.app.models import User

            return s.query(User).filter(User.id == user_id).first()
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _get_current_user_override
    return TestClient(app)


def test_skill_store_default_visibility_domestic_vs_overseas(db_session, db_session_factory, monkeypatch):
    from backend.app.models import User

    domestic = User(
        email="domestic@test.local",
        hashed_password="x",
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        is_overseas_user=False,
        created_at=datetime.utcnow(),
    )
    overseas = User(
        email="overseas@test.local",
        hashed_password="x",
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        is_overseas_user=False,
        created_at=datetime.utcnow(),
    )
    db_session.add(domestic)
    db_session.add(overseas)
    db_session.commit()
    db_session.refresh(domestic)
    db_session.refresh(overseas)

    domestic_client = _client_for_user(db_session_factory, monkeypatch, domestic.id)
    overseas_client = _client_for_user(db_session_factory, monkeypatch, overseas.id)

    domestic_resp = domestic_client.get("/skills/store")
    overseas_resp = overseas_client.get("/skills/store", headers={"X-Lobster-Client-Overseas": "true"})

    assert domestic_resp.status_code == 200
    assert overseas_resp.status_code == 200

    domestic_ids = {item["id"] for item in domestic_resp.json()["packages"]}
    overseas_ids = {item["id"] for item in overseas_resp.json()["packages"]}

    assert "douyin_publish" in domestic_ids
    assert "ip_content_daily_skill" in domestic_ids
    assert "messenger_reply" not in domestic_ids
    assert "twilio_whatsapp" not in domestic_ids
    assert "bihuo_25_video_skill" not in domestic_ids
    assert "multi_clip_mixer_skill" in domestic_ids

    assert "twilio_whatsapp" in overseas_ids
    assert "youtube_publish" in overseas_ids
    assert "openclaw_memory_skill" in overseas_ids
    assert "comfly_ecommerce_detail_skill" in overseas_ids
    assert "hifly_digital_human_skill" in overseas_ids
    assert "cutcli_template_studio" in overseas_ids
    assert "comfly_veo_skill" in overseas_ids
    assert "comfly_seedance_tvc_skill" in overseas_ids
    assert "goal_video_pipeline_skill" in overseas_ids
    assert "ip_content_daily_skill" in overseas_ids
    assert "create_ppt_skill" in overseas_ids
    assert "create_video_pipeline_skill" in overseas_ids
    assert "douyin_publish" not in overseas_ids
    assert "xiaohongshu_publish" not in overseas_ids
    assert "toutiao_publish" not in overseas_ids
    assert "bihuo_25_video_skill" not in overseas_ids
    assert "multi_clip_mixer_skill" in overseas_ids


def test_skill_store_visibility_depends_on_client_header_not_user_origin(db_session, db_session_factory, monkeypatch):
    from backend.app.models import User

    user = User(
        email="switch@test.local",
        hashed_password="x",
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        is_overseas_user=False,
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    client = _client_for_user(db_session_factory, monkeypatch, user.id)

    domestic_resp = client.get("/skills/store")
    overseas_resp = client.get("/skills/store", headers={"X-Lobster-Client-Overseas": "true"})

    assert domestic_resp.status_code == 200
    assert overseas_resp.status_code == 200

    domestic_ids = {item["id"] for item in domestic_resp.json()["packages"]}
    overseas_ids = {item["id"] for item in overseas_resp.json()["packages"]}

    assert "douyin_publish" in domestic_ids
    assert "douyin_publish" not in overseas_ids
    assert "youtube_publish" not in domestic_ids
    assert "youtube_publish" in overseas_ids


def test_bihuo_25_is_visible_only_after_server_grant(db_session, db_session_factory, monkeypatch):
    from backend.app.models import User, UserSkillVisibility

    user = User(
        email="bihuo25-permission@test.local",
        hashed_password="x",
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        is_overseas_user=False,
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    client = _client_for_user(db_session_factory, monkeypatch, user.id)
    denied = client.get("/skills/bihuo-25-video-eligible")
    store_before = client.get("/skills/store")

    assert denied.status_code == 200
    assert denied.json()["allowed"] is False
    assert "bihuo_25_video_skill" not in {item["id"] for item in store_before.json()["packages"]}

    db_session.add(UserSkillVisibility(user_id=user.id, package_id="bihuo_25_video_skill"))
    db_session.commit()

    allowed = client.get("/skills/bihuo-25-video-eligible")
    store_after = client.get("/skills/store")
    package = next(item for item in store_after.json()["packages"] if item["id"] == "bihuo_25_video_skill")

    assert allowed.status_code == 200
    assert allowed.json()["allowed"] is True
    assert package["default_installed"] is False
