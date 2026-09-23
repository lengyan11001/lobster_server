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
    seen_slots: list[str] = []
    monkeypatch.setattr(
        h5_workflows,
        "_personal_default_template",
        lambda db, user_id, installation_id="": (seen_slots.append(str(installation_id)), personal)[1],
    )
    monkeypatch.setattr(h5_workflows, "_current_personal_schedule_template", lambda db, user_id, row: current)
    monkeypatch.setattr(h5_workflows, "_sales_digital_human_provider", lambda extra, template: "shanjian_v2")
    monkeypatch.setattr(h5_workflows, "_sales_digital_human_template_id", lambda personal, current: "style-current")
    monkeypatch.setattr(
        h5_workflows,
        "_h5_dh_context_params",
        lambda db, user_id, installation_id="": {
            "requirements": {"industry": "new"},
            "keyword_ids": [11],
            "keyword_texts": ["new keyword"],
            "competitor_ids": [22],
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
    queried_competitor_ids = []

    def active_competitors(db, user_id, ids):
        queried_competitor_ids.extend(ids)
        return [SimpleNamespace(last_fetch_at=datetime.utcnow())]

    monkeypatch.setattr(h5_workflows, "_active_competitors_for_ids", active_competitors)
    monkeypatch.setattr(h5_workflows, "_missing_sales_persona_fields", lambda requirements: [])
    monkeypatch.setattr(
        h5_workflows,
        "_local_bestseller_profile_from_persona",
        lambda requirements: {"photo_asset_id": "photo-1", "city": "shenzhen"},
    )
    monkeypatch.setattr(h5_workflows, "_device_is_online", lambda db, user_id, installation_id: True)
    # 现在人设必须以"当前模板关联的资料调查"为准：这里给当前模板挂一份资料调查，
    # 否则启动会被新规则拦下（模板未关联资料调查）。
    monkeypatch.setattr(
        h5_workflows,
        "_survey_for_template",
        lambda db, row: SimpleNamespace(id=1, requirements={"industry": "new"}),
    )

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
    assert queried_competitor_ids == [22]
    # 启动校验必须按设备槽位解析模板资源，否则会读到账号级的空壳行。
    assert seen_slots == ["online-1"]


def test_h5_template_context_includes_competitor_ids(monkeypatch, db_session):
    personal = SimpleNamespace(user_id=31, meta={})
    current = SimpleNamespace(user_id=31, meta={})
    monkeypatch.setattr(
        scheduled_tasks,
        "_h5_dh_personal_default_template",
        lambda db, user_id, installation_id="": personal,
    )
    monkeypatch.setattr(scheduled_tasks, "_h5_dh_current_template", lambda db, user_id, row: current)
    monkeypatch.setattr(
        scheduled_tasks,
        "_personal_default_template_payload_with_resources",
        lambda db, row: {
            "requirements": {},
            "keyword_ids": [],
            "competitor_ids": [22, 23],
            "keywords": [],
            "competitors": [
                {"account_name": "competitor-a"},
                {"account_name": "competitor-b"},
            ],
            "memory_doc_ids": [],
            "memory_docs": [],
        },
    )

    context = scheduled_tasks._h5_dh_context_params(db_session, 31)

    assert context["competitor_ids"] == [22, 23]
    assert context["competitors"] == ["competitor-a", "competitor-b"]


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


def test_sales_activation_is_blocked_when_the_template_has_no_persona_survey(monkeypatch, db_session, test_user):
    """当前模板没关联资料调查时直接拦下：不许拿个人默认行里的旧人设顶上。

    线上出现过"模板换成新行业了，生成的数字人内容还是旧行业"就是这条兜底造成的。
    """
    import pytest
    from fastapi import HTTPException

    personal = SimpleNamespace(
        id=13,
        user_id=test_user.id,
        installation_id="slot-no-survey",
        requirements={"basic_profile": {"profile_name": "旧行业人设"}},
        meta={},
    )
    current = SimpleNamespace(id=14, user_id=test_user.id, requirements={}, meta={})
    monkeypatch.setattr(h5_workflows, "_personal_default_template", lambda db, user_id, installation_id="": personal)
    monkeypatch.setattr(h5_workflows, "_current_personal_schedule_template", lambda db, user_id, row: current)
    monkeypatch.setattr(h5_workflows, "_survey_for_template", lambda db, row: None)
    monkeypatch.setattr(
        h5_workflows,
        "_h5_dh_context_params",
        lambda db, user_id, installation_id="": {
            "requirements": {"basic_profile": {"profile_name": "旧行业人设"}},
            "keyword_ids": [1],
            "competitors": ["x"],
            "competitor_ids": [1],
            "memory_docs": [{"id": 1, "title": "m"}],
            "memory_doc_ids": ["1"],
        },
    )
    monkeypatch.setattr(h5_workflows, "_personal_default_resource_overrides", lambda personal, current: {
        "keyword_ids": False,
        "competitor_ids": False,
        "memory_doc_ids": False,
    })
    monkeypatch.setattr(h5_workflows, "_active_keywords_for_ids", lambda db, user_id, ids: [SimpleNamespace()])
    monkeypatch.setattr(
        h5_workflows,
        "_active_competitors_for_ids",
        lambda db, user_id, ids: [SimpleNamespace(last_fetch_at=datetime.utcnow())],
    )
    monkeypatch.setattr(h5_workflows, "_missing_sales_persona_fields", lambda requirements: [])
    monkeypatch.setattr(h5_workflows, "_device_is_online", lambda db, user_id, installation_id: True)
    monkeypatch.setattr(h5_workflows, "_sales_digital_human_provider", lambda extra, template: "shanjian_v2")
    monkeypatch.setattr(h5_workflows, "_sales_digital_human_template_id", lambda personal, current: "style-x")
    monkeypatch.setattr(h5_workflows, "_has_active_keywords", lambda *args, **kwargs: True)
    monkeypatch.setattr(h5_workflows, "_has_active_competitors", lambda *args, **kwargs: True)
    monkeypatch.setattr(h5_workflows, "_has_active_memory_docs", lambda *args, **kwargs: True)

    nodes = [
        {
            "id": "sales-digital",
            "department_id": "sales",
            "plan": {
                "task_kind": "client_workflow",
                "payload": {"action": "shanjian_digital_human_video", "params": {}},
            },
        }
    ]

    with pytest.raises(HTTPException) as excinfo:
        h5_workflows._prepare_sales_workflow_nodes(
            db=db_session,
            owner=test_user,
            installation_id="slot-no-survey",
            template_name="销售员工",
            nodes=nodes,
            snapshot_extra=None,
        )

    assert "资料调查" in str(excinfo.value.detail)



def _content_node(
    node_id: str,
    *,
    task_kind: str = "client_workflow",
    action: str = "",
    tasks=None,
    label: str = "",
    ability_key: str = "",
) -> dict:
    if task_kind == "douyin_leads":
        payload = {"action": action or "stranger_message", "params": {}}
    elif task_kind == "client_workflow":
        payload = {"action": action, "params": {}}
    else:
        payload = {"tasks": ["moments_candidate"] if tasks is None else list(tasks)}
    node = {
        "id": node_id,
        "time": "08:00",
        "ability_label": label,
        "plan": {"task_kind": task_kind, "title": label, "payload": payload},
    }
    if ability_key:
        node["ability_key"] = ability_key
    return node


_COMPLETE_LOCAL_PROFILE = {
    "gender": "女",
    "identity": "顾问",
    "industry": "同城获客",
    "province": "广东",
    "city": "深圳",
    "hometown": "湖南",
    "age_label": "90后",
    "target_age": "商家",
    "style": "口播",
    "photo_asset_id": "photo-1",
}


def _stub_ip_persona_gate(
    monkeypatch,
    *,
    survey=True,
    keywords=True,
    competitors=True,
    memory=True,
    synced=True,
    profile=None,
    avatars=True,
    voices=True,
    edit_template=True,
    device_online=True,
    requirements=None,
):
    personal = SimpleNamespace(id=13, user_id=1, installation_id="slot-custom", requirements={}, meta={})
    current = SimpleNamespace(id=14, user_id=1, requirements={}, meta={})
    resolved_profile = dict(_COMPLETE_LOCAL_PROFILE if profile is None else profile)
    resolved_requirements = (
        {"basic_profile": {"profile_name": "孔明"}} if requirements is None else requirements
    )
    monkeypatch.setattr(h5_workflows, "_personal_default_template", lambda db, user_id, installation_id="": personal)
    monkeypatch.setattr(h5_workflows, "_current_personal_schedule_template", lambda db, user_id, row: current)
    monkeypatch.setattr(h5_workflows, "_survey_for_template", lambda db, row: SimpleNamespace(id=58) if survey else None)
    monkeypatch.setattr(
        h5_workflows,
        "_h5_dh_context_params",
        lambda db, user_id, installation_id="": {
            "requirements": resolved_requirements,
            "keyword_ids": [1] if keywords else [],
            "keyword_texts": ["同城"] if keywords else [],
            "competitors": ["同行"] if competitors else [],
            "competitor_ids": [1] if competitors else [],
            "memory_docs": [{"id": "m1", "title": "记忆"}] if memory else [],
            "memory_doc_ids": ["m1"] if memory else [],
            "digital_human_resources": {
                "avatars": [{"provider": "shanjian", "virtualman_id": "vm-1", "title": "形象"}] if avatars else [],
                "voices": [{"voice": "voice-1"}] if voices else [],
            },
        },
    )
    monkeypatch.setattr(h5_workflows, "_personal_default_resource_overrides", lambda personal, current: {
        "keyword_ids": False,
        "competitor_ids": False,
        "memory_doc_ids": False,
    })
    monkeypatch.setattr(h5_workflows, "_active_keywords_for_ids", lambda db, user_id, ids: [SimpleNamespace()] if ids else [])
    monkeypatch.setattr(
        h5_workflows,
        "_active_competitors_for_ids",
        lambda db, user_id, ids, synced=synced: (
            [SimpleNamespace(last_fetch_at=datetime.utcnow() if synced else None)] if ids else []
        ),
    )
    monkeypatch.setattr(h5_workflows, "_missing_sales_persona_fields", lambda requirements: [])
    monkeypatch.setattr(
        h5_workflows,
        "_local_bestseller_profile_from_persona",
        lambda requirements, profile=resolved_profile: dict(profile),
    )
    monkeypatch.setattr(h5_workflows, "_device_is_online", lambda db, user_id, installation_id: device_online)
    monkeypatch.setattr(h5_workflows, "_sales_digital_human_provider", lambda extra, template: "shanjian_v2")
    monkeypatch.setattr(
        h5_workflows,
        "_sales_digital_human_template_id",
        lambda personal, current: "style-x" if edit_template else "",
    )
    monkeypatch.setattr(h5_workflows, "_has_active_keywords", lambda *args, **kwargs: True)
    monkeypatch.setattr(h5_workflows, "_has_active_competitors", lambda *args, **kwargs: True)
    monkeypatch.setattr(h5_workflows, "_has_active_memory_docs", lambda *args, **kwargs: True)


def _activate_custom(monkeypatch, db_session, test_user, nodes, **kwargs):
    _stub_ip_persona_gate(monkeypatch, **kwargs)
    return h5_workflows._prepare_sales_workflow_nodes(
        db=db_session,
        owner=test_user,
        installation_id="slot-custom",
        template_name="短视频+微信员工（鲸海）",
        nodes=nodes,
        snapshot_extra={"source": "own"},
    )


def _blocked_detail(monkeypatch, db_session, test_user, nodes, **kwargs):
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        _activate_custom(monkeypatch, db_session, test_user, nodes, **kwargs)
    assert excinfo.value.status_code == 400
    detail = str(excinfo.value.detail)
    assert "当前工作流无法启动" in detail
    assert "销售员工无法启动" not in detail
    return detail


def _custom_short_video_nodes() -> list[dict]:
    return [
        _content_node("dm", task_kind="douyin_leads", action="stranger_message", label="\u6296\u97f3\u79c1\u4fe1\u63a5\u7ba1"),
        _content_node("city", action="local_bestseller_daily_video", label="\u521b\u4f5c\u540c\u57ce\u7206\u6b3e\u89c6\u9891"),
        _content_node("dh", action="shanjian_digital_human_video", label="\u521b\u4f5c\u6570\u5b57\u4eba\u53e3\u64ad\u89c6\u9891"),
        _content_node("wx", action="native_wechat_poll", label="\u5fae\u4fe1\u79c1\u4fe1\u63a5\u7ba1"),
        _content_node("like", action="native_wechat_moments_engage", label="\u5fae\u4fe1\u670b\u53cb\u5708\u70b9\u8d5e\u8bc4\u8bba"),
        _content_node("moments", task_kind="ip_content_daily", tasks=["moments_candidate"], label="\u670b\u53cb\u5708\u56fe\u6587"),
    ]


def test_custom_content_workflow_requires_survey_even_without_sales_label(monkeypatch, db_session, test_user):
    import pytest
    from fastapi import HTTPException

    _stub_ip_persona_gate(monkeypatch, survey=False)
    with pytest.raises(HTTPException) as excinfo:
        h5_workflows._prepare_sales_workflow_nodes(
            db=db_session,
            owner=test_user,
            installation_id="slot-custom",
            template_name="\u77ed\u89c6\u9891+\u5fae\u4fe1\u5458\u5de5\uff08\u9cb8\u6d77\uff09",
            nodes=_custom_short_video_nodes(),
            snapshot_extra={"source": "own"},
        )
    detail = str(excinfo.value.detail)
    assert excinfo.value.status_code == 400
    assert "\u5f53\u524d\u5de5\u4f5c\u6d41\u65e0\u6cd5\u542f\u52a8" in detail
    assert "\u8d44\u6599\u8c03\u67e5" in detail
    assert "\u9500\u552e\u5458\u5de5\u65e0\u6cd5\u542f\u52a8" not in detail


def test_custom_content_workflow_requires_keywords_and_competitors_separately(monkeypatch, db_session, test_user):
    import pytest
    from fastapi import HTTPException

    _stub_ip_persona_gate(monkeypatch, keywords=False)
    with pytest.raises(HTTPException) as missing_keywords:
        h5_workflows._prepare_sales_workflow_nodes(
            db=db_session,
            owner=test_user,
            installation_id="slot-custom",
            template_name="\u77ed\u89c6\u9891+\u5fae\u4fe1\u5458\u5de5\uff08\u9cb8\u6d77\uff09",
            nodes=_custom_short_video_nodes(),
            snapshot_extra=None,
        )
    assert "\u5173\u952e\u8bcd" in str(missing_keywords.value.detail)

    _stub_ip_persona_gate(monkeypatch, competitors=False)
    with pytest.raises(HTTPException) as missing_competitors:
        h5_workflows._prepare_sales_workflow_nodes(
            db=db_session,
            owner=test_user,
            installation_id="slot-custom",
            template_name="\u77ed\u89c6\u9891+\u5fae\u4fe1\u5458\u5de5\uff08\u9cb8\u6d77\uff09",
            nodes=_custom_short_video_nodes(),
            snapshot_extra=None,
        )
    assert "\u540c\u884c\u8d26\u53f7" in str(missing_competitors.value.detail)


def test_custom_content_workflow_starts_when_persona_resources_exist(monkeypatch, db_session, test_user):
    _stub_ip_persona_gate(monkeypatch)
    prepared = h5_workflows._prepare_sales_workflow_nodes(
        db=db_session,
        owner=test_user,
        installation_id="slot-custom",
        template_name="\u77ed\u89c6\u9891+\u5fae\u4fe1\u5458\u5de5\uff08\u9cb8\u6d77\uff09",
        nodes=_custom_short_video_nodes(),
        snapshot_extra=None,
    )
    moments = next(node for node in prepared if node["id"] == "moments")
    douyin = next(node for node in prepared if node["id"] == "dm")
    assert moments["plan"]["payload"]["template_source"] == "personal_current"
    assert "keyword_ids" not in moments["plan"]["payload"]
    douyin_params = douyin["plan"]["payload"].get("params") or {}
    assert "wechat_add_friend_enabled" not in douyin_params


def test_wechat_and_douyin_only_custom_workflow_does_not_require_persona(db_session, test_user):
    nodes = [
        _content_node("dm", task_kind="douyin_leads", action="stranger_message", label="\u6296\u97f3\u79c1\u4fe1\u63a5\u7ba1"),
        _content_node("wx", action="native_wechat_poll", label="\u5fae\u4fe1\u79c1\u4fe1\u63a5\u7ba1"),
        _content_node("like", action="native_wechat_moments_engage", label="\u5fae\u4fe1\u670b\u53cb\u5708\u70b9\u8d5e\u8bc4\u8bba"),
    ]
    prepared = h5_workflows._prepare_sales_workflow_nodes(
        db=db_session,
        owner=test_user,
        installation_id="slot-custom",
        template_name="\u53ea\u505a\u79c1\u57df",
        nodes=nodes,
        snapshot_extra=None,
    )
    assert prepared == nodes


def test_non_sales_system_catalog_is_not_forced_through_persona_gate(monkeypatch, db_session, test_user):
    monkeypatch.setattr(h5_workflows, "_enabled_system_workflow_keys", lambda: {"system_sales", "system_short_video_wechat", "system_douyin_leads"})
    nodes = _custom_short_video_nodes()
    prepared = h5_workflows._prepare_sales_workflow_nodes(
        db=db_session,
        owner=test_user,
        installation_id="slot-custom",
        template_name="\u77ed\u89c6\u9891+\u5fae\u4fe1\u5458\u5de5",
        nodes=nodes,
        snapshot_extra={"template_key": "system_short_video_wechat"},
    )
    assert prepared == nodes

def test_local_bestseller_alone_starts_with_linked_complete_profile(monkeypatch, db_session, test_user):
    nodes = [_content_node("city", action="local_bestseller_daily_video", label="创作同城爆款视频")]
    prepared = _activate_custom(monkeypatch, db_session, test_user, nodes, keywords=False, competitors=False, memory=False)
    assert prepared[0]["id"] == "city"


def test_local_bestseller_without_survey_does_not_list_profile_fields(monkeypatch, db_session, test_user):
    nodes = [_content_node("city", action="local_bestseller_daily_video", label="创作同城爆款视频")]
    detail = _blocked_detail(
        monkeypatch,
        db_session,
        test_user,
        nodes,
        survey=False,
        keywords=False,
        competitors=False,
        memory=False,
        profile={},
    )
    assert "资料调查" in detail
    for absent in ("性别", "人物照片", "关键词", "同行", "记忆", "你的名字", "主要分享什么"):
        assert absent not in detail


def test_local_bestseller_incomplete_profile_lists_local_fields_not_name(monkeypatch, db_session, test_user):
    nodes = [_content_node("city", action="local_bestseller_daily_video", label="创作同城爆款视频")]
    detail = _blocked_detail(
        monkeypatch,
        db_session,
        test_user,
        nodes,
        keywords=False,
        competitors=False,
        memory=False,
        profile={"city": "深圳", "photo_asset_id": "photo-1"},
    )
    for present in ("性别", "你是做什么的", "业务/产品或主要分享内容", "现居省份", "籍贯", "出生年代", "想卖给谁/目标客户", "视频风格"):
        assert present in detail
    for absent in ("你的名字", "主要分享什么", "看完后希望用户做什么", "关键词", "同行账号", "记忆文件", "现居城市", "人物照片"):
        assert absent not in detail


def test_digital_human_alone_requires_script_assets_not_competitors(monkeypatch, db_session, test_user):
    nodes = [_content_node("dh", action="shanjian_digital_human_video", label="创作数字人口播视频")]
    prepared = _activate_custom(monkeypatch, db_session, test_user, nodes, competitors=False)
    assert prepared[0]["plan"]["payload"]["action"] == "shanjian_digital_human_video"

    empty = _blocked_detail(monkeypatch, db_session, test_user, nodes, competitors=False, requirements={})
    assert "没有可用内容" in empty
    assert "同行" not in empty

    missing_keyword = _blocked_detail(monkeypatch, db_session, test_user, nodes, keywords=False, competitors=False)
    assert "关键词" in missing_keyword
    assert "同行" not in missing_keyword

    missing_assets = _blocked_detail(
        monkeypatch,
        db_session,
        test_user,
        nodes,
        competitors=False,
        avatars=False,
        voices=False,
        edit_template=False,
    )
    assert "数字人形象" in missing_assets
    assert "声音分身" in missing_assets
    assert "剪辑模板" in missing_assets
    assert "同行" not in missing_assets


def test_moments_alone_requires_keywords_and_synced_competitors_only(monkeypatch, db_session, test_user):
    nodes = [_content_node("moments", task_kind="ip_content_daily", tasks=["moments_candidate"], ability_key="ip_content_moments", label="朋友圈图文")]
    prepared = _activate_custom(monkeypatch, db_session, test_user, nodes, survey=False, memory=False)
    assert prepared[0]["plan"]["payload"]["tasks"] == ["moments_candidate"]

    detail = _blocked_detail(monkeypatch, db_session, test_user, nodes, survey=False, memory=False, keywords=False)
    assert "关键词" in detail
    assert "资料调查" not in detail
    assert "记忆" not in detail

    unsynced = _blocked_detail(monkeypatch, db_session, test_user, nodes, survey=False, memory=False, synced=False)
    assert "还没有同步" in unsynced
    assert "关键词" not in unsynced


def test_oral_nodes_require_only_their_own_sources(monkeypatch, db_session, test_user):
    industry = [_content_node("oral", task_kind="ip_content_daily", tasks=["industry_hot_oral"], ability_key="ip_content_oral", label="行业口播")]
    prepared = _activate_custom(
        monkeypatch, db_session, test_user, industry, survey=False, competitors=False, memory=False
    )
    assert prepared[0]["plan"]["payload"]["tasks"] == ["industry_hot_oral"]

    professional = [_content_node("oral", task_kind="ip_content_daily", tasks=["professional_ip_oral"], ability_key="ip_content_oral", label="专业口播")]
    prepared = _activate_custom(
        monkeypatch, db_session, test_user, professional, survey=False, keywords=False, memory=False
    )
    assert prepared[0]["plan"]["payload"]["tasks"] == ["professional_ip_oral"]

    unsynced = _blocked_detail(
        monkeypatch, db_session, test_user, professional, survey=False, keywords=False, memory=False, synced=False
    )
    assert "还没有同步" in unsynced
    assert "关键词" not in unsynced
    assert "资料调查" not in unsynced


def test_empty_oral_tasks_do_not_expand_into_three_daily_tasks(monkeypatch, db_session, test_user):
    oral = [_content_node("oral", task_kind="ip_content_daily", tasks=[], ability_key="ip_content_oral", label="口播")]
    prepared = _activate_custom(monkeypatch, db_session, test_user, oral, survey=False, memory=False)
    assert prepared[0]["plan"]["payload"]["tasks"] == ["industry_hot_oral", "professional_ip_oral"]

    daily = [_content_node("daily", task_kind="ip_content_daily", tasks=[], label="日更")]
    prepared = _activate_custom(monkeypatch, db_session, test_user, daily, survey=False, memory=False)
    assert prepared[0]["plan"]["payload"]["tasks"] == [
        "industry_hot_oral",
        "professional_ip_oral",
        "moments_candidate",
    ]


def test_child_nodes_are_included_in_custom_material_union(monkeypatch, db_session, test_user):
    parent = _content_node("dm", task_kind="douyin_leads", action="stranger_message", label="抖音私信接管")
    parent["children"] = [
        _content_node("moments", task_kind="ip_content_daily", tasks=["moments_candidate"], ability_key="ip_content_moments", label="朋友圈图文"),
    ]
    prepared = _activate_custom(monkeypatch, db_session, test_user, [parent], survey=False, memory=False)
    child = prepared[0]["children"][0]
    assert child["plan"]["payload"]["tasks"] == ["moments_candidate"]

    city_parent = _content_node("dm", task_kind="douyin_leads", action="stranger_message", label="抖音私信接管")
    city_parent["children"] = [
        _content_node("city", action="local_bestseller_daily_video", label="创作同城爆款视频"),
    ]
    detail = _blocked_detail(
        monkeypatch,
        db_session,
        test_user,
        [city_parent],
        survey=False,
        keywords=False,
        competitors=False,
        memory=False,
        profile={},
    )
    assert "资料调查" in detail
    assert "性别" not in detail
    assert "关键词" not in detail


def test_custom_short_video_union_blocks_only_real_dependencies(monkeypatch, db_session, test_user):
    detail = _blocked_detail(
        monkeypatch,
        db_session,
        test_user,
        _custom_short_video_nodes(),
        keywords=False,
        competitors=False,
        memory=False,
        profile={},
        avatars=False,
        voices=False,
        edit_template=False,
        device_online=False,
    )
    for present in (
        "关键词",
        "同行账号",
        "记忆文件",
        "性别",
        "你是做什么的",
        "业务/产品或主要分享内容",
        "现居省份",
        "现居城市",
        "籍贯",
        "出生年代",
        "想卖给谁/目标客户",
        "视频风格",
        "人物照片",
        "数字人形象",
        "声音分身",
        "剪辑模板",
        "不在线",
    ):
        assert present in detail, present
    for absent in ("你的名字", "主要分享什么", "看完后希望用户做什么", "销售员工无法启动"):
        assert absent not in detail, absent
