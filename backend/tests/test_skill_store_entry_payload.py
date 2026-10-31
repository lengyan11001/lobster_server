from __future__ import annotations
from datetime import datetime
from decimal import Decimal

from tests.test_skill_store_visibility import _client_for_user

ENTRY_TARGETS = {
    "multi_clip_mixer_skill": ("multi-clip-mixer", None),
    "ip_content_oral_skill": ("ip-content-studio", "oral"),
    "ip_content_moments_skill": ("ip-content-studio", "moments"),
    "viral_video_remix_skill": ("viral-video-remix", None),
    "liepin_recruit_skill": ("liepin-recruit", None),
    "wechat_channels_transcript_skill": ("wechat-channels-transcript", None),
    "ai_3d_model_skill": ("ai-3d-model", None),
    "digital_human_koubo_skill": ("digital-human-koubo", None),
    "meta_social": ("meta-social", None),
    "shanjian_smart_clip": ("shanjian-smart-clip", None),
    "personal_telegram_assistant": ("personal-telegram", None),
}


def _mk(db_session, email, role="user"):
    from backend.app.models import User
    u = User(email=email, hashed_password="x", credits=Decimal("100.0000"), role=role,
             preferred_model="sutui", is_overseas_user=False, created_at=datetime.utcnow())
    db_session.add(u); db_session.commit(); db_session.refresh(u)
    return u


def test_skill_store_forwards_registry_entry(db_session, db_session_factory, monkeypatch):
    """/skills/store 必须把注册表 entry 原样下发；否则客户端只能显示「安装」，进不去工作台。"""
    user = _mk(db_session, "entry-user@test.local")
    client = _client_for_user(db_session_factory, monkeypatch, user.id)
    resp = client.get("/skills/store")
    assert resp.status_code == 200
    pkgs = {p["id"]: p for p in resp.json()["packages"]}
    # 普通用户默认可见的包：entry 必须到
    for pid in ("multi_clip_mixer_skill", "ip_content_oral_skill", "ip_content_moments_skill", "viral_video_remix_skill"):
        assert pid in pkgs, pid
        target, arg = ENTRY_TARGETS[pid]
        assert pkgs[pid]["entry"]["target"] == target
        assert pkgs[pid]["entry"].get("arg") == arg
    # 没有配置 entry 的包不应凭空多出该字段
    assert "entry" not in pkgs.get("web_research_skill", {})


def test_skill_store_entry_for_admin_visible_packages(db_session, db_session_factory, monkeypatch):
    admin = _mk(db_session, "entry-admin@test.local", role="admin")
    client = _client_for_user(db_session_factory, monkeypatch, admin.id)
    pkgs = {p["id"]: p for p in client.get("/skills/store").json()["packages"]}
    for pid, (target, arg) in ENTRY_TARGETS.items():
        assert pid in pkgs, "admin 应该能看到 " + pid
        assert pkgs[pid]["entry"]["target"] == target, pid
        assert pkgs[pid]["entry"].get("arg") == arg, pid