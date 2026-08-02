from backend.app.api.scheduled_tasks import _normalize_sales_digital_human_run_payload


def test_sales_digital_human_run_drops_node_prompt_for_ip_daily_script():
    normalized = _normalize_sales_digital_human_run_payload(
        "client_workflow",
        {
            "action": "shanjian_digital_human_video",
            "params": {
                "script_source": "ip_daily_industry_hot_oral",
                "prompt": "创作一条数字人口播视频（用于发朋友圈）",
                "voice": "voice-1",
            },
        },
    )

    assert "prompt" not in normalized["params"]
    assert normalized["params"]["voice"] == "voice-1"


def test_sales_digital_human_run_keeps_explicit_script():
    normalized = _normalize_sales_digital_human_run_payload(
        "client_workflow",
        {
            "action": "shanjian_digital_human_video",
            "params": {
                "script_source": "ip_daily_industry_hot_oral",
                "script": "用户明确提供的完整口播文案",
                "prompt": "节点说明",
            },
        },
    )

    assert normalized["params"]["script"] == "用户明确提供的完整口播文案"
    assert "prompt" not in normalized["params"]


def test_non_sales_digital_human_run_is_unchanged():
    payload = {
        "action": "shanjian_digital_human_video",
        "params": {"prompt": "普通数字人直接口播文案"},
    }

    assert _normalize_sales_digital_human_run_payload("client_workflow", payload) == payload
