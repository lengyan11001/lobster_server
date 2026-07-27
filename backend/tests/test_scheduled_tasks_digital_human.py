from backend.app.api import scheduled_tasks


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
    monkeypatch.setattr(scheduled_tasks, "_h5_dh_latest_voice", lambda db, user_id: "voice-2")

    task_kind, payload = scheduled_tasks._maybe_convert_h5_digital_human_task(
        object(), task_kind="capability", payload=_payload(), target_user_id=7
    )

    assert task_kind == "client_workflow"
    assert payload["action"] == "shanjian_digital_human_video"
    assert payload["params"]["script_source"] == "ip_daily_industry_hot_oral"
    assert payload["params"]["keyword_ids"] == [11, 12]
    assert payload["params"]["virtualman_id"] == "virtualman-1"
