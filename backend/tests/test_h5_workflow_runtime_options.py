from backend.app.api.h5_workflows import _apply_workflow_runtime_options


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
