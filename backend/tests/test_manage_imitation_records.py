"""管理后台「跟创生成记录」：列表 + 详情（爆款提示词 / 我们请求上游的 / 上游返回的）。"""
from __future__ import annotations

import json
import pathlib
import sys
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _admin_client(db_session_factory, user_id: int):
    from backend.app.api.auth import get_current_user
    from backend.app.api.manage import router as manage_router
    from backend.app.db import get_db
    from backend.app.models import User

    app = FastAPI()
    app.include_router(manage_router)

    def get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    def user_override():
        session = db_session_factory()
        return session.query(User).filter(User.id == user_id).first()

    app.dependency_overrides[get_db] = get_db_override
    app.dependency_overrides[get_current_user] = user_override
    return TestClient(app)


def test_manage_imitation_records_list_and_detail(db_session, db_session_factory, test_user):
    from backend.app.models import DouyinImitationTask, User

    db_session.query(User).filter(User.id == test_user.id).first().role = "admin"
    db_session.commit()

    task = DouyinImitationTask(
        user_id=int(test_user.id), task_id="up-123", item_id="7688685833386071653",
        title="热门视频跟创", source_desc="720P · 复刻特效 · 抖音作品",
        provider="videoedit", model="wan2.7-videoedit",
        prompt="参考视频的特效，将这个特效应用到图片中女人身上，场景为街边。",
        upstream_request=json.dumps(
            {"model": "wan2.7-videoedit",
             "input": {"prompt": "参考视频的特效…", "media": [{"type": "video", "url": "https://tos.test/v.mp4"}]},
             "parameters": {"resolution": "720P"}}, ensure_ascii=False),
        upstream_response=json.dumps({"submit": '{"output": {"task_id": "up-123"}}'}, ensure_ascii=False),
        status="SUCCESS", billable_seconds=12, credits_charged=Decimal("1080"),
        image_url="https://tos.test/a.png", source_video_url="https://tos.test/v.mp4",
    )
    db_session.add(task)
    db_session.commit()

    client = _admin_client(db_session_factory, test_user.id)

    listed = client.get("/api/manage/imitation-records?page=1&page_size=10")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] >= 1
    item = next(row for row in body["items"] if row["task_id"] == "up-123")
    assert item["billable_seconds"] == 12
    assert item["credits_charged"] == 1080.0
    assert item["user_email"] == test_user.email

    detail = client.get("/api/manage/imitation-records/%s" % item["id"])
    assert detail.status_code == 200, detail.text
    record = detail.json()["record"]
    assert "特效" in record["prompt"]
    assert "wan2.7-videoedit" in record["upstream_request"]
    assert "up-123" in record["upstream_response"]
    assert record["image_url"] == "https://tos.test/a.png"


def test_manage_imitation_records_requires_admin(db_session, db_session_factory, test_user):
    from backend.app.models import User

    db_session.query(User).filter(User.id == test_user.id).first().role = "user"
    db_session.commit()

    client = _admin_client(db_session_factory, test_user.id)
    resp = client.get("/api/manage/imitation-records")
    assert resp.status_code == 403
