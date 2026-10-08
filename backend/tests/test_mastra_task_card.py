"""后台任务进度卡：写入/去重/最新取值（2026-09-20）。"""
from __future__ import annotations

from backend.app.services import mastra_task_card as card


def test_upsert_and_latest(db_session):
    assert card.latest_task_card(db_session, "msg-1") is None
    wrote = card.upsert_task_card(
        db_session,
        message_id="msg-1",
        user_id=9,
        status="queued",
        title="发布抖音视频",
        text="已确认，任务已转入后台执行。",
        source="approval",
    )
    assert wrote is True
    db_session.commit()
    latest = card.latest_task_card(db_session, "msg-1")
    assert latest["status"] == "queued"
    assert latest["status_label"] == "排队中"
    assert latest["card_id"] == "task:msg-1"


def test_upsert_dedupes_unchanged_state(db_session):
    kwargs = dict(message_id="msg-2", user_id=9, status="running", title="任务", text="执行中", source="online")
    assert card.upsert_task_card(db_session, **kwargs) is True
    db_session.commit()
    assert card.upsert_task_card(db_session, **kwargs) is False   # 状态没变 → 不重复写事件
    assert card.upsert_task_card(db_session, **{**kwargs, "status": "done", "artifacts": ["https://a/b.mp4"]}) is True
    db_session.commit()
    latest = card.latest_task_card(db_session, "msg-2")
    assert latest["status"] == "done"
    assert latest["artifacts"] == ["https://a/b.mp4"]


def test_artifacts_are_capped_and_deduped(db_session):
    card.upsert_task_card(
        db_session,
        message_id="msg-3",
        user_id=9,
        status="done",
        artifacts=["https://a/1.png", "https://a/1.png", "https://a/2.png"],
    )
    db_session.commit()
    latest = card.latest_task_card(db_session, "msg-3")
    assert latest["artifacts"] == ["https://a/1.png", "https://a/2.png"]

def test_overall_status_rules():
    from backend.app.services import mastra_task_card as card

    assert card.overall_status(["done", "running"]) == "running"
    assert card.overall_status(["done", "failed"]) == "failed"
    assert card.overall_status(["done", "done"]) == "done"
    assert card.overall_status(["cancelled", "cancelled"]) == "cancelled"


def test_items_are_saved_and_deduped(db_session):
    from backend.app.services import mastra_task_card as card

    items = [
        {"key": "media:image.generate:t1", "title": "image.generate", "status": "running", "text": "生成中"},
        {"key": "online:child-1", "title": "发布抖音视频", "status": "done", "artifacts": ["https://a/b.mp4"]},
    ]
    assert card.upsert_task_card(db_session, message_id="msg-items", user_id=3, status="running", items=items) is True
    db_session.commit()
    latest = card.latest_task_card(db_session, "msg-items")
    assert [item["key"] for item in latest["items"]] == ["media:image.generate:t1", "online:child-1"]
    assert latest["items"][0]["status_label"] == "执行中"
    assert latest["media_urls"] == ["https://a/b.mp4"]
    assert latest["cancellable"] is True
    # 同样的 items 再写一次：不新增事件
    assert card.upsert_task_card(db_session, message_id="msg-items", user_id=3, status="running", items=items) is False
