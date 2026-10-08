"""管理后台（bhzn.top/admin）「跟创生成记录」：列表 + 详情（提示词 / 上游请求 / 上游返回）。

2026-10-05 用户澄清：manage.bhzn.top 是「项目管理」，管理后台是 bhzn.top/admin，
这块能力放在 admin.py（/api/admin/imitation-records），页面 /admin/imitation-records。
"""
from __future__ import annotations

import json
import pathlib
import sys
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _seed_task(db_session, user_id: int):
    from backend.app.models import DouyinImitationTask

    task = DouyinImitationTask(
        user_id=int(user_id), task_id="up-123", item_id="7688685833386071653",
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
    return task


def test_admin_imitation_records_list_and_detail(db_session, test_user):
    from backend.app.api import admin as admin_api

    task = _seed_task(db_session, test_user.id)

    listed = admin_api.admin_list_imitation_records(
        user_id=0, status="", q="", page=1, page_size=10, ctx=None, db=db_session
    )
    assert listed["total"] >= 1
    item = next(row for row in listed["items"] if row["task_id"] == "up-123")
    assert item["billable_seconds"] == 12
    assert item["credits_charged"] == 1080.0
    assert item["user_email"] == test_user.email

    # 关键词能搜到（任务号 / 提示词）
    hit = admin_api.admin_list_imitation_records(
        user_id=0, status="", q="特效", page=1, page_size=10, ctx=None, db=db_session
    )
    assert any(row["task_id"] == "up-123" for row in hit["items"])

    detail = admin_api.admin_get_imitation_record(record_id=int(task.id), ctx=None, db=db_session)
    record = detail["record"]
    assert "特效" in record["prompt"]
    assert "wan2.7-videoedit" in record["upstream_request"]
    assert "up-123" in record["upstream_response"]
    assert record["image_url"] == "https://tos.test/a.png"


def test_admin_imitation_records_routes_and_page_exist():
    from backend.app.api.admin import router

    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/api/admin/imitation-records" in paths
    assert "/api/admin/imitation-records/{record_id}" in paths
    assert "/admin/imitation-records" in paths, "页面要挂在管理后台 /admin 前缀下"

    page = pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "imitation-records.html"
    assert page.is_file()
    body = page.read_text(encoding="utf-8")
    for marker in ("跟创生成记录", "爆款提示词", "我们请求上游的", "上游返回的", "/api/admin/imitation-records", "admin_token"):
        assert marker in body, marker
