from __future__ import annotations
from datetime import datetime
from decimal import Decimal

from tests.test_skill_store_visibility import _client_for_user


def _mk_user(db_session, email="entry@test.local"):
    from backend.app.models import User
    u = User(email=email, hashed_password="x", credits=Decimal("100.0000"), role="user",
             preferred_model="sutui", is_overseas_user=False, created_at=datetime.utcnow())
    db_session.add(u); db_session.commit(); db_session.refresh(u)
    return u


def test_skill_store_forwards_registry_entry(db_session, db_session_factory, monkeypatch):
    """/skills/store 必须把注册表里的 entry 原样下发，否则客户端拿不到入口（只能显示安装）。"""
    u = _mk_user(db_session)
    client = _client_for_user(db_session_factory, monkeypatch, u.id)
    resp = client.get("/skills/store")
    assert resp.status_code == 200
    pkgs = {p["id"]: p for p in resp.json()["packages"]}

    assert pkgs["multi_clip_mixer_skill"]["entry"]["target"] == "multi-clip-mixer"
    assert pkgs["ip_content_oral_skill"]["entry"]["arg"] == "oral"
    assert pkgs["ip_content_moments_skill"]["entry"]["arg"] == "moments"
    assert pkgs["wechat_channels_transcript_skill"]["entry"]["target"] == "wechat-channels-transcript"
    assert pkgs["ai_3d_model_skill"]["entry"]["target"] == "ai-3d-model"
    assert pkgs["liepin_recruit_skill"]["entry"]["target"] == "liepin-recruit"
    assert pkgs["digital_human_koubo_skill"]["entry"]["target"] == "digital-human-koubo"
    # 没有配置 entry 的包不应凭空多出该字段
    assert "entry" not in pkgs.get("juhe_wechat_skill", {})