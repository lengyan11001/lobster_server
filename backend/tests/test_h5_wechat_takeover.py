from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from backend.app.api import h5_chat
from backend.app.api.h5_chat import H5WechatAutoReplyIn
import backend.app.api.h5_workflows as h5_workflows
from backend.app.api.h5_workflows import (
    _clean_action_nodes,
    _clean_nodes,
    _ensure_sales_douyin_add_friend_children,
    _native_wechat_plan,
    _sales_douyin_action_payload,
)
from backend.app.api.scheduled_tasks import _enrich_native_wechat_workflow_payload
from backend.app.models import H5ChatMessage, H5MountedAccountDefault


ROOT = Path(__file__).resolve().parents[2]


def test_native_wechat_takeover_plan_scans_friends_once_and_messages_every_15_seconds(monkeypatch):
    monkeypatch.setattr(h5_workflows, "_workflow_minutes_between", lambda *args, **kwargs: 60)
    plan = _native_wechat_plan("native_wechat_poll", "微信私信接管")
    params = plan["payload"]["params"]

    assert params["takeover_session_minutes"] == 60
    assert params["message_poll_interval_seconds"] == 15
    assert params["accept_friend_requests_once"] is True


def test_wechat_auto_reply_config_persists_memory_and_group_conditions(db_session, test_user, monkeypatch):
    monkeypatch.setattr(h5_chat, "online_user_for_mobile_user", lambda _db, user: user)

    def mounted_payload(_db, _user_id, **_kwargs):
        return {
            "ok": True,
            "accounts": [
                {
                    "scope": "wechat",
                    "online": True,
                    "installation_id": "device-1",
                    "account_key": "wechat:pc-default",
                    "account_id": "pc-wechat-default",
                    "nickname": "本机微信",
                }
            ],
            "defaults": {},
        }

    monkeypatch.setattr(h5_chat, "_mounted_accounts_payload", mounted_payload)

    h5_chat.h5_set_wechat_auto_reply(
        H5WechatAutoReplyIn(
            enabled=True,
            installation_id="device-1",
            memory_doc_ids=["faq-doc"],
            group_invite_enabled=True,
            group_invite_memory_doc_id="group-rule-doc",
            group_invite_keywords="咨询报价，预约体验",
            group_invite_contacts=["销售经理"],
            group_invite_primary_contact="销售经理",
            group_invite_primary_contact_name="王经理",
            group_invite_welcome_message="您好，我把负责接待的王经理拉进群了。",
        ),
        current_user=test_user,
        db=db_session,
    )
    h5_chat.h5_set_wechat_auto_reply(
        H5WechatAutoReplyIn(enabled=False, installation_id="device-1"),
        current_user=test_user,
        db=db_session,
    )

    pref = db_session.query(H5MountedAccountDefault).filter_by(user_id=test_user.id, scope="wechat_auto_reply").one()
    assert pref.payload["memory_doc_ids"] == ["faq-doc"]
    assert pref.payload["group_invite_enabled"] is True
    assert pref.payload["group_invite_memory_doc_id"] == "group-rule-doc"
    assert pref.payload["group_invite_keywords"] == "咨询报价，预约体验"
    assert pref.payload["group_invite_contacts"] == ["销售经理"]
    assert pref.payload["group_invite_primary_contact"] == "销售经理"
    assert pref.payload["group_invite_primary_contact_name"] == "王经理"
    assert pref.payload["group_invite_welcome_message"].startswith("您好")

    message = db_session.query(H5ChatMessage).order_by(H5ChatMessage.created_at.desc()).first()
    command = json.loads(message.content.removeprefix(h5_chat._H5_CLIENT_COMMAND_PREFIX))
    assert command["memory_doc_ids"] == ["faq-doc"]
    assert command["group_invite_enabled"] is True
    assert command["group_invite_memory_doc_id"] == "group-rule-doc"
    assert command["group_invite_contacts"] == ["销售经理"]
    assert command["group_invite_primary_contact"] == "销售经理"
    assert command["group_invite_welcome_message"].startswith("您好")


def test_wechat_auto_reply_uses_fixed_fifteen_second_interval(db_session, test_user, monkeypatch):
    monkeypatch.setattr(h5_chat, "online_user_for_mobile_user", lambda _db, user: user)

    def mounted_payload(_db, _user_id, **_kwargs):
        return {
            "ok": True,
            "accounts": [
                {
                    "scope": "wechat",
                    "online": True,
                    "installation_id": "device-1",
                    "account_key": "wechat:pc-default",
                    "account_id": "pc-wechat-default",
                    "nickname": "本机微信",
                }
            ],
            "defaults": {},
        }

    monkeypatch.setattr(h5_chat, "_mounted_accounts_payload", mounted_payload)

    h5_chat.h5_set_wechat_auto_reply(
        H5WechatAutoReplyIn(
            enabled=True,
            installation_id="device-1",
            interval_seconds=60,
        ),
        current_user=test_user,
        db=db_session,
    )

    pref = db_session.query(H5MountedAccountDefault).filter_by(user_id=test_user.id, scope="wechat_auto_reply").one()
    assert pref.payload["interval_seconds"] == 15

    message = db_session.query(H5ChatMessage).order_by(H5ChatMessage.created_at.desc()).first()
    command = json.loads(message.content.removeprefix(h5_chat._H5_CLIENT_COMMAND_PREFIX))
    assert command["interval_seconds"] == 15


def test_native_wechat_task_uses_saved_group_invite_settings(db_session, test_user):
    now = datetime.utcnow()
    db_session.add(
        H5MountedAccountDefault(
            user_id=test_user.id,
            scope="wechat_auto_reply",
            account_key="wechat:pc-default",
            platform="wechat",
            account_id="pc-wechat-default",
            installation_id="device-1",
            source="pc_wechat",
            payload={
                "memory_doc_ids": ["faq-doc"],
                "group_invite_enabled": True,
                "group_invite_memory_doc_id": "group-rules",
                "group_invite_keywords": "报价,合作",
                "group_invite_contacts": ["wxid_manager"],
                "group_invite_primary_contact": "wxid_manager",
                "group_invite_primary_contact_name": "九变",
                "group_invite_welcome_message": "欢迎进群",
            },
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    payload = _enrich_native_wechat_workflow_payload(
        db_session,
        payload={
            "action": "native_wechat_poll",
            "params": {
                "group_invite_enabled": True,
            },
        },
        target_user_id=test_user.id,
    )

    params = payload["params"]
    assert params["memory_doc_ids"] == ["faq-doc"]
    assert params["group_invite_memory_doc_id"] == "group-rules"
    assert params["group_invite_keywords"] == "报价,合作"
    assert params["group_invite_contacts"] == ["wxid_manager"]
    assert params["group_invite_primary_contact"] == "wxid_manager"
    assert params["group_invite_primary_contact_name"] == "九变"
    assert params["group_invite_welcome_message"] == "欢迎进群"
    assert params["group_invite_rule_status"] == "configured"

def test_add_friend_child_defaults_to_douyin_private_message_phone():
    parent = {"id": "douyin-private", "ability_label": "抖音私信接管", "department_id": "sales"}
    actions = _clean_action_nodes(
        [
            {
                "id": "add-friend",
                "time": "15:00",
                "action_type": "native_wechat_add_friend",
                "ability_key": "native_wechat_add_friend",
                "plan": {"payload": {"action": "native_wechat_add_friend", "params": {}}},
            }
        ],
        parent,
    )

    params = actions[0]["plan"]["payload"]["params"]
    assert actions[0]["parent_node_id"] == "douyin-private"
    assert params["source_workflow_node_id"] == "douyin-private"
    assert params["source_mode"] == "douyin_private_message_phone"
    assert params["trigger"] == "clear_mobile"


def test_workflow_child_end_time_is_preserved_and_defaults_to_next_start_or_parent_end():
    actions = _clean_action_nodes(
        [
            {
                "id": "publish-1",
                "time": "15:00",
                "action_type": "publish",
                "platform": "douyin",
                "plan": {"payload": {"params": {}}},
            },
            {
                "id": "publish-2",
                "time": "15:30",
                "end_time": "15:45",
                "action_type": "publish",
                "platform": "wechat_channels",
                "plan": {"payload": {"params": {}}},
            },
        ],
        {"id": "parent", "end_time": "16:00", "ability_label": "parent"},
    )

    assert actions[0]["end_time"] == "15:30"
    assert actions[0]["time_range"] == "15:00-15:30"
    assert actions[1]["end_time"] == "15:45"


def test_h5_exposes_wechat_memory_and_group_condition_settings():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")

    assert 'id="mountedWechatMemoryDocSelect"' in html
    assert 'id="mountedWechatGroupInvitePrimaryContact"' in html
    assert 'id="mountedWechatGroupInviteMemoryDocSelect"' in html
    assert 'id="mountedWechatContactSearch"' in html
    assert 'id="mountedWechatGroupInviteKeywords"' not in html
    assert 'id="mountedWechatGroupInviteWelcomeMessage"' in html
    assert "function saveMountedWechatTakeoverConfig" in script
    assert 'source_mode: "douyin_private_message_phone"' in script
    assert "workflowParamDouyinWechatAddFriend" in script
    assert "wechat_add_friend_enabled" in script
    assert "if (isSalesDouyinPrivateNode(node) && addFriendPreset)" not in script


def test_wechat_contact_snapshot_only_keeps_contact_selector_fields():
    rows = h5_chat._normalize_wechat_contact_snapshot(
        [
            {
                "value": "王经理",
                "name": "王伟",
                "contact_key": "wxid-manager",
                "remark": "王经理",
                "wx_no": "manager_wang",
                "raw_json": {"private": "not exposed"},
            },
            {"value": "王经理", "name": "重复项"},
        ]
    )

    assert rows == [
        {
            "value": "王经理",
            "name": "王伟",
            "contact_key": "wxid-manager",
            "remark": "王经理",
            "wx_no": "manager_wang",
        }
    ]


def test_legacy_sales_add_friend_rows_migrate_under_each_douyin_takeover():
    nodes = [
        {
            "id": "legacy-add-friend",
            "time": "07:00",
            "ability_key": "native_wechat_add_friend",
            "plan": {
                "task_kind": "client_workflow",
                "payload": {
                    "action": "native_wechat_add_friend",
                    "params": {"source_mode": "douyin_private_message_wechat_id"},
                },
            },
        },
        {
            "id": "douyin-private-1",
            "time": "14:45",
            "ability_key": "douyin_leads",
            "ability_label": "抖音私信接管",
            "department_id": "sales",
            "plan": {"task_kind": "douyin_leads", "payload": {"action": "stranger_message"}},
        },
        {
            "id": "douyin-private-2",
            "time": "19:15",
            "ability_key": "douyin_leads",
            "ability_label": "抖音私信接管",
            "department_id": "sales",
            "plan": {"task_kind": "douyin_leads", "payload": {"action": "stranger_message"}},
        },
    ]

    migrated = _ensure_sales_douyin_add_friend_children(nodes)
    migrated_again = _ensure_sales_douyin_add_friend_children(migrated)

    assert [node["id"] for node in migrated_again] == ["douyin-private-1", "douyin-private-2"]
    for parent in migrated_again:
        assert parent.get("children", []) == []
        params = parent["plan"]["payload"]["params"]
        assert params["wechat_add_friend_enabled"] is True
        assert params["wechat_add_friend_targets_source"] == "douyin_private_message_phone"
        assert "wechat_add_friend_rules" not in params


def test_douyin_takeover_add_friend_switch_persists_false_through_save_and_activation():
    raw_node = {
        "id": "douyin-private",
        "time": "14:45",
        "end_time": "15:00",
        "ability_key": "douyin_leads",
        "ability_label": "抖音私信接管",
        "department_id": "sales",
        "note": "抖音私信接管",
        "plan": {
            "title": "抖音私信接管",
            "task_kind": "douyin_leads",
            "content": "H5 工作流：抖音私信接管",
            "payload": {
                "action": "stranger_message",
                "params": {
                    "wechat_add_friend_enabled": False,
                    "wechat_add_friend_targets_source": "douyin_private_message_phone",
                },
            },
        },
    }

    saved = _clean_nodes([raw_node])
    saved_params = saved[0]["plan"]["payload"]["params"]
    assert saved_params["wechat_add_friend_enabled"] is False

    migrated = _ensure_sales_douyin_add_friend_children(saved)
    migrated_params = migrated[0]["plan"]["payload"]["params"]
    assert migrated_params["wechat_add_friend_enabled"] is False

    dispatched = _sales_douyin_action_payload(
        migrated[0],
        migrated[0]["plan"]["payload"],
    )
    assert dispatched == {
        "action": "stranger_message",
        "params": {
            "wechat_add_friend_enabled": False,
            "wechat_add_friend_targets_source": "douyin_private_message_phone",
        },
    }


def test_douyin_takeover_add_friend_switch_persists_true_through_save_and_activation():
    raw_node = {
        "id": "douyin-private-enabled",
        "time": "14:45",
        "end_time": "15:00",
        "ability_key": "douyin_leads",
        "ability_label": "Douyin private takeover",
        "department_id": "sales",
        "note": "Douyin private takeover",
        "plan": {
            "title": "Douyin private takeover",
            "task_kind": "douyin_leads",
            "content": "H5 workflow: Douyin private takeover",
            "payload": {
                "action": "stranger_message",
                "params": {
                    "wechat_add_friend_enabled": True,
                    "wechat_add_friend_targets_source": "douyin_private_message_phone",
                },
            },
        },
    }

    saved = _clean_nodes([raw_node])
    saved_params = saved[0]["plan"]["payload"]["params"]
    assert saved_params["wechat_add_friend_enabled"] is True

    migrated = _ensure_sales_douyin_add_friend_children(saved)
    migrated_params = migrated[0]["plan"]["payload"]["params"]
    assert migrated_params["wechat_add_friend_enabled"] is True

    dispatched = _sales_douyin_action_payload(
        migrated[0],
        migrated[0]["plan"]["payload"],
    )
    assert dispatched == {
        "action": "stranger_message",
        "params": {
            "wechat_add_friend_enabled": True,
            "wechat_add_friend_targets_source": "douyin_private_message_phone",
        },
    }


def test_douyin_takeover_add_friend_switch_treats_string_false_as_false():
    raw_node = {
        "id": "douyin-private-string-false",
        "time": "14:45",
        "end_time": "15:00",
        "ability_key": "douyin_leads",
        "ability_label": "抖音私信接管",
        "department_id": "sales",
        "note": "抖音私信接管",
        "plan": {
            "title": "抖音私信接管",
            "task_kind": "douyin_leads",
            "content": "H5 工作流：抖音私信接管",
            "payload": {
                "action": "stranger_message",
                "params": {
                    "wechat_add_friend_enabled": "false",
                    "wechat_add_friend_targets_source": "douyin_private_message_phone",
                },
            },
        },
    }

    migrated = _ensure_sales_douyin_add_friend_children(_clean_nodes([raw_node]))
    dispatched = _sales_douyin_action_payload(migrated[0], migrated[0]["plan"]["payload"])

    assert migrated[0]["plan"]["payload"]["params"]["wechat_add_friend_enabled"] is False
    assert dispatched["params"]["wechat_add_friend_enabled"] is False


def test_h5_custom_employee_collects_add_friend_switch_before_generic_defaults():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    function_start = script.index("function workflowPlanFromParamFields")
    function_end = script.index("function workflowPlanForLookup", function_start)
    function_source = script[function_start:function_end]

    switch_branch = function_source.index("isSalesDouyinPrivateNode(workflowNode)")
    default_branch = function_source.index("workflowNodeUsesPersonaDefaults(workflowNode)")
    assert switch_branch < default_branch
    assert "collectWorkflowQuickPlan" in function_source


def test_h5_custom_employee_reopens_add_friend_switch_from_server_payload():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    detect_start = script.index("function isSalesDouyinPrivateNode")
    detect_end = script.index("function appendSalesWorkflowChild", detect_start)
    detect_source = script[detect_start:detect_end]
    refill_start = script.index("function refillWorkflowParamFields")
    refill_end = script.index("function openWorkflowParamModal", refill_start)
    refill_source = script[refill_start:refill_end]

    assert 'payload.action || params.sales_action' in detect_source
    assert 'action === "stranger_message"' in detect_source
    assert "function workflowLookupIsDouyinLeads" in detect_source
    assert "const isDouyinLookup = workflowLookupIsDouyinLeads(nodeInfo);" in refill_source
    assert "workflowBoolParam(params.wechat_add_friend_enabled, false)" in refill_source


def test_h5_node_save_persists_template_and_reloads_server_copy():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    save_start = script.index("async function saveWorkflowParamNode")
    save_end = script.index("function workflowDemoPlan", save_start)
    save_source = script[save_start:save_end]

    assert "await saveWorkflowTemplate({ notify: false });" in save_source
    assert "节点参数已保存到服务器" in save_source
    assert 'saveWorkflowParamNode().catch((err) => toast(err.message || "保存失败"))' in script


def test_h5_migrates_add_friend_rows_when_loading_sales_templates():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert "return migrateSalesDouyinAddFriendChildren(foldSalesDouyinFollowupNodes(normalized));" in script
    assert "migrateWechatGroupInviteNodes" not in script
    assert "native_wechat_group_invite" not in script
    assert "function migrateSalesDouyinAddFriendChildren(nodes)" in script
    assert "const prepared = list.filter((node) => !isSalesWechatAddFriendRow(node));" in script
