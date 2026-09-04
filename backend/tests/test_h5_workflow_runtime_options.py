from datetime import datetime
from types import SimpleNamespace

from backend.app.api import h5_workflows, scheduled_tasks
from backend.app.api.h5_workflows import (
    _apply_sales_digital_human_defaults,
    _apply_workflow_runtime_options,
    _prepare_publish_action_nodes,
    _sales_digital_human_template_id,
    _workflow_node_should_start_now,
)
from backend.app.models import H5ChatDevicePresence, H5MountedAccountDefault, IPContentScheduleTemplate


def _node(node_id: str, action: str) -> dict:
    return {
        "id": node_id,
        "time": "06:00",
        "plan": {
            "task_kind": "client_workflow",
            "payload": {"action": action, "params": {}},
        },
    }


def test_sales_activation_day_is_only_applied_to_local_bestseller():
    nodes = [
        _node("local", "local_bestseller_daily_video"),
        _node("digital", "shanjian_digital_human_video"),
    ]
    nodes[0]["plan"]["payload"]["params"]["day"] = 3

    result = _apply_workflow_runtime_options(nodes, local_bestseller_plan_day=7)

    local_params = result[0]["plan"]["payload"]["params"]
    digital_params = result[1]["plan"]["payload"]["params"]
    assert local_params["start_day"] == 7
    assert local_params["day_mode"] == "workflow_elapsed"
    assert "day" not in local_params
    assert "day" not in digital_params


def test_native_wechat_takeover_starts_when_activation_is_inside_window():
    node = _node("wechat", "native_wechat_poll")
    node["end_time"] = "23:59"

    assert _workflow_node_should_start_now(
        node,
        task_kind="client_workflow",
        now_utc=datetime(2026, 8, 16, 8, 21),
        timezone_offset_minutes=480,
    ) is True
    assert _workflow_node_should_start_now(
        node,
        task_kind="client_workflow",
        now_utc=datetime(2026, 8, 16, 16, 30),
        timezone_offset_minutes=480,
    ) is False


def test_all_client_workflow_nodes_start_inside_today_window():
    node = _node("video", "shanjian_digital_human_video")
    node["end_time"] = "23:59"

    assert _workflow_node_should_start_now(
        node,
        task_kind="client_workflow",
        now_utc=datetime(2026, 8, 16, 8, 21),
        timezone_offset_minutes=480,
    ) is True


def test_workflow_node_only_starts_now_inside_window():
    node = _node("video", "shanjian_digital_human_video")
    node["time"] = "10:00"
    node["end_time"] = "11:00"

    assert _workflow_node_should_start_now(
        node,
        task_kind="client_workflow",
        now_utc=datetime(2026, 8, 16, 1, 30),  # 09:30 local
        timezone_offset_minutes=480,
    ) is False
    assert _workflow_node_should_start_now(
        node,
        task_kind="client_workflow",
        now_utc=datetime(2026, 8, 16, 3, 30),  # 11:30 local
        timezone_offset_minutes=480,
    ) is False


def test_workflow_node_starts_now_inside_overnight_window():
    node = _node("wechat", "native_wechat_poll")
    node["time"] = "23:00"
    node["end_time"] = "01:00"

    assert _workflow_node_should_start_now(
        node,
        task_kind="client_workflow",
        now_utc=datetime(2026, 8, 16, 16, 30),  # 00:30 local next day
        timezone_offset_minutes=480,
    ) is True


def test_digital_human_nodes_receive_distinct_sequence_slots():
    nodes = [
        _node("digital-1", "shanjian_digital_human_video"),
        _node("digital-2", "shanjian_digital_human_video"),
        _node("digital-3", "shanjian_digital_human_video"),
    ]

    result = _apply_workflow_runtime_options(nodes)
    params = [node["plan"]["payload"]["params"] for node in result]

    assert [item["virtualman_rotation_slot"] for item in params] == [0, 1, 2]
    assert {item["virtualman_selection_mode"] for item in params} == {"daily_sequence"}


def test_sales_digital_human_uses_active_personal_template_and_short_video():
    params = _apply_sales_digital_human_defaults(
        {
            "use_template": False,
            "style_id": "one-off-template",
            "template_scene": "realMan",
            "long_video": True,
            "voice": "voice-1",
        }
    )

    assert "use_template" not in params
    assert "style_id" not in params
    assert "template_scene" not in params
    assert params["long_video"] is False
    assert params["template_mode"] == "active_personal_template"
    assert params["voice"] == "voice-1"


def test_sales_digital_human_template_comes_from_current_ip_template():
    personal = IPContentScheduleTemplate(
        user_id=1,
        name="个人默认配置",
        meta={"digital_human_template": {"style_id": "style-personal"}},
    )
    current = IPContentScheduleTemplate(
        user_id=1,
        name="当前销售模板",
        meta={"digital_human_template": {"style_id": "style-current"}},
    )

    assert _sales_digital_human_template_id(personal, current) == "style-current"


def test_sales_digital_human_template_accepts_template_id_alias():
    current = IPContentScheduleTemplate(
        user_id=1,
        name="current-ip-template",
        meta={"digital_human_template": {"templateId": "template-current"}},
    )

    assert _sales_digital_human_template_id(None, current) == "template-current"


def test_current_ip_template_can_explicitly_report_missing_sales_template():
    personal = IPContentScheduleTemplate(
        user_id=1,
        name="个人默认配置",
        meta={"digital_human_template": {"style_id": "style-stale"}},
    )
    current = IPContentScheduleTemplate(
        user_id=1,
        name="当前销售模板",
        meta={"digital_human_template": None},
    )

    assert _sales_digital_human_template_id(personal, current) == ""


def test_sales_activation_replaces_stale_template_resources(monkeypatch, db_session, test_user):
    personal = SimpleNamespace(user_id=test_user.id, meta={})
    current = SimpleNamespace(user_id=test_user.id, meta={})
    monkeypatch.setattr(h5_workflows, "_personal_default_template", lambda db, user_id: personal)
    monkeypatch.setattr(h5_workflows, "_current_personal_schedule_template", lambda db, user_id, row: current)
    monkeypatch.setattr(h5_workflows, "_sales_digital_human_provider", lambda extra, template: "shanjian_v2")
    monkeypatch.setattr(h5_workflows, "_sales_digital_human_template_id", lambda personal, current: "style-current")
    monkeypatch.setattr(
        h5_workflows,
        "_h5_dh_context_params",
        lambda db, user_id: {
            "requirements": {"industry": "new"},
            "keyword_ids": [11],
            "keyword_texts": ["new keyword"],
            "competitors": ["new competitor"],
            "memory_doc_ids": ["31"],
            "memory_docs": [{"id": 31, "title": "new memory"}],
            "language": "en-US",
            "digital_human_resources": {
                "avatars": [{"provider": "shanjian_v2", "virtualman_id": "new-avatar"}],
                "voices": [{"voice": "new-voice"}],
            },
        },
    )
    monkeypatch.setattr(
        h5_workflows,
        "_personal_default_resource_overrides",
        lambda personal, current: {"keyword_ids": False, "competitor_ids": False, "memory_doc_ids": False},
    )
    monkeypatch.setattr(h5_workflows, "_active_keywords_for_ids", lambda db, user_id, ids: [SimpleNamespace()])
    monkeypatch.setattr(h5_workflows, "_active_competitors_for_ids", lambda db, user_id, ids: [SimpleNamespace(last_fetch_at=datetime.utcnow())])
    monkeypatch.setattr(h5_workflows, "_missing_sales_persona_fields", lambda requirements: [])
    monkeypatch.setattr(h5_workflows, "_device_is_online", lambda db, user_id, installation_id: True)

    nodes = [
        {
            "id": "sales-digital",
            "department_id": "sales",
            "plan": {
                "task_kind": "client_workflow",
                "payload": {
                    "action": "shanjian_digital_human_video",
                    "params": {
                        "requirements": {"industry": "old"},
                        "virtualman_id": "old-avatar",
                        "virtualman_candidates": [{"virtualman_id": "old-avatar"}],
                        "voice": "old-voice",
                        "voice_candidates": [{"voice": "old-voice"}],
                    },
                },
            },
        }
    ]

    prepared = h5_workflows._prepare_sales_workflow_nodes(
        db=db_session,
        owner=test_user,
        installation_id="online-1",
        template_name="销售员工",
        nodes=nodes,
        snapshot_extra=None,
    )

    params = prepared[0]["plan"]["payload"]["params"]
    assert params["requirements"] == {"industry": "new"}
    assert params["virtualman_id"] == "new-avatar"
    assert params["voice"] == "new-voice"
    assert params["keyword_ids"] == [11]
    assert params["language"] == "en-US"


def test_live_template_clear_removes_old_digital_human_resources(db_session, test_user):
    personal = IPContentScheduleTemplate(
        user_id=test_user.id,
        name=scheduled_tasks._PERSONAL_DEFAULT_TEMPLATE_NAME,
        requirements={"industry": "old"},
        meta={"current_template_id": 0},
    )
    db_session.add(personal)
    db_session.flush()
    current = IPContentScheduleTemplate(
        user_id=test_user.id,
        name="current-ip-template",
        requirements={"industry": "new"},
        meta={
            "digital_human_template": None,
            "digital_human_template_configured": True,
            "digital_human_resources": {"avatars": [], "voices": []},
            "digital_human_resources_configured": True,
        },
    )
    db_session.add(current)
    db_session.flush()
    personal.meta = {"current_template_id": current.id}
    db_session.commit()

    result = scheduled_tasks._refresh_live_personal_template_payload(
        db_session,
        task_kind="client_workflow",
        target_user_id=test_user.id,
        payload={
            "action": "shanjian_digital_human_video",
            "params": {
                "requirements": {"industry": "old"},
                "virtualman_id": "old-avatar",
                "virtualman_candidates": [{"virtualman_id": "old-avatar"}],
                "voice": "old-voice",
                "speaker_id": "old-voice",
                "voice_candidates": [{"voice": "old-voice"}],
            },
            "h5_context": {"workflow_template_id": "workflow-1", "workflow_node_id": "node-1"},
        },
    )

    params = result["params"]
    assert params["requirements"] == {"industry": "new"}
    assert params["virtualman_candidates"] == []
    assert params["voice_candidates"] == []
    assert "virtualman_id" not in params
    assert "voice" not in params
    assert "speaker_id" not in params


def test_publish_default_uses_payload_installation_id_when_column_is_empty(db_session, test_user):
    installation_id = "2fc3f43f7a684411a442cb661898aa74"
    now = datetime.utcnow()
    db_session.add(
        H5ChatDevicePresence(
            user_id=test_user.id,
            installation_id=installation_id,
            display_name="local-online",
            last_seen_at=now,
            created_at=now,
        )
    )
    db_session.add(
        H5MountedAccountDefault(
            user_id=test_user.id,
            scope="publish:douyin",
            account_key=f"{installation_id}:douyin:-1001",
            platform="douyin",
            account_id="-1001",
            account_label="抖音账号1",
            installation_id=None,
            source="publish_device",
            payload={
                "account_key": f"{installation_id}:douyin:-1001",
                "platform": "douyin",
                "account_id": "-1001",
                "nickname": "抖音账号1",
                "installation_id": installation_id,
            },
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()
    nodes = [
        {
            "id": "content",
            "ability_label": "同城爆款视频",
            "children": [
                {
                    "id": "publish-douyin",
                    "platform": "douyin",
                    "plan": {"payload": {"action": "publish_content", "params": {}}},
                }
            ],
        }
    ]

    prepared = _prepare_publish_action_nodes(
        db=db_session,
        owner=test_user,
        installation_id=installation_id,
        nodes=nodes,
    )

    params = prepared[0]["children"][0]["plan"]["payload"]["params"]
    assert params["installation_id"] == installation_id
    assert params["publish_installation_id"] == installation_id
