from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(db_session_factory, user_id: int) -> TestClient:
    from backend.app.api.auth import get_current_user
    from backend.app.api.h5_home import router
    from backend.app.db import get_db
    from backend.app.models import User

    app = FastAPI()
    app.include_router(router)

    def _get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    def _get_current_user_override():
        session = db_session_factory()
        try:
            return session.query(User).filter(User.id == user_id).first()
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _get_current_user_override
    return TestClient(app)


def _asset(db_session, *, user_id: int, asset_id: str, media_type: str = "image"):
    from backend.app.models import Asset

    row = Asset(
        asset_id=asset_id,
        user_id=user_id,
        filename=f"{asset_id}.jpg",
        media_type=media_type,
        source_url=f"https://assets.example/{asset_id}.jpg",
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_home_hero_defaults_to_builtin_image(db_session_factory, test_user):
    response = _client(db_session_factory, test_user.id).get("/api/h5/home/preferences")

    assert response.status_code == 200
    assert response.json() == {
        "hero_asset_id": None,
        "hero_url": None,
        "is_custom": False,
        "speech_enabled": True,
        "speech_voice_uri": "",
    }


def test_home_hero_can_be_saved_and_loaded(db_session, db_session_factory, test_user):
    asset = _asset(db_session, user_id=test_user.id, asset_id="hero-owned")
    client = _client(db_session_factory, test_user.id)

    saved = client.put("/api/h5/home/hero", json={"asset_id": asset.asset_id})
    loaded = client.get("/api/h5/home/preferences")

    assert saved.status_code == 200
    assert saved.json()["hero_url"] == asset.source_url
    assert loaded.json() == saved.json()


def test_home_hero_rejects_another_users_asset(db_session, db_session_factory, test_user, other_user):
    asset = _asset(db_session, user_id=other_user.id, asset_id="hero-other")

    response = _client(db_session_factory, test_user.id).put(
        "/api/h5/home/hero",
        json={"asset_id": asset.asset_id},
    )

    assert response.status_code == 404


def test_home_hero_rejects_non_image_asset(db_session, db_session_factory, test_user):
    asset = _asset(db_session, user_id=test_user.id, asset_id="hero-video", media_type="video")

    response = _client(db_session_factory, test_user.id).put(
        "/api/h5/home/hero",
        json={"asset_id": asset.asset_id},
    )

    assert response.status_code == 404


def test_speech_preference_can_be_disabled_and_persists(db_session_factory, test_user):
    client = _client(db_session_factory, test_user.id)

    saved = client.put("/api/h5/home/preferences", json={"speech_enabled": False})
    loaded = client.get("/api/h5/home/preferences")

    assert saved.status_code == 200
    assert saved.json()["speech_enabled"] is False
    assert loaded.json()["speech_enabled"] is False


def test_speech_voice_persists_and_legacy_toggle_does_not_clear_it(db_session_factory, test_user):
    client = _client(db_session_factory, test_user.id)

    selected = client.put(
        "/api/h5/home/preferences",
        json={"speech_enabled": True, "speech_voice_uri": "Microsoft Xiaoxiao Online"},
    )
    toggled = client.put("/api/h5/home/preferences", json={"speech_enabled": False})

    assert selected.status_code == 200
    assert selected.json()["speech_voice_uri"] == "Microsoft Xiaoxiao Online"
    assert toggled.json()["speech_enabled"] is False
    assert toggled.json()["speech_voice_uri"] == "Microsoft Xiaoxiao Online"


def test_h5_profile_exposes_persistent_speech_switch():
    from pathlib import Path

    h5 = Path(__file__).resolve().parents[2] / "h5_static"
    html = (h5 / "index.html").read_text(encoding="utf-8")
    script = (h5 / "h5-app.js").read_text(encoding="utf-8")

    assert 'id="speechPreferenceSwitch"' in html
    assert 'id="speechVoiceSelect"' in html
    assert 'id="speechVoicePreviewBtn"' in html
    assert 'role="switch"' in html
    assert 'api("/api/h5/home/preferences", {' in script
    assert 'speech_voice_uri: state.speechVoiceUri' in script
    assert 'window.speechSynthesis.getVoices' in script
