"""服务端：抖音私信接管节点支持「AI 记忆接管」（回复策略 + 记忆文件）。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.api import h5_workflows as wf  # noqa: E402


def test_action_payload_keeps_ai_memory_and_memory_doc_ids():
    node = {"note": "抖音私信接管", "ability_key": "douyin_leads"}
    payload = {
        "action": "stranger_message",
        "params": {"reply_mode": "ai_memory", "memory_doc_ids": ["faq-doc"]},
    }

    result = wf._sales_douyin_action_payload(node, payload)

    assert result["action"] == "stranger_message"
    assert result["params"]["reply_mode"] == "ai_memory"
    assert result["params"]["memory_doc_ids"] == ["faq-doc"]
    assert result["params"]["wechat_add_friend_targets_source"] == "douyin_private_message_phone"


def test_action_payload_still_normalizes_unknown_reply_mode():
    node = {"note": "抖音私信接管", "ability_key": "douyin_leads"}
    payload = {"action": "stranger_message", "params": {"reply_mode": "ai_whatever"}}

    result = wf._sales_douyin_action_payload(node, payload)

    assert result["params"]["reply_mode"] == "fixed"
    assert "memory_doc_ids" not in result["params"]


def test_private_switch_normalize_keeps_ai_memory():
    node = {"ability_key": "douyin_leads", "ability_label": "抖音私信接管", "note": "抖音私信接管"}
    plan = {"task_kind": "douyin_leads", "title": "抖音私信接管"}
    payload = {
        "action": "stranger_message",
        "params": {"reply_mode": "ai_memory", "memory_doc_ids": ["faq-doc", "faq-doc", ""]},
    }

    wf._normalize_douyin_private_switch(node, plan, payload)

    params = payload["params"]
    assert params["reply_mode"] == "ai_memory"
    assert params["memory_doc_ids"] == ["faq-doc"]
    assert params["wechat_add_friend_targets_source"] == "douyin_private_message_phone"


def test_memory_helpers_filter_docs_by_selected_ids():
    docs = [{"doc_id": "a", "content": "A"}, {"doc_id": "b", "content": "B"}]

    assert wf._clean_douyin_memory_doc_ids(["a", {"doc_id": "b"}, "a", ""]) == ["a", "b"]
    assert wf._douyin_memory_docs_for_ids(docs, ["b"]) == [{"doc_id": "b", "content": "B"}]
    assert wf._douyin_memory_docs_for_ids(docs, []) == docs


def test_h5_app_douyin_node_offers_memory_takeover():
    source = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert 'optionHtml("ai_memory", "AI 记忆接管（按记忆文件回复）")' in source
    assert 'id="workflowParamDouyinMemoryField"' in source
    assert "workflowParamDouyinMemoryDocs" in source
    assert "function bindWorkflowDouyinReplyModeControls()" in source
    assert 'douyinReplyMode === "ai_memory" ? "ai_memory"' in source


def test_node_picker_has_dedicated_memory_takeover_node():
    source = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert '{ key: "douyin_leads", label: "抖音私信记忆接管", note: "抖音私信记忆接管" }' in source
    assert "function salesWorkflowIsMemoryTakeoverNote(" in source
    assert "@@memory_takeover" in source
    assert "douyinDefaultReplyMode" in source


def test_action_payload_defaults_memory_mode_from_node_note():
    node = {"note": "抖音私信记忆接管", "ability_key": "douyin_leads"}

    result = wf._sales_douyin_action_payload(node, {"action": "stranger_message", "params": {}})

    assert result["action"] == "stranger_message"
    assert result["params"]["reply_mode"] == "ai_memory"
    assert result["params"]["wechat_add_friend_targets_source"] == "douyin_private_message_phone"


def test_action_payload_keeps_legacy_node_untouched():
    node = {"note": "抖音私信接管", "ability_key": "douyin_leads"}

    result = wf._sales_douyin_action_payload(node, {"action": "stranger_message", "params": {}})

    assert "reply_mode" not in result.get("params", {})


def test_memory_takeover_node_uses_private_takeover_fields():
    """「抖音私信记忆接管」必须走私信接管表单：否则弹窗显示的是搜索采集的地区/关键词参数。"""
    source = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    start = source.index("function isSalesDouyinPrivateNode(node) {")
    body = source[start : start + 1800]

    assert "salesWorkflowIsMemoryTakeoverNote(text)" in body


def test_server_private_takeover_node_detect_accepts_memory_note():
    node = {
        "ability_key": "douyin_leads",
        "ability_label": "抖音私信记忆接管",
        "note": "抖音私信记忆接管",
        "plan": {
            "task_kind": "douyin_leads",
            "title": "抖音私信记忆接管",
            "payload": {"action": "stranger_message", "params": {}},
        },
    }

    assert wf._is_douyin_private_takeover_node(node) is True


def test_h5_node_detect_accepts_ai_memory_params():
    """新建未保存、或参数里就是 ai_memory 的节点，也必须走私信接管表单（不是采集表单）。"""
    source = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    start = source.index("function isSalesDouyinPrivateNode(node) {")
    body = source[start : start + 1400]

    assert 'params.reply_mode || "").trim().toLowerCase() === "ai_memory"' in body
    assert "workflowBoolParam(params.memory_takeover, false)" in body


def test_h5_memory_node_hides_add_friend_field():
    source = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert 'id="workflowParamDouyinWechatAddFriendField"' in source
    assert '$("workflowParamDouyinWechatAddFriendField")?.classList.toggle("hidden", mode === "ai_memory")' in source


def test_h5_field_render_uses_lookup_and_node_params():
    """刚添加、还没写 action 的记忆接管节点，也必须渲染成私信接管表单。"""
    source = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    start = source.index("function workflowFieldsHtmlForNode(node, workflowNode = null, lookup = null) {")
    body = source[start : start + 1800]

    assert "memoryTakeoverFields" in body
    assert "lookup.optionLabel || lookup.defaultNote" in body
    assert "workflowBoolParam(douyinNodeParams.memory_takeover, false)" in body
    assert 'workflowFieldsHtmlForNode(lookup.node, node, lookup)' in source


def test_h5_add_node_form_has_private_takeover_branch_and_memory_field():
    """H5「添加节点」表单也要有私信接管分支（原来只认采集/触达，节点被当成 search_collect）。"""
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")

    assert 'id="workflowNodeDouyinMemoryField"' in html
    assert 'id="workflowNodeDouyinMemoryDocs"' in html
    assert "const showDouyinMemoryTakeover = showDouyinPrivate && salesWorkflowIsMemoryTakeoverNote(selectedNote);" in script
    assert '$("workflowNodeDouyinMemoryField")?.classList.toggle("hidden", !showDouyinMemoryTakeover);' in script
    # 添加节点时私信接管/记忆接管要生成 stranger_message 计划，而不是 search_collect
    assert 'reply_mode: memoryTakeoverNode ? "ai_memory" : "fixed",' in script
    assert 'const privateMemoryDocIds = memoryTakeoverNode ? selectedMultiValues("workflowNodeDouyinMemoryDocs") : [];' in script


def test_h5_add_form_never_shows_collection_params_for_takeover_note():
    """硬规则：备注/名字里是私信接管的节点，添加表单永远不显示精准获客参数。"""
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert 'const noteIsPrivateTakeover = /私信接管|私信引流|记忆接管/.test(selectedNote);' in script
    assert "&& !showDouyinPrivate" in script
