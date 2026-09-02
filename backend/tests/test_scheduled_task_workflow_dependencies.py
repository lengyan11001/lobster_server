from datetime import datetime, timedelta

from backend.app.api import scheduled_tasks
from backend.app.models import ScheduledTask, ScheduledTaskRun


def _workflow_task(
    user_id: int,
    *,
    node_id: str,
    template_id: str = "template-1",
    action: str = "local_bestseller_daily_video",
    next_run_at: datetime | None = None,
    status: str = "active",
) -> ScheduledTask:
    now = datetime.utcnow()
    return ScheduledTask(
        user_id=user_id,
        title=node_id,
        task_kind="client_workflow",
        content=node_id,
        payload={
            "action": action,
            "h5_context": {
                "workflow_node_id": node_id,
                "workflow_template_id": template_id,
            },
            "schedule_config": {
                "daily_times": ["01:00"],
                "timezone_offset_minutes": 480,
            },
        },
        schedule_type="daily_times",
        target_installation_ids=["device-1"],
        status=status,
        next_run_at=next_run_at if next_run_at is not None else now,
        created_at=now,
        updated_at=now,
    )


def _child_task(user_id: int, *, parent_node_id: str, next_run_at: datetime) -> ScheduledTask:
    now = datetime.utcnow()
    return ScheduledTask(
        user_id=user_id,
        title="publish child",
        task_kind="client_workflow",
        content="publish child",
        payload={
            "action": "publish_content",
            "params": {
                "source_mode": "parent_latest_run",
                "source_workflow_node_id": parent_node_id,
            },
            "h5_context": {
                "workflow_node_id": "child-node",
                "workflow_template_id": "template-1",
                "workflow_parent_node_id": parent_node_id,
            },
            "schedule_config": {
                "daily_times": ["06:00"],
                "timezone_offset_minutes": 480,
            },
        },
        schedule_type="daily_times",
        target_installation_ids=["device-1"],
        status="active",
        next_run_at=next_run_at,
        created_at=now,
        updated_at=now,
    )


def _parent_run(
    *,
    user_id: int,
    task_id: int,
    status: str,
    scheduled_at: datetime,
    result_payload: dict,
) -> ScheduledTaskRun:
    return ScheduledTaskRun(
        id=f"parent-{status}-{task_id}",
        task_id=task_id,
        user_id=user_id,
        installation_id="device-1",
        claimed_by_installation_id="device-1" if status == "processing" else None,
        title="parent",
        task_kind="client_workflow",
        content="parent",
        payload={
            "action": "local_bestseller_daily_video",
            "h5_context": {
                "workflow_node_id": "parent-node",
                "workflow_template_id": "template-1",
            },
        },
        status=status,
        progress={"scheduled_at": scheduled_at.isoformat()},
        result_payload=result_payload,
        created_at=scheduled_at,
        updated_at=scheduled_at,
    )


def test_workflow_child_is_ready_after_parent_materializes(db_session, test_user):
    now = datetime.utcnow()
    parent = _workflow_task(test_user.id, node_id="parent-node", next_run_at=now + timedelta(days=1))
    child = _child_task(test_user.id, parent_node_id="parent-node", next_run_at=now)
    db_session.add_all([parent, child])
    db_session.flush()
    db_session.add(
        _parent_run(
            user_id=test_user.id,
            task_id=parent.id,
            status="completed",
            scheduled_at=now - timedelta(minutes=30),
            result_payload={"local_result": {"asset_id": "asset-1", "media_type": "video"}},
        )
    )
    db_session.commit()

    assert (
        scheduled_tasks._workflow_dependency_state(
            db_session,
            child,
            scheduled_at=now,
            now=now,
            installation_id="device-1",
        )
        == "ready"
    )


def test_workflow_child_waits_while_parent_is_processing(db_session, test_user):
    now = datetime.utcnow()
    parent = _workflow_task(test_user.id, node_id="parent-node", next_run_at=now)
    child = _child_task(test_user.id, parent_node_id="parent-node", next_run_at=now)
    db_session.add_all([parent, child])
    db_session.flush()
    db_session.add(
        _parent_run(
            user_id=test_user.id,
            task_id=parent.id,
            status="processing",
            scheduled_at=now - timedelta(minutes=5),
            result_payload={},
        )
    )
    db_session.commit()

    assert (
        scheduled_tasks._workflow_dependency_state(
            db_session,
            child,
            scheduled_at=now,
            now=now,
            installation_id="device-1",
        )
        == "waiting"
    )


def test_workflow_child_is_skipped_when_parent_missed_window(db_session, test_user):
    now = datetime.utcnow()
    parent = _workflow_task(
        test_user.id,
        node_id="parent-node",
        next_run_at=now + timedelta(days=1),
    )
    child = _child_task(test_user.id, parent_node_id="parent-node", next_run_at=now)
    db_session.add_all([parent, child])
    db_session.commit()

    assert (
        scheduled_tasks._workflow_dependency_state(
            db_session,
            child,
            scheduled_at=now,
            now=now,
            installation_id="device-1",
        )
        == "skip"
    )


def test_enqueue_does_not_materialize_child_after_parent_missed_window(db_session, test_user):
    now = datetime.utcnow()
    parent = _workflow_task(
        test_user.id,
        node_id="parent-node",
        next_run_at=now + timedelta(days=1),
    )
    child = _child_task(test_user.id, parent_node_id="parent-node", next_run_at=now)
    db_session.add_all([parent, child])
    db_session.commit()

    assert scheduled_tasks._enqueue_due_tasks(db_session, test_user.id, "device-1") == 0
    assert db_session.query(ScheduledTaskRun).filter(ScheduledTaskRun.task_id == child.id).count() == 0
    assert child.next_run_at is not None and child.next_run_at > now


def test_workflow_child_deadline_follows_late_parent_completion(db_session, test_user):
    scheduled_at = datetime(2026, 8, 20, 5, 30)
    parent_finished_at = datetime(2026, 8, 20, 7, 0)
    now = datetime(2026, 8, 20, 7, 30)
    parent = _workflow_task(
        test_user.id,
        node_id="parent-node",
        next_run_at=scheduled_at,
    )
    child = _child_task(
        test_user.id,
        parent_node_id="parent-node",
        next_run_at=scheduled_at,
    )
    child.payload["h5_context"].update(
        {
            "workflow_node_time": "13:30",
            "workflow_node_end_time": "14:30",
        }
    )
    db_session.add_all([parent, child])
    db_session.flush()
    parent_run = _parent_run(
        user_id=test_user.id,
        task_id=parent.id,
        status="completed",
        scheduled_at=scheduled_at,
        result_payload={"local_result": {"asset_id": "asset-1", "media_type": "video"}},
    )
    parent_run.finished_at = parent_finished_at
    parent_run.updated_at = parent_finished_at
    db_session.add(parent_run)
    db_session.commit()

    deadline = scheduled_tasks._scheduled_occurrence_deadline(
        db_session,
        child,
        scheduled_at,
        installation_id="device-1",
    )

    assert deadline == datetime(2026, 8, 20, 8, 0)
    assert not scheduled_tasks._scheduled_occurrence_expired(
        db_session,
        child,
        scheduled_at,
        now,
        installation_id="device-1",
    )

    run = scheduled_tasks._create_run_for_target(
        db_session,
        child,
        "device-1",
        now,
        scheduled_at=scheduled_at,
    )
    assert run.payload["h5_context"]["workflow_node_deadline_at"] == "2026-08-20T08:00:00"
    assert run.payload["h5_context"]["workflow_node_effective_start_at"] == "2026-08-20T07:00:00"
