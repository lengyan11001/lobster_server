from datetime import datetime

from backend.app.api import scheduled_tasks
from backend.app.models import Asset, IPContentScheduleTemplate, ScheduledTask


def _add_persona_and_photo(db_session, user_id: int) -> None:
    db_session.add(
        IPContentScheduleTemplate(
            user_id=user_id,
            name=scheduled_tasks._PERSONAL_DEFAULT_TEMPLATE_NAME,
            status="active",
            requirements={
                "gender": "female",
                "role": "driving instructor",
                "product": "VIP driving lessons",
                "current_province": "Guangdong",
                "current_city": "Shaoguan",
                "hometown": "Guizhou",
                "birth_era": "1998",
                "target_customer": "new drivers",
                "video_style": "direct and practical",
                "profile_photo_asset_id": "server-photo-1",
            },
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


def test_local_bestseller_drops_internal_photo_source_url(db_session, test_user):
    db_session.add(
        IPContentScheduleTemplate(
            user_id=test_user.id,
            name=scheduled_tasks._PERSONAL_DEFAULT_TEMPLATE_NAME,
            status="active",
            requirements={"profile_photo_asset_id": "internal-photo-1"},
        )
    )
    db_session.add(
        Asset(
            asset_id="internal-photo-1",
            user_id=test_user.id,
            filename="assets/internal-photo-1.png",
            media_type="image",
            source_url="http://127.0.0.1:8000/api/assets/file/internal-photo-1?token=abc",
        )
    )
    db_session.commit()

    payload = scheduled_tasks._enrich_local_bestseller_workflow_payload(
        db_session,
        payload={"action": "local_bestseller_daily_video", "params": {}},
        target_user_id=test_user.id,
        now=datetime(2026, 7, 28, 6, 0),
    )

    profile = payload["params"]["profile"]
    assert "photo_url" not in profile
    assert profile["photo_asset_id"] == "internal-photo-1"


def test_local_bestseller_keeps_asset_id_when_override_url_is_internal(db_session, test_user):
    payload = scheduled_tasks._enrich_local_bestseller_workflow_payload(
        db_session,
        payload={
            "action": "local_bestseller_daily_video",
            "params": {
                "profile_override": True,
                "profile": {
                    "photo_url": "http://127.0.0.1:8000/api/assets/file/local-photo?token=abc",
                    "photo_asset_id": "local-photo-1",
                },
            },
        },
        target_user_id=test_user.id,
        now=datetime(2026, 7, 28, 6, 0),
    )

    profile = payload["params"]["profile"]
    assert "photo_url" not in profile
    assert profile["photo_asset_id"] == "local-photo-1"


def test_employee_workflow_keeps_its_original_day_start_across_daily_runs(db_session, test_user):
    first_payload = scheduled_tasks._enrich_local_bestseller_workflow_payload(
        db_session,
        payload={
            "action": "local_bestseller_daily_video",
            "params": {"start_day": 7, "day_mode": "workflow_elapsed"},
        },
        target_user_id=test_user.id,
        now=datetime(2026, 8, 20, 0, 0),
    )
    second_payload = scheduled_tasks._enrich_local_bestseller_workflow_payload(
        db_session,
        payload=first_payload,
        target_user_id=test_user.id,
        now=datetime(2026, 8, 21, 0, 0),
    )

    assert second_payload["params"]["start_day"] == 7
    assert second_payload["params"]["day_mode"] == "workflow_elapsed"
    assert second_payload["h5_context"]["workflow_started_at"] == first_payload["h5_context"]["workflow_started_at"]
    assert second_payload["h5_context"]["workflow_day_start"] == first_payload["h5_context"]["workflow_day_start"]


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


def test_claim_time_refresh_keeps_live_profile_for_older_online_clients(db_session, test_user):
    _add_persona_and_photo(db_session, test_user.id)

    payload = scheduled_tasks._refresh_live_personal_template_payload(
        db_session,
        task_kind="client_workflow",
        payload={
            "action": "local_bestseller_daily_video",
            "params": {},
            "h5_context": {"workflow_template_id": 159},
        },
        target_user_id=test_user.id,
        now=datetime(2026, 7, 29, 6, 0),
    )

    profile = payload["params"]["profile"]
    assert profile["photo_url"] == "https://assets.example.test/assets/server-photo-1.png"
    assert payload["params"]["profile_photo_source_asset_id"] == "server-photo-1"
    assert payload["template_validation"]["ok"] is True


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
