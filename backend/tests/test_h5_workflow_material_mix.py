from types import SimpleNamespace

from backend.app.api import h5_workflows, scheduled_tasks


def _node(action: str) -> dict:
    return {
        "id": "mix-node",
        "ability_key": action,
        "time": "09:00",
        "plan": {
            "task_kind": "client_workflow",
            "title": "素材混剪",
            "payload": {"action": action, "params": {}},
        },
    }


def _context() -> dict:
    return {
        "requirements": {"industry": "火锅"},
        "keyword_ids": [11],
        "keywords": ["火锅"],
        "keyword_texts": ["火锅"],
        "memory_doc_ids": ["9"],
        "memory_docs": [{"id": 9, "title": "品牌", "content": "门店资料"}],
        "language": "zh-CN",
        "target_language": "zh-CN",
        "digital_human_template": {
            "style_id": "style-7",
            "scene": "realMan",
            "materials": [{"type": "video", "fileUrl": "https://cdn.example.com/a.mp4"}],
        },
        "digital_human_resources": {"avatars": [], "voices": [{"voice": "voice-9"}]},
        "voice": "voice-9",
        "voice_candidates": [{"voice": "voice-9"}],
        "virtualman_candidates": [],
        "virtualman_id": "",
    }


def test_material_mix_node_consumes_ip_persona_but_not_avatar():
    node = _node("shanjian_material_mix")
    assert h5_workflows._node_consumes_ip_persona(node) is True

    needs = h5_workflows._custom_workflow_material_needs([node])
    assert needs["survey"] is True
    assert needs["keywords"] is True
    assert needs["memory"] is True
    assert needs["digital_human"] is False


def test_material_mix_runtime_payload_keeps_duration_and_drops_avatar(monkeypatch):
    monkeypatch.setattr(
        scheduled_tasks,
        "_h5_dh_personal_default_template",
        lambda db, user_id, installation_id="": SimpleNamespace(id=1, user_id=7, meta={}),
    )
    monkeypatch.setattr(
        scheduled_tasks,
        "_h5_dh_context_params",
        lambda db, user_id, installation_id="": _context(),
    )
    payload = {
        "action": "shanjian_material_mix",
        "h5_context": {"workflow_template_id": 5, "workflow_node_id": "mix-node"},
        "params": {
            "video_duration": 45,
            "virtualman_id": "should-be-removed",
            "virtualman_candidates": [{"virtualman_id": "should-be-removed"}],
        },
    }

    refreshed = scheduled_tasks._refresh_live_personal_template_payload(
        None,
        task_kind="client_workflow",
        payload=payload,
        target_user_id=7,
    )

    params = refreshed["params"]
    assert params["video_duration"] == 45
    assert params["style_id"] == "style-7"
    assert params["template_scene"] == "oralMixCutting"
    assert params["voice"] == "voice-9"
    assert params["requirements"]["industry"] == "火锅"
    assert "virtualman_id" not in params
    assert "virtualman_candidates" not in params
    assert "virtualman_selection_mode" not in params
    assert refreshed["template_validation"]["ok"] is True


def test_material_mix_runtime_validation_reports_missing_template(monkeypatch):
    context = _context()
    context["digital_human_template"] = {}
    context["digital_human_resources"] = {"avatars": [], "voices": []}
    context["voice"] = ""
    context["voice_candidates"] = []
    monkeypatch.setattr(
        scheduled_tasks,
        "_h5_dh_context_params",
        lambda db, user_id, installation_id="": context,
    )
    payload = {
        "action": "shanjian_material_mix",
        "h5_context": {"workflow_template_id": 5, "workflow_node_id": "mix-node"},
        "params": {},
    }
    validation = scheduled_tasks._live_personal_template_validation(
        None,
        task_kind="client_workflow",
        payload=payload,
        target_user_id=7,
    )
    assert validation["ok"] is False
    assert "editing template" in validation["missing"]
    assert "voice asset" in validation["missing"]


def test_material_mix_video_becomes_final_media():
    media = scheduled_tasks._final_media_from_run_payload(
        {
            "action": "shanjian_material_mix",
            "local_result": {
                "video_url": "https://cdn.example.com/mix.mp4",
                "video_asset_id": "asset-1",
                "title": "素材混剪",
            },
        }
    )
    assert media["video_url"] == "https://cdn.example.com/mix.mp4"
    assert media["asset_id"] == "asset-1"
