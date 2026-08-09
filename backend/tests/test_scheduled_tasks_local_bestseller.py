from datetime import datetime

from backend.app.api import scheduled_tasks
from backend.app.models import Asset, IPContentScheduleTemplate, ScheduledTask


def _add_persona_and_photo(db_session, user_id: int) -> None:
    db_session.add(
        IPContentScheduleTemplate(
            user_id=user_id,
            name=scheduled_tasks._PERSONAL_DEFAULT_TEMPLATE_NAME,
            status="active",
            requirements={"profile_photo_asset_id": "server-photo-1"},
        )
    )
    db_session.add(
        Asset(
            asset_id="server-photo-1",
            user_id=user_id,
            filename="assets/server-photo-1.png",
            media_type="image",
            source_url="https://assets.example.test/assets/server-photo-1.png",
        )
    )
    db_session.commit()


def test_local_bestseller_resolves_server_photo_for_older_online_clients(db_session, test_user):
    _add_persona_and_photo(db_session, test_user.id)

    payload = scheduled_tasks._enrich_local_bestseller_workflow_payload(
        db_session,
        payload={"action": "local_bestseller_daily_video", "params": {}},
        target_user_id=test_user.id,
        now=datetime(2026, 7, 28, 6, 0),
    )

    assert payload["params"]["profile"]["photo_url"] == "https://assets.example.test/assets/server-photo-1.png"
    assert "photo_asset_id" not in payload["params"]["profile"]
    assert payload["params"]["profile_photo_source_asset_id"] == "server-photo-1"


def test_existing_scheduled_task_refreshes_server_photo_when_run_is_created(db_session, test_user):
    _add_persona_and_photo(db_session, test_user.id)
    task = ScheduledTask(
        user_id=test_user.id,
        title="Local bestseller video",
        task_kind="client_workflow",
        content="",
        payload={
            "action": "local_bestseller_daily_video",
            "params": {"profile": {"photo_asset_id": "server-photo-1"}},
        },
        schedule_type="daily_times",
        status="active",
        next_run_at=datetime(2026, 7, 29, 6, 0),
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    run = scheduled_tasks._create_run_for_target(
        db_session,
        task,
        installation_id="desktop-1",
        now=datetime(2026, 7, 29, 6, 0),
    )

    assert run.payload["params"]["profile"]["photo_url"] == "https://assets.example.test/assets/server-photo-1.png"
    assert "photo_asset_id" not in run.payload["params"]["profile"]


def test_one_off_local_bestseller_can_override_selected_persona_fields(db_session, test_user):
    _add_persona_and_photo(db_session, test_user.id)

    payload = scheduled_tasks._enrich_local_bestseller_workflow_payload(
        db_session,
        payload={
            "action": "local_bestseller_daily_video",
            "params": {
                "profile_override": True,
                "profile": {
                    "name": "本次出镜人",
                    "photo_url": "https://manual.example.test/person.png",
                },
            },
        },
        target_user_id=test_user.id,
        now=datetime(2026, 7, 28, 6, 0),
    )

    assert payload["params"]["profile"]["name"] == "本次出镜人"
    assert payload["params"]["profile"]["photo_url"] == "https://manual.example.test/person.png"
    assert payload["h5_context"]["persona_source"] == "h5_profile_override"


def test_custom_local_bestseller_does_not_inherit_persona_fields(db_session, test_user):
    _add_persona_and_photo(db_session, test_user.id)

    payload = scheduled_tasks._enrich_local_bestseller_workflow_payload(
        db_session,
        payload={
            "action": "local_bestseller_daily_video",
            "params": {
                "profile_source": "custom",
                "profile": {
                    "name": "本次出镜人",
                    "style": "真实同城生活感",
                },
            },
        },
        target_user_id=test_user.id,
        now=datetime(2026, 7, 28, 6, 0),
    )

    assert payload["params"]["profile"] == {
        "name": "本次出镜人",
        "style": "真实同城生活感",
    }
    assert "profile_photo_source_asset_id" not in payload["params"]
    assert payload["h5_context"]["persona_source"] == "h5_custom_profile"


def test_employee_local_bestseller_still_prefers_current_persona(db_session, test_user):
    _add_persona_and_photo(db_session, test_user.id)

    payload = scheduled_tasks._enrich_local_bestseller_workflow_payload(
        db_session,
        payload={
            "action": "local_bestseller_daily_video",
            "params": {
                "profile": {"photo_url": "https://stale.example.test/person.png"},
            },
        },
        target_user_id=test_user.id,
        now=datetime(2026, 7, 28, 6, 0),
    )

    assert payload["params"]["profile"]["photo_url"] == "https://assets.example.test/assets/server-photo-1.png"
    assert payload["h5_context"]["persona_source"] == "ip_persona_default"
