from starlette.requests import Request


def _fake_request() -> Request:
    return Request({"type": "http", "headers": []})


def test_voice_options_merge_mine_and_upstream_system(db_session, test_user, monkeypatch):
    """数字人/混剪的声音选择：一次返回我的声音 + 上游系统声音，并去重。"""
    from backend.app.api import hifly_assets
    from backend.app.models import UserHiflyVoiceAsset
    from backend.app.services import device_tts

    db_session.add(
        UserHiflyVoiceAsset(
            user_id=test_user.id,
            title="我的克隆",
            status="success",
            hifly_task_id="voice-task-1",
            hifly_voice_id="minimax_clone_1",
            meta={"provider": "minimax"},
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        "backend.app.api.cutcli_templates._load_sutui_token_for_stt",
        lambda db, uid: ("test-token", "test"),
    )
    monkeypatch.setattr(
        device_tts,
        "list_voices",
        lambda token: [
            {"id": "male-qn-qingse", "name": "青涩男声"},
            {"id": "minimax_clone_1", "name": "重复克隆"},
        ],
    )

    result = hifly_assets.hifly_voice_options(
        request=_fake_request(),
        current_user=test_user,
        db=db_session,
    )

    assert result["ok"] is True
    assert result["mine_count"] == 1
    assert result["system_count"] == 1
    assert result["mine"][0]["source"] == "mine"
    assert result["mine"][0]["section_label"] == "我的声音"
    assert result["system"][0]["source"] == "system"
    assert result["system"][0]["section_label"] == "系统声音"
    assert [item["voice"] for item in result["items"]] == ["minimax_clone_1", "male-qn-qingse"]
    assert result["default_id"] == "minimax_clone_1"


def test_voice_options_endpoint_route(db_session, test_user, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api.auth import get_current_user
    from backend.app.api.hifly_assets import router as hifly_router
    from backend.app.db import get_db
    from backend.app.services import device_tts

    monkeypatch.setattr(
        "backend.app.api.cutcli_templates._load_sutui_token_for_stt",
        lambda db, uid: ("test-token", "test"),
    )
    monkeypatch.setattr(device_tts, "list_voices", lambda token: [{"id": "male-qn-qingse", "name": "青涩男声"}])

    app = FastAPI()
    app.include_router(hifly_router, prefix="")
    app.dependency_overrides[get_current_user] = lambda: test_user
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with TestClient(app) as client:
            resp = client.get("/api/hifly/voice/options")
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            assert payload["ok"] is True
            assert payload["system_count"] == 1
            assert payload["system"][0]["voice"] == "male-qn-qingse"
    finally:
        app.dependency_overrides.clear()
