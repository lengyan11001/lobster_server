from pathlib import Path

from backend.app.api.h5_workflows import _sales_action_from_note, _sales_douyin_action_payload


ROOT = Path(__file__).resolve().parents[2]


def test_sales_douyin_payload_only_keeps_the_action():
    node = {"note": "抖音主动私信10个精准客户", "ability_label": "抖音主动私信精准客户"}
    legacy_payload = {
        "action": "search_collect",
        "params": {
            "sales_action": "direct_message",
            "keyword": "IP模板关键词",
            "regions": ["深圳"],
            "account_id": "douyin-1",
            "message": "服务端话术",
        },
    }

    assert _sales_douyin_action_payload(node, legacy_payload) == {"action": "direct_message"}


def test_sales_douyin_action_can_be_recovered_from_old_node_title():
    node = {"note": "抖音关注10个精准客户，并找到他的首条作品去评论"}

    assert _sales_douyin_action_payload(node, {"action": "search_collect", "params": {}}) == {
        "action": "follow_comment"
    }


def test_sales_douyin_stranger_message_keeps_online_runtime_params():
    node = {"ability_key": "douyin_leads", "note": "抖音私信接管"}

    assert _sales_douyin_action_payload(
        node,
        {
            "action": "stranger_message",
            "params": {"wechat_add_friend_enabled": False},
        },
    ) == {
        "action": "stranger_message",
        "params": {
            "wechat_add_friend_enabled": False,
            "wechat_add_friend_targets_source": "douyin_private_message_phone",
        },
    }


def test_sales_douyin_stranger_message_parses_string_false_switch():
    node = {"ability_key": "douyin_leads", "note": "抖音私信接管"}

    payload = _sales_douyin_action_payload(
        node,
        {
            "action": "stranger_message",
            "params": {"wechat_add_friend_enabled": "false"},
        },
    )

    assert payload["params"]["wechat_add_friend_enabled"] is False


def test_all_sales_douyin_preset_titles_map_to_the_expected_action():
    cases = {
        "抖音自动养号": "account_nurture",
        "抖音获客·关键词抓取精准客户": "search_collect",
        "抖音回复精准客户评论10个": "reply_comments",
        "抖音自己评论区接管，评论并@10个精准客户": "mention_comment",
        "抖音关注10个精准客户，并找到他的首条作品去评论": "follow_comment",
        "抖音主动私信10个精准客户": "direct_message",
        "抖音私信接管": "stranger_message",
    }

    assert {_sales_action_from_note(title) for title in cases} == set(cases.values())
    for title, expected in cases.items():
        legacy = {"action": "search_collect", "params": {"sales_action": "search_collect", "keyword": title}}
        assert _sales_douyin_action_payload({"note": title}, legacy) == {"action": expected}


def test_h5_sales_preset_dispatches_douyin_without_business_params():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert "payload: { action: salesAction }" in script
    assert "const preservedParams = {};" in script
    assert 'action === "stranger_message"' in script
    assert "preservedParams.wechat_add_friend_enabled = workflowBoolParam(rowParams.wechat_add_friend_enabled, false);" in script
    assert "preservedParams.wechat_add_friend_enabled = workflowBoolParam(planParams.wechat_add_friend_enabled, false);" in script
    assert 'payload: { action: "search_collect", params: { keyword: prompt, sales_action:' not in script


def test_h5_douyin_nodes_are_marked_as_one_shot():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    workflow_source = (ROOT / "backend" / "app" / "api" / "h5_workflows.py").read_text(encoding="utf-8")

    assert "payload.h5_one_shot = true" in script
    assert 'h5_task_source: "h5"' in script
    assert 'douyin_execution_mode: "one_shot"' in script
    assert 'payload["h5_one_shot"] = True' in workflow_source
    assert 'payload["douyin_execution_mode"] = "one_shot"' in workflow_source


def test_h5_renders_all_sales_douyin_results_and_private_message_content():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    for action in (
        "account_nurture",
        "search_collect",
        "reply_comments",
        "mention_comment",
        "follow_comment",
        "direct_message",
        "stranger_message",
    ):
        assert f"{action}: {{" in script

    assert 'item.incoming_message || item.preview_text ? `客户消息：' in script
    assert 'item.reply_message ? `回复内容：${item.reply_message}`' in script
    assert 'item.reply_error ? `回复失败原因：${item.reply_error}`' in script
    assert 'data.conversation_scope === "recent" ? "本轮无新增，以下展示最近会话"' in script
