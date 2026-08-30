from fastapi import HTTPException
from pathlib import Path

import backend.app.api.h5_workflows as h5_workflows


H5_APP = Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js"


def test_workflow_permission_gate_applies_to_system_sales_nodes(monkeypatch):
    monkeypatch.setattr(
        h5_workflows,
        "user_feature_flags",
        lambda _db, _user_id: {"private_domain_entry": False},
    )

    node = {
        "ability_key": "native_wechat_poll",
        "department_id": "sales",
        "plan": {"payload": {"action": "native_wechat_poll"}},
    }

    try:
        h5_workflows._assert_workflow_feature_permissions(object(), 1, [node])
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "私域销冠" in str(exc.detail)
    else:
        raise AssertionError("system sales WeChat node bypassed private-domain permission")


def test_workflow_permission_gate_applies_to_douyin_nodes(monkeypatch):
    monkeypatch.setattr(
        h5_workflows,
        "user_feature_flags",
        lambda _db, _user_id: {"douyin_leads_access": False},
    )

    node = {
        "ability_key": "douyin_leads",
        "plan": {"payload": {"action": "douyin_leads"}},
    }

    try:
        h5_workflows._assert_workflow_feature_permissions(object(), 1, [node])
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "抖音获客" in str(exc.detail)
    else:
        raise AssertionError("Douyin workflow node bypassed lead permission")


def test_workflow_permission_gate_applies_to_capability_packages(monkeypatch):
    monkeypatch.setattr(h5_workflows, "user_feature_flags", lambda _db, _user_id: {})
    monkeypatch.setattr(
        h5_workflows,
        "_workflow_capability_package_map",
        lambda: {"comfly.seedance.tvc.pipeline": "comfly_seedance_tvc_skill"},
    )
    monkeypatch.setattr(h5_workflows, "_workflow_visible_package_ids", lambda _db, _user_id: set())

    node = {
        "ability_key": "comfly.seedance.tvc.pipeline",
        "plan": {
            "task_kind": "capability",
            "payload": {"capability_id": "comfly.seedance.tvc.pipeline"},
        },
    }

    try:
        h5_workflows._assert_workflow_feature_permissions(object(), 1, [node])
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "创意分镜头视频" in str(exc.detail)
    else:
        raise AssertionError("workflow capability bypassed package visibility")


def test_workflow_permission_gate_allows_visible_capability_package(monkeypatch):
    monkeypatch.setattr(h5_workflows, "user_feature_flags", lambda _db, _user_id: {})
    monkeypatch.setattr(
        h5_workflows,
        "_workflow_capability_package_map",
        lambda: {"comfly.seedance.tvc.pipeline": "comfly_seedance_tvc_skill"},
    )
    monkeypatch.setattr(
        h5_workflows,
        "_workflow_visible_package_ids",
        lambda _db, _user_id: {"comfly_seedance_tvc_skill"},
    )

    h5_workflows._assert_workflow_feature_permissions(
        object(),
        1,
        [{"ability_key": "comfly.seedance.tvc.pipeline", "plan": {"payload": {"capability_id": "comfly.seedance.tvc.pipeline"}}}],
    )


def test_workflow_permission_gate_applies_to_ip_daily_node_package(monkeypatch):
    monkeypatch.setattr(h5_workflows, "user_feature_flags", lambda _db, _user_id: {})
    monkeypatch.setattr(h5_workflows, "_workflow_capability_package_map", lambda: {})
    monkeypatch.setattr(h5_workflows, "_workflow_visible_package_ids", lambda _db, _user_id: set())

    try:
        h5_workflows._assert_workflow_feature_permissions(
            object(),
            1,
            [{"ability_key": "ip_content_daily", "plan": {"task_kind": "ip_content_daily"}}],
        )
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "IP日更文案" in str(exc.detail)
    else:
        raise AssertionError("IP daily workflow bypassed package visibility")


def test_workflow_permission_gate_checks_legacy_action_children(monkeypatch):
    monkeypatch.setattr(h5_workflows, "user_feature_flags", lambda _db, _user_id: {})
    monkeypatch.setattr(
        h5_workflows,
        "_workflow_capability_package_map",
        lambda: {"comfly.seedance.tvc.pipeline": "comfly_seedance_tvc_skill"},
    )
    monkeypatch.setattr(h5_workflows, "_workflow_visible_package_ids", lambda _db, _user_id: set())

    try:
        h5_workflows._assert_workflow_feature_permissions(
            object(),
            1,
            [{"ability_key": "local_bestseller", "actions": [{"ability_key": "comfly.seedance.tvc.pipeline"}]}],
        )
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "创意分镜头视频" in str(exc.detail)
    else:
        raise AssertionError("legacy workflow action child bypassed package visibility")


def test_h5_sales_node_options_apply_the_same_permission_gate_as_custom_nodes():
    script = H5_APP.read_text(encoding="utf-8")
    start = script.index("function workflowSalesNodeLookups()")
    end = script.index("const WORKFLOW_NODE_GROUP_ORDER", start)
    block = script[start:end]

    assert "!abilityIsActionable(lookup.node)" in block
