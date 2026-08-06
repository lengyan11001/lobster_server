from datetime import datetime, timedelta

from backend.app.api import scheduled_tasks
from backend.app.models import ScheduledTask, UserHiflyVoiceAsset


def _payload():
    return {
        "capability_id": "hifly.video.create_by_tts",
        "payload": {"avatar": "avatar-1", "voice": "voice-1"},
        "h5_context": {"source": "h5", "ability_label": "创作数字人口播视频"},
    }


def _context():
    return {
        "requirements": {"industry": "装修"},
        "keyword_ids": [11, 12],
        "keywords": ["深圳装修"],
        "keyword_texts": ["深圳装修"],
        "memory_doc_ids": ["31"],
        "memory_docs": [{"id": 31, "title": "品牌资料", "content": "真实业务资料"}],
        "language": "zh-CN",
        "target_language": "zh-CN",
    }


def test_h5_legacy_digital_human_uses_ip_daily_script_context(monkeypatch):
    monkeypatch.setattr(scheduled_tasks, "_h5_dh_provider", lambda: scheduled_tasks._DIGITAL_HUMAN_PROVIDER_LEGACY)
    monkeypatch.setattr(scheduled_tasks, "_h5_dh_context_params", lambda db, user_id: _context())

    task_kind, payload = scheduled_tasks._maybe_convert_h5_digital_human_task(
        object(), task_kind="capability", payload=_payload(), target_user_id=7
    )

    assert task_kind == "capability"
    assert payload["payload"]["script_source"] == "ip_daily_industry_hot_oral"
    assert payload["payload"]["keyword_ids"] == [11, 12]
    assert payload["payload"]["keyword_texts"] == ["深圳装修"]


def test_h5_v2_digital_human_converts_after_ip_daily_context(monkeypatch):
    monkeypatch.setattr(scheduled_tasks, "_h5_dh_provider", lambda: scheduled_tasks._DIGITAL_HUMAN_PROVIDER_V2)
    monkeypatch.setattr(scheduled_tasks, "_h5_dh_context_params", lambda db, user_id: _context())
    monkeypatch.setattr(scheduled_tasks, "_h5_dh_latest_virtualman", lambda db, user_id: "virtualman-1")
    monkeypatch.setattr(
        scheduled_tasks,
        "_h5_dh_available_virtualmans",
        lambda db, user_id: [
            {"profile_id": 1, "virtualman_id": "virtualman-1", "title": "Avatar 1", "cover_url": ""},
            {"profile_id": 2, "virtualman_id": "virtualman-2", "title": "Avatar 2", "cover_url": ""},
        ],
    )
    monkeypatch.setattr(scheduled_tasks, "_h5_dh_latest_voice", lambda db, user_id: "voice-2")
    monkeypatch.setattr(
        scheduled_tasks,
        "_h5_dh_resolve_voice",
        lambda db, user_id, requested_voice="": requested_voice or "voice-2",
    )

    task_kind, payload = scheduled_tasks._maybe_convert_h5_digital_human_task(
        object(), task_kind="capability", payload=_payload(), target_user_id=7
    )

    assert task_kind == "client_workflow"
    assert payload["action"] == "shanjian_digital_human_video"
    assert payload["params"]["script_source"] == "ip_daily_industry_hot_oral"
    assert payload["params"]["keyword_ids"] == [11, 12]
    assert payload["params"]["virtualman_id"] == "virtualman-1"
    assert payload["params"]["virtualman_selection_mode"] == "daily_round_robin"
    assert [item["virtualman_id"] for item in payload["params"]["virtualman_candidates"]] == [
        "virtualman-1",
        "virtualman-2",
    ]


def test_h5_v2_digital_human_preserves_duration_and_template_choices(monkeypatch):
    monkeypatch.setattr(scheduled_tasks, "_h5_dh_provider", lambda: scheduled_tasks._DIGITAL_HUMAN_PROVIDER_V2)
    monkeypatch.setattr(scheduled_tasks, "_h5_dh_context_params", lambda db, user_id: _context())
    monkeypatch.setattr(scheduled_tasks, "_h5_dh_latest_virtualman", lambda db, user_id: "virtualman-1")
    monkeypatch.setattr(scheduled_tasks, "_h5_dh_available_virtualmans", lambda db, user_id: [])
    monkeypatch.setattr(scheduled_tasks, "_h5_dh_latest_voice", lambda db, user_id: "voice-2")
    monkeypatch.setattr(
        scheduled_tasks,
        "_h5_dh_resolve_voice",
        lambda db, user_id, requested_voice="": requested_voice or "voice-2",
    )
    request_payload = _payload()
    request_payload["payload"].update(
        {
            "long_video": True,
            "use_template": True,
            "virtualman_id": "virtualman-selected",
            "style_id": "style-selected",
            "template_scene": "realMan",
        }
    )

    task_kind, payload = scheduled_tasks._maybe_convert_h5_digital_human_task(
        object(), task_kind="capability", payload=request_payload, target_user_id=7
    )

    assert task_kind == "client_workflow"
    assert payload["params"]["long_video"] is True
    assert payload["params"]["use_template"] is True
    assert payload["params"]["virtualman_id"] == "virtualman-selected"
    assert payload["params"]["virtualman_selection_mode"] == "fixed"
    assert payload["params"]["style_id"] == "style-selected"


def test_scheduled_run_replaces_a_deleted_voice_before_dispatch(db_session, test_user):
    now = datetime.utcnow()
    db_session.add_all(
        [
            UserHiflyVoiceAsset(
                user_id=test_user.id,
                title="Deleted voice",
                status="deleted",
                hifly_task_id="voice-task-deleted",
                hifly_voice_id="voice-deleted",
                updated_at=now,
            ),
            UserHiflyVoiceAsset(
                user_id=test_user.id,
                title="Current voice",
                status="success",
                hifly_task_id="voice-task-current",
                hifly_voice_id="voice-current",
                updated_at=now - timedelta(minutes=1),
            ),
        ]
    )
    task = ScheduledTask(
        user_id=test_user.id,
        title="Digital human",
        task_kind="client_workflow",
        content="Digital human",
        payload={
            "action": "shanjian_digital_human_video",
            "params": {
                "voice": "voice-deleted",
                "speaker_id": "voice-deleted",
                "script_source": "ip_daily_industry_hot_oral",
            },
        },
        schedule_type="daily_times",
        status="active",
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    run = scheduled_tasks._create_run_for_target(db_session, task, "online-1", now)

    assert run.payload["params"]["voice"] == "voice-current"
    assert run.payload["params"]["speaker_id"] == "voice-current"


def test_scheduled_run_keeps_an_explicit_active_voice(db_session, test_user):
    db_session.add_all(
        [
            UserHiflyVoiceAsset(
                user_id=test_user.id,
                title="Selected voice",
                status="success",
                hifly_task_id="voice-task-selected",
                hifly_voice_id="voice-selected",
            ),
            UserHiflyVoiceAsset(
                user_id=test_user.id,
                title="Newer voice",
                status="success",
                hifly_task_id="voice-task-newer",
                hifly_voice_id="voice-newer",
                updated_at=datetime.utcnow() + timedelta(minutes=1),
            ),
        ]
    )
    db_session.commit()
    payload = {
        "action": "shanjian_digital_human_video",
        "params": {"voice": "voice-selected", "speaker_id": "voice-selected"},
    }

    result = scheduled_tasks._enrich_digital_human_voice_payload(
        db_session,
        payload=payload,
        target_user_id=test_user.id,
    )

    assert result["params"]["voice"] == "voice-selected"
    assert result["params"]["speaker_id"] == "voice-selected"


def test_scheduled_run_drops_a_deleted_voice_when_no_active_voice_exists(db_session, test_user):
    db_session.add(
        UserHiflyVoiceAsset(
            user_id=test_user.id,
            title="Deleted voice",
            status="deleted",
            hifly_task_id="voice-task-only-deleted",
            hifly_voice_id="voice-deleted",
        )
    )
    db_session.commit()
    payload = {
        "action": "shanjian_digital_human_video",
        "params": {"voice": "voice-deleted", "speaker_id": "voice-deleted"},
    }

    result = scheduled_tasks._enrich_digital_human_voice_payload(
        db_session,
        payload=payload,
        target_user_id=test_user.id,
    )

    assert "voice" not in result["params"]
    assert "speaker_id" not in result["params"]
