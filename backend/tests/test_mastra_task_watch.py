"""长任务看护：完成/失败主动推回会话（2026-09-20）。

覆盖：通知文案（成功带产物 / 失败带原因）、幂等、媒体任务扫描、产物地址提取。
"""
from __future__ import annotations

from datetime import datetime

from backend.app.models import H5ChatEvent, H5ChatMessage, ScheduledTaskRun
from backend.app.services import mastra_task_watch as watch


def _child(db, *, user_id: int = 4242, status: str = "completed", run_status: str = "completed", payload=None):
    now = datetime.utcnow()
    suffix = now.timestamp()
    root = H5ChatMessage(
        id=f"root-{user_id}-{suffix}",
        user_id=user_id,
        mode="mastra",
        content="帮我发一条抖音",
        status="completed",
        created_at=now,
        updated_at=now,
    )
    child = H5ChatMessage(
        id=f"child-{user_id}-{suffix}",
        user_id=user_id,
        installation_id="u-test",
        parent_message_id=root.id,
        mode="mastra",
        content="发布内容",
        status=status,
        reply_text="已发布",
        created_at=now,
        updated_at=now,
        finished_at=now,
    )
    db.add(root)
    db.add(child)
    db.add(
        ScheduledTaskRun(
            id=f"run-{user_id}-{suffix}",
            task_id=1,
            user_id=user_id,
            task_kind="client_workflow",
            title="发布抖音视频",
            payload={},
            status=run_status,
            result_text="视频已发布",
            result_payload=payload or {"url": "https://example.com/v.mp4"},
            h5_message_id=child.id,
            created_at=now,
            updated_at=now,
            finished_at=now,
        )
    )
    db.commit()
    return root, child


def test_online_child_notice_success_includes_asset(db_session):
    root, child = _child(db_session)
    notice = watch._online_child_notice(db_session, child)
    assert notice is not None
    key, text, root_id = notice
    assert key.startswith("online:")
    assert root_id == root.id
    assert "已完成" in text
    assert "https://example.com/v.mp4" in text


def test_online_child_notice_failure_reports_reason(db_session):
    root, child = _child(db_session, status="failed", run_status="failed", payload={})
    run = db_session.query(ScheduledTaskRun).filter(ScheduledTaskRun.h5_message_id == child.id).first()
    run.error = "抖音登录态失效"
    db_session.commit()
    notice = watch._online_child_notice(db_session, child)
    assert notice is not None
    _key, text, _root_id = notice
    assert "失败" in text
    assert "登录态失效" in text


def test_push_notice_is_idempotent(db_session):
    root, child = _child(db_session, user_id=4243)
    key, text, root_id = watch._online_child_notice(db_session, child)
    watch._push_notice(
        db_session,
        user_id=child.user_id,
        installation_id="u-test",
        text=text,
        root_message_id=root_id,
        key=key,
    )
    before = (
        db_session.query(H5ChatMessage)
        .filter(H5ChatMessage.user_id == 4243, H5ChatMessage.mode == "scheduled_task")
        .count()
    )
    assert before == 1
    assert watch._notice_already_sent(db_session, root_id, key) is True
    watch._push_notice(
        db_session,
        user_id=child.user_id,
        installation_id="u-test",
        text=text,
        root_message_id=root_id,
        key=key,
    )
    after = (
        db_session.query(H5ChatMessage)
        .filter(H5ChatMessage.user_id == 4243, H5ChatMessage.mode == "scheduled_task")
        .count()
    )
    assert after == 1


def test_media_scan_only_picks_unfinished(db_session):
    now = datetime.utcnow()
    db_session.add(
        H5ChatEvent(
            message_id="msg-1",
            user_id=4244,
            event_type="progress",
            payload={
                "text": "生成中",
                "media_task": {
                    "capability_id": "image.generate",
                    "task_id": "task-running",
                    "status": "processing",
                    "terminal": False,
                },
            },
            created_at=now,
        )
    )
    db_session.add(
        H5ChatEvent(
            message_id="msg-1",
            user_id=4244,
            event_type="progress",
            payload={
                "text": "已完成",
                "media_task": {
                    "capability_id": "image.generate",
                    "task_id": "task-done",
                    "status": "completed",
                    "terminal": True,
                },
            },
            created_at=now,
        )
    )
    db_session.commit()
    pending = watch._media_tasks_from_events(db_session, now)
    assert {item["task_id"] for item in pending} == {"task-running"}


def test_collect_urls_finds_nested_assets():
    payload = {
        "result": {
            "saved_assets": [{"asset_id": "a1", "url": "https://example.com/a.png"}],
            "output": {"videos": [{"video_url": "https://example.com/b.mp4"}]},
        }
    }
    urls = watch._collect_urls(payload)
    assert "https://example.com/a.png" in urls
    assert "https://example.com/b.mp4" in urls


def test_truncate_keeps_tail_marker():
    assert watch._truncate("x" * 500, 100).endswith("…")

def test_nested_result_shape_is_unwrapped():
    """MCP 可能回 {capability_id, result:{status,...}}，要能识别终态与错误。"""
    parsed = {"capability_id": "task.get_result", "result": {"status": "failed", "error": {"message": "上游 404 任务不存在"}}}
    merged = {**parsed, **parsed["result"]}
    assert watch._media_status(merged) == "failed"
    assert "404" in watch._task_error_text(merged)


def test_task_error_text_handles_shapes():
    assert watch._task_error_text({"error": "boom"}) == "boom"
    assert watch._task_error_text({"error": {"message": "boom2"}}) == "boom2"
    assert watch._task_error_text({"detail": {"message": "boom3"}}) == "boom3"
    assert watch._task_error_text({}) == ""
