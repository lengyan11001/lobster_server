from __future__ import annotations

from datetime import datetime
from pathlib import Path

from starlette.requests import Request

from backend.app.api import h5_chat, scheduled_tasks
from backend.app.api import h5_recorder
from backend.app.models import (
    DouyinDashboardDeviceState,
    H5ChatDevicePresence,
    RecorderAudioRecord,
    ScheduledTask,
    ScheduledTaskRun,
)


def _presence(user_id: int, installation_id: str, account_id: str) -> H5ChatDevicePresence:
    now = datetime.utcnow()
    return H5ChatDevicePresence(
        user_id=user_id,
        installation_id=installation_id,
        display_name=f"Online {installation_id}",
        account_payload={
            "accounts": [
                {
                    "platform": "douyin",
                    "account_id": account_id,
                    "nickname": account_id,
                    "status": "online",
                    "online": True,
                }
            ],
            "wechat_contacts": [{"name": f"contact-{installation_id}"}],
        },
        last_seen_at=now,
        created_at=now,
    )


def test_mounted_accounts_are_filtered_by_selected_device(db_session, test_user):
    now = datetime.utcnow()
    db_session.add_all(
        [
            _presence(test_user.id, "device-a", "account-a"),
            _presence(test_user.id, "device-b", "account-b"),
            DouyinDashboardDeviceState(
                user_id=test_user.id,
                installation_id="device-a",
                payload={"accounts": [{"account_id": "lead-a", "nickname": "lead-a", "online": True}]},
                created_at=now,
                updated_at=now,
            ),
            DouyinDashboardDeviceState(
                user_id=test_user.id,
                installation_id="device-b",
                payload={"accounts": [{"account_id": "lead-b", "nickname": "lead-b", "online": True}]},
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    db_session.commit()

    payload = h5_chat._mounted_accounts_payload(db_session, test_user.id, installation_id="device-a")

    assert {row["installation_id"] for row in payload["devices"]} == {"device-a"}
    assert payload["accounts"]
    assert {row["installation_id"] for row in payload["accounts"]} == {"device-a"}
    assert {row["scope"] for row in payload["accounts"]} == {"wechat", "publish", "douyin"}
    assert "account-b" not in {row.get("account_id") for row in payload["accounts"]}


def test_task_and_run_lists_are_filtered_by_selected_device(db_session, test_user, monkeypatch):
    now = datetime.utcnow()
    tasks = [
        ScheduledTask(
            user_id=test_user.id,
            title="device a task",
            task_kind="client_workflow",
            content="a",
            target_installation_ids=["device-a"],
            status="active",
            created_at=now,
            updated_at=now,
        ),
        ScheduledTask(
            user_id=test_user.id,
            title="device b task",
            task_kind="client_workflow",
            content="b",
            target_installation_ids=["device-b"],
            status="active",
            created_at=now,
            updated_at=now,
        ),
        ScheduledTask(
            user_id=test_user.id,
            title="server task",
            task_kind="ip_content_daily",
            content="server",
            target_installation_ids=[],
            status="active",
            created_at=now,
            updated_at=now,
        ),
    ]
    db_session.add_all(tasks)
    db_session.flush()
    db_session.add_all(
        [
            ScheduledTaskRun(
                id="run-a",
                task_id=tasks[0].id,
                user_id=test_user.id,
                installation_id="device-a",
                title="run a",
                task_kind="client_workflow",
                content="a",
                status="completed",
                created_at=now,
                updated_at=now,
            ),
            ScheduledTaskRun(
                id="run-b",
                task_id=tasks[1].id,
                user_id=test_user.id,
                installation_id="device-b",
                title="run b",
                task_kind="client_workflow",
                content="b",
                status="completed",
                created_at=now,
                updated_at=now,
            ),
            ScheduledTaskRun(
                id="run-server",
                task_id=tasks[2].id,
                user_id=test_user.id,
                title="server run",
                task_kind="ip_content_daily",
                content="server",
                status="completed",
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(scheduled_tasks, "online_user_for_mobile_user", lambda _db, user: user)
    monkeypatch.setattr(scheduled_tasks, "_enqueue_due_tasks", lambda *_args, **_kwargs: None)

    task_payload = scheduled_tasks.list_scheduled_tasks(
        limit=20,
        offset=0,
        installation_id="device-a",
        current_user=test_user,
        db=db_session,
    )
    request = Request({"type": "http", "method": "GET", "path": "/api/scheduled-tasks/runs", "headers": []})
    run_payload = scheduled_tasks.list_scheduled_task_runs(
        request=request,
        limit=20,
        offset=0,
        compact=True,
        date="",
        timezone_offset_minutes=480,
        installation_id="device-a",
        current_user=test_user,
        db=db_session,
    )

    assert {row["title"] for row in task_payload["tasks"]} == {"device a task", "server task"}
    assert {row["id"] for row in run_payload["runs"]} == {"run-a", "run-server"}
    assert task_payload["pagination"]["total"] == 2
    assert run_payload["pagination"]["total"] == 2


def test_h5_task_center_can_cancel_an_active_run(db_session, test_user, monkeypatch):
    now = datetime.utcnow()
    run = ScheduledTaskRun(
        id="run-to-cancel",
        user_id=test_user.id,
        installation_id="device-a",
        title="active task",
        task_kind="client_workflow",
        content="working",
        status="processing",
        progress={"stage": "working", "text": "正在执行"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(run)
    db_session.commit()
    monkeypatch.setattr(scheduled_tasks, "online_user_for_mobile_user", lambda _db, user: user)

    payload = scheduled_tasks.cancel_scheduled_task_run(
        "run-to-cancel",
        current_user=test_user,
        db=db_session,
    )

    db_session.refresh(run)
    assert payload["cancelled"] is True
    assert run.status == "cancelled"
    assert run.finished_at is not None
    assert run.progress["reason"] == "cancelled_by_user"


def test_recorder_lists_use_selected_device_and_keep_legacy_records(db_session, test_user):
    now = datetime.utcnow()
    db_session.add_all(
        [
            RecorderAudioRecord(
                user_id=test_user.id,
                installation_id="device-a",
                file_name="a.opus",
                display_name="a.opus",
                device_name="A",
                source_type="device",
                audio_path="a.opus",
                created_at=now,
                updated_at=now,
            ),
            RecorderAudioRecord(
                user_id=test_user.id,
                installation_id="device-b",
                file_name="b.opus",
                display_name="b.opus",
                device_name="B",
                source_type="device",
                audio_path="b.opus",
                created_at=now,
                updated_at=now,
            ),
            RecorderAudioRecord(
                user_id=test_user.id,
                installation_id="",
                file_name="legacy.opus",
                display_name="legacy.opus",
                device_name="legacy",
                source_type="device",
                audio_path="legacy.opus",
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    db_session.commit()

    payload = h5_recorder.list_recordings(
        page=1,
        page_size=20,
        source_type="",
        installation_id="device-a",
        current_user=test_user,
        db=db_session,
    )
    known = h5_recorder.recorder_known_names(
        installation_id="device-a",
        current_user=test_user,
        db=db_session,
    )

    assert {row["file_name"] for row in payload["items"]} == {"a.opus", "legacy.opus"}
    assert set(known["items"]) == {"a.opus", "legacy.opus"}


def test_h5_device_dropdown_drives_all_device_scoped_requests():
    root = Path(__file__).resolve().parents[2]
    html = (root / "h5_static" / "index.html").read_text(encoding="utf-8")
    script = (root / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert 'id="profileHeaderDeviceSelect"' in html
    assert '[$("profileHeaderDeviceSelect"), $("profileDeviceSelect")]' in script
    assert 'params.set("installation_id", installationId);' in script
    assert '/api/h5-chat/mounted-accounts${query}' in script
    assert '/api/douyin/dashboard-status${query}' in script
    assert 'installation_id: (row && row.installation_id) || currentInstallationId()' in script
    assert 'installation_id: Optional[str] = Field(default=None, max_length=128)' in (root / "backend" / "app" / "api" / "h5_chat.py").read_text(encoding="utf-8")
    assert "refreshSelectedDeviceData(requestId)" in script
