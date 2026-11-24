import asyncio

from backend.app.models import TaskWakeEvent
from backend.app.services import task_wake


class _Task:
    def __init__(self, payload):
        self.payload = payload


def test_record_appends_row(db_session):
    task_wake.reset_state_for_tests()
    task_wake.record(db_session, user_id=31, installation_id="dev-a", kind="scheduled_task")
    db_session.commit()
    rows = db_session.query(TaskWakeEvent).all()
    assert len(rows) == 1
    assert rows[0].user_id == 31
    assert rows[0].installation_id == "dev-a"


def test_dispatch_matches_installation_and_wildcard():
    task_wake.reset_state_for_tests()

    async def run():
        q_exact = await task_wake.subscribe(31, "dev-a")
        q_wild = await task_wake.subscribe(31, "")
        q_other = await task_wake.subscribe(31, "dev-b")
        q_user2 = await task_wake.subscribe(32, "")
        task_wake._dispatch_local(
            {"id": 1, "user_id": 31, "installation_id": "dev-a", "kind": "scheduled_task"}
        )
        assert q_exact.qsize() == 1
        assert q_wild.qsize() == 1
        assert q_other.qsize() == 0
        assert q_user2.qsize() == 0

    asyncio.run(run())


def test_wake_scope_only_live_executor():
    from backend.app.api import scheduled_tasks

    assert scheduled_tasks._executor_wake_enabled(_Task({"live_executor": True}))
    assert not scheduled_tasks._executor_wake_enabled(
        _Task({"h5_context": {"source": "h5_live_executor"}})
    )
    assert not scheduled_tasks._executor_wake_enabled(_Task(None))


def test_stream_route_registered():
    from backend.app.api import scheduled_tasks

    paths = {getattr(route, "path", "") for route in scheduled_tasks.router.routes}
    assert "/api/scheduled-tasks/stream" in paths


def test_executor_run_records_wake_row(db_session, test_user):
    from datetime import datetime

    from backend.app.api import scheduled_tasks
    from backend.app.models import ScheduledTask

    now = datetime.utcnow()
    task = ScheduledTask(
        user_id=test_user.id,
        title="抖音养号",
        task_kind="client_workflow",
        content="H5 现场执行台：抖音养号",
        payload={"action": "douyin_nurture", "params": {}, "live_executor": True},
        schedule_type="once",
        target_installation_ids=["dev-a"],
        status="active",
        next_run_at=now,
        run_count=0,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    db_session.commit()

    scheduled_tasks._create_run_for_target(db_session, task, "dev-a", now)
    db_session.commit()

    rows = db_session.query(TaskWakeEvent).all()
    assert len(rows) == 1
    assert rows[0].installation_id == "dev-a"


def test_normal_workflow_run_does_not_wake(db_session, test_user):
    from datetime import datetime

    from backend.app.api import scheduled_tasks
    from backend.app.models import ScheduledTask

    now = datetime.utcnow()
    task = ScheduledTask(
        user_id=test_user.id,
        title="普通工作流",
        task_kind="client_workflow",
        content="workflow",
        payload={"action": "douyin_nurture", "params": {}},
        schedule_type="once",
        target_installation_ids=["dev-a"],
        status="active",
        next_run_at=now,
        run_count=0,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    db_session.commit()

    scheduled_tasks._create_run_for_target(db_session, task, "dev-a", now)
    db_session.commit()

    assert db_session.query(TaskWakeEvent).count() == 0
