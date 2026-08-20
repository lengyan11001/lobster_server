from datetime import datetime

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


def test_non_takeover_workflow_nodes_keep_daily_schedule_on_activation():
    node = _node("video", "shanjian_digital_human_video")
    node["end_time"] = "23:59"

    assert _workflow_node_should_start_now(
        node,
        task_kind="client_workflow",
        now_utc=datetime(2026, 8, 16, 8, 21),
        timezone_offset_minutes=480,
    ) is False


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
