from backend.app.api.h5_workflows import (
    _apply_sales_digital_human_defaults,
    _apply_workflow_runtime_options,
    _sales_digital_human_template_id,
)
from backend.app.models import IPContentScheduleTemplate


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

    result = _apply_workflow_runtime_options(nodes, local_bestseller_plan_day=7)

    local_params = result[0]["plan"]["payload"]["params"]
    digital_params = result[1]["plan"]["payload"]["params"]
    assert local_params["day"] == 7
    assert local_params["day_mode"] == "activation_selected"
    assert "day" not in digital_params


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
