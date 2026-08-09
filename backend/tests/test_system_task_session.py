from __future__ import annotations

from datetime import datetime, timedelta

from backend.app.api import scheduled_tasks
from backend.app.models import H5ChatMessage, H5ChatSession, ScheduledTask
from backend.app.services.h5_chat_sessions import (
    SYSTEM_TASK_SESSION_TITLE,
    backfill_system_task_session,
    system_task_session_id,
)


def _task(db, user_id: int, *, title: str, task_kind: str = "client_workflow") -> ScheduledTask:
    row = ScheduledTask(
        user_id=user_id,
        title=title,
        task_kind=task_kind,
        content=title,
        payload={"action": "test_action", "params": {}},
        schedule_type="once",
        status="active",
        next_run_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def test_scheduled_runs_share_one_system_session(db_session, test_user):
    first = _task(db_session, test_user.id, title="第一个工作流")
    second = _task(db_session, test_user.id, title="第二个工作流")
    now = datetime.utcnow()

    first_run = scheduled_tasks._create_run_for_target(db_session, first, "desktop-1", now)
    second_run = scheduled_tasks._create_run_for_target(db_session, second, "desktop-1", now + timedelta(seconds=1))
    db_session.flush()

    expected = system_task_session_id(test_user.id)
    first_message = db_session.get(H5ChatMessage, first_run.h5_message_id)
    second_message = db_session.get(H5ChatMessage, second_run.h5_message_id)
    session = db_session.get(H5ChatSession, expected)
    assert first_message.session_id == expected
    assert second_message.session_id == expected
    assert session.title == SYSTEM_TASK_SESSION_TITLE
    assert session.last_message_at == now + timedelta(seconds=1)


def test_server_side_run_is_visible_and_completion_updates_system_message(db_session, test_user):
    task = _task(db_session, test_user.id, title="服务器任务", task_kind="ip_content_daily")
    run = scheduled_tasks._create_run_for_target(db_session, task, None, datetime.utcnow())
    db_session.flush()

    assert run.h5_message_id
    message = db_session.get(H5ChatMessage, run.h5_message_id)
    assert message.session_id == system_task_session_id(test_user.id)

    finished = datetime.utcnow()
    run.status = "completed"
    run.result_text = "服务器任务已完成"
    run.finished_at = finished
    scheduled_tasks._sync_h5_message_from_run(db_session, run, finished)
    db_session.flush()

    assert message.status == "completed"
    assert message.reply_text == "服务器任务已完成"
    assert message.finished_at == finished


def test_system_task_sessions_are_isolated_by_user(db_session, test_user, other_user):
    mine = _task(db_session, test_user.id, title="我的任务")
    theirs = _task(db_session, other_user.id, title="对方任务")

    mine_run = scheduled_tasks._create_run_for_target(db_session, mine, "desktop-1", datetime.utcnow())
    their_run = scheduled_tasks._create_run_for_target(db_session, theirs, "desktop-2", datetime.utcnow())
    db_session.flush()

    mine_message = db_session.get(H5ChatMessage, mine_run.h5_message_id)
    their_message = db_session.get(H5ChatMessage, their_run.h5_message_id)
    assert mine_message.session_id == system_task_session_id(test_user.id)
    assert their_message.session_id == system_task_session_id(other_user.id)
    assert mine_message.session_id != their_message.session_id


def test_historical_scheduled_messages_move_out_of_normal_chat(db_session, test_user):
    now = datetime.utcnow()
    normal = H5ChatSession(
        id="normal-session",
        user_id=test_user.id,
        title="普通对话",
        permission_mode="confirm",
        created_at=now,
        updated_at=now,
    )
    scheduled = H5ChatMessage(
        id="legacy-scheduled-message",
        user_id=test_user.id,
        session_id=normal.id,
        mode="scheduled_task",
        content="历史任务",
        status="completed",
        created_at=now,
        updated_at=now,
    )
    direct = H5ChatMessage(
        id="normal-chat-message",
        user_id=test_user.id,
        session_id=normal.id,
        mode="mastra",
        content="普通消息",
        status="completed",
        created_at=now,
        updated_at=now,
    )
    client_command = H5ChatMessage(
        id="legacy-client-command",
        user_id=test_user.id,
        mode="client_command",
        content="页面下发任务",
        status="completed",
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([normal, scheduled, direct, client_command])
    db_session.flush()

    backfill_system_task_session(db_session, test_user.id)
    db_session.flush()
    db_session.expire_all()

    assert db_session.get(H5ChatMessage, scheduled.id).session_id == system_task_session_id(test_user.id)
    assert db_session.get(H5ChatMessage, client_command.id).session_id == system_task_session_id(test_user.id)
    assert db_session.get(H5ChatMessage, direct.id).session_id == normal.id
