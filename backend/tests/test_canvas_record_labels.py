"""画布内容记录标题口径（2026-10-05 用户确认）：模型名 + 提示词摘要，不能再写接口路径。

背景：画布是拿 api/v3/tasks/query 轮询拿结果的，那个请求体里没有 model/prompt，
以前登记内容记录时只能拿接口路径当标题 → 用户看着"生成的图在内容记录里找不到"。
"""
from __future__ import annotations

import json
from datetime import datetime

from backend.app.api import canvas_hub
from backend.app.api.canvas_hub import (
    _looks_like_api_path,
    _model_display_name,
    canvas_task_meta,
    content_record_labels,
    register_content_record,
    repair_canvas_record_titles,
)


def test_model_display_name_strips_vendor_prefix():
    assert _model_display_name("fal-ai/nano-banana-2") == "nano-banana-2"
    assert _model_display_name("openai/gpt-image-2") == "gpt-image-2"
    assert _model_display_name("minimax/h3") == "h3"
    assert _model_display_name("api/v3/tasks/query") == ""
    assert _model_display_name("") == ""


def test_api_path_is_not_a_title():
    assert _looks_like_api_path("api/v3/tasks/query 生成")
    assert _looks_like_api_path("/v3/tasks/create")
    assert not _looks_like_api_path("nano-banana-2 · 一只在街边的老虎")


def test_labels_use_model_and_prompt_head():
    title, summary, filename = content_record_labels(
        model="fal-ai/nano-banana-2",
        prompt="  一只在街边的老虎  ",
        media_type="image",
        url="https://cdn-hk.51sux.com/v3-tasks/nano-image/2026/10/05/a.png",
        asked_title="api/v3/tasks/query 生成",
    )
    assert title == "nano-banana-2 · 一只在街边的老虎"
    assert summary == "一只在街边的老虎"
    assert filename.startswith("nano-banana-2_") and filename.endswith(".png")


def test_labels_fall_back_to_model_only():
    title, summary, filename = content_record_labels(model="openai/gpt-image-2", url="https://cdn/a.jpg")
    assert title == "gpt-image-2 生成"
    assert summary == "gpt-image-2 生成"
    assert filename.endswith(".jpg")


def test_human_title_is_kept():
    title, _, _ = content_record_labels(model="m/x", prompt="p", asked_title="我的封面图")
    assert title == "我的封面图"


def test_video_filename_takes_ext_from_url():
    _, _, filename = content_record_labels(model="minimax/hailuo-2", media_type="video",
                                           url="https://cdn/x.mp4")
    assert filename.endswith(".mp4")


def _seed_canvas_task(db, uid, task_id="task-1", model="fal-ai/nano-banana-2",
                      prompt="一只在街边的老虎"):
    canvas_hub.add_canvas_task(
        db, uid, model, "api/v3/tasks/create",
        json.dumps({"data": {"task_id": task_id}}).encode(),
        prompt=prompt, charged=72,
    )


def test_register_content_record_rebuilds_bad_title_from_canvas_task(db_session, test_user):
    canvas_hub.ensure_tables(db_session)
    _seed_canvas_task(db_session, test_user.id)
    assert canvas_task_meta(db_session, "task-1")["model"] == "fal-ai/nano-banana-2"
    assert canvas_task_meta(db_session, "task-1")["prompt"] == "一只在街边的老虎"

    ok = register_content_record(
        db_session, test_user.id, "https://cdn.example.test/out.png",
        media_type="image", task_id="task-1", model="",
        title="api/v3/tasks/query 生成",
    )
    assert ok
    from backend.app.models import UserContentRecord

    row = db_session.query(UserContentRecord).filter_by(user_id=test_user.id, source="canvas").one()
    assert row.title == "nano-banana-2 · 一只在街边的老虎"
    assert row.summary == "一只在街边的老虎"
    assert row.filename.startswith("nano-banana-2_")
    assert row.filename.endswith(".png")
    assert row.meta["model"] == "fal-ai/nano-banana-2"
    assert row.meta["prompt"] == "一只在街边的老虎"


def test_register_content_record_is_idempotent_and_repairs(db_session, test_user):
    canvas_hub.ensure_tables(db_session)
    _seed_canvas_task(db_session, test_user.id, task_id="task-2")
    for _ in range(2):
        assert register_content_record(db_session, test_user.id, "https://cdn.example.test/out2.png",
                                       media_type="image", task_id="task-2")
    from backend.app.models import UserContentRecord

    rows = db_session.query(UserContentRecord).filter_by(user_id=test_user.id, source="canvas").all()
    assert len(rows) == 1
    assert rows[0].title == "nano-banana-2 · 一只在街边的老虎"


def test_repair_canvas_record_titles_fixes_legacy_rows(db_session, test_user):
    canvas_hub.ensure_tables(db_session)
    from backend.app.models import UserContentRecord

    db_session.add(UserContentRecord(
        user_id=test_user.id, source="canvas", source_id="legacy-task",
        kind="image", title="api/v3/tasks/query 生成", summary="api/v3/tasks/query 生成",
        file_url="https://cdn.example.test/legacy.png", status="completed",
        meta={"model": "openai/gpt-image-2", "media_type": "image",
              "url": "https://cdn.example.test/legacy.png"},
        source_created_at=datetime.utcnow(),
    ))
    db_session.commit()

    assert repair_canvas_record_titles(db_session, user_id=test_user.id) == 1
    row = db_session.query(UserContentRecord).filter_by(source_id="legacy-task").one()
    assert row.title == "gpt-image-2 生成"
    assert row.summary == "gpt-image-2 生成"
    assert row.filename.startswith("gpt-image-2_")
    # 再跑一次不应该再改（幂等）
    assert repair_canvas_record_titles(db_session, user_id=test_user.id) == 0