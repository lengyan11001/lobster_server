"""Personal IP defaults are per installation slot.

Setting a default on one device must never rewrite the default another device
sees, so the personal-default row is keyed by (user_id, installation_id, name)
and a device that has no row of its own falls back to the account-level row.
"""
from __future__ import annotations


def _personal_default_rows(db, user_id: int):
    from backend.app.models import IPContentScheduleTemplate

    return (
        db.query(IPContentScheduleTemplate)
        .filter(IPContentScheduleTemplate.user_id == user_id)
        .all()
    )


def test_save_and_read_are_scoped_per_installation(db_session, test_user):
    from backend.app.api import ip_content_studio as studio

    studio.save_personal_default_ip_content_config(
        body=studio.ScheduleTemplateBody(
            requirements={"common": "设备A"},
            meta={"source": "test"},
        ),
        x_installation_id="slot-aaaa-0001",
        current_user=test_user,
        db=db_session,
    )
    studio.save_personal_default_ip_content_config(
        body=studio.ScheduleTemplateBody(
            requirements={"common": "设备B"},
            meta={"source": "test"},
        ),
        x_installation_id="slot-bbbb-0002",
        current_user=test_user,
        db=db_session,
    )

    rows = {row.installation_id: row for row in _personal_default_rows(db_session, test_user.id)}
    assert set(rows) == {"slot-aaaa-0001", "slot-bbbb-0002"}
    assert rows["slot-aaaa-0001"].requirements["common"] == "设备A"
    assert rows["slot-bbbb-0002"].requirements["common"] == "设备B"

    payload_a = studio.get_personal_default_ip_content_config(
        x_installation_id="slot-aaaa-0001",
        current_user=test_user,
        db=db_session,
    )
    payload_b = studio.get_personal_default_ip_content_config(
        x_installation_id="slot-bbbb-0002",
        current_user=test_user,
        db=db_session,
    )
    assert payload_a["item"]["requirements"]["common"] == "设备A"
    assert payload_b["item"]["requirements"]["common"] == "设备B"


def test_new_device_does_not_inherit_account_level_row(db_session, test_user):
    from backend.app.api import ip_content_studio as studio
    from backend.app.models import IPContentScheduleTemplate

    # Legacy/H5 saves without a selected device stay account level.
    studio.save_personal_default_ip_content_config(
        body=studio.ScheduleTemplateBody(
            requirements={"common": "账号默认"},
            meta={"source": "test"},
        ),
        x_installation_id="",
        current_user=test_user,
        db=db_session,
    )

    # 口径（2026-09-19）：新设备不再继承账号级配置——每个槽位只认自己那份，
    # 没配就是空的（由工作流明确报"请在当前设备模板里配置"），不串台。
    payload = studio.get_personal_default_ip_content_config(
        x_installation_id="slot-new-0003",
        current_user=test_user,
        db=db_session,
    )
    assert (payload["item"].get("requirements") or {}).get("common") in (None, "")

    # 该设备保存后生成自己的行，且不能改写账号级行。
    studio.save_personal_default_ip_content_config(
        body=studio.ScheduleTemplateBody(
            requirements={"common": "新设备"},
            meta={"source": "test"},
        ),
        x_installation_id="slot-new-0003",
        current_user=test_user,
        db=db_session,
    )
    account_row = (
        db_session.query(IPContentScheduleTemplate)
        .filter(
            IPContentScheduleTemplate.user_id == test_user.id,
            IPContentScheduleTemplate.installation_id == "",
        )
        .one()
    )
    assert account_row.requirements["common"] == "账号默认"


def test_execution_resolver_prefers_the_slot_row(db_session, test_user):
    from backend.app.api import ip_content_studio as studio
    from backend.app.models import IPContentScheduleTemplate

    name = studio._PERSONAL_DEFAULT_TEMPLATE_NAME
    db_session.add(
        IPContentScheduleTemplate(
            user_id=test_user.id,
            installation_id="",
            name=name,
            requirements={"profile_name": "账号"},
            meta={},
            status="active",
        )
    )
    db_session.add(
        IPContentScheduleTemplate(
            user_id=test_user.id,
            installation_id="slot-x",
            name=name,
            requirements={"profile_name": "设备X"},
            meta={},
            status="active",
        )
    )
    db_session.commit()

    assert studio._personal_default_row_for_slot(db_session, test_user.id, "slot-x").requirements["profile_name"] == "设备X"
    # 口径：槽位只认自己的行——未知槽位不再回落账号级（宁可报错也不串台）。
    assert studio._personal_default_row_for_slot(db_session, test_user.id, "slot-y") is None
    # 不传槽位（账号级页面）时才返回账号级行。
    assert studio._personal_default_row_for_slot(db_session, test_user.id, "").requirements["profile_name"] == "账号"


def test_task_payload_slot_is_used_by_run_payload(db_session, test_user):
    from backend.app.api import scheduled_tasks

    payload = {"h5_context": {"installation_id": "slot-x"}}
    assert scheduled_tasks._slot_from_payload(payload) == "slot-x"
    assert scheduled_tasks._slot_from_payload({"h5_context": {}}) == ""
    assert scheduled_tasks._slot_from_payload({"h5_context": {}}, "slot-fallback") == "slot-fallback"


def test_empty_account_row_falls_back_to_row_with_digital_human(db_session, test_user):
    """账号级默认行是空壳时，不能让它盖掉用户在设备槽位里选好的数字人配置。"""
    from backend.app.api import ip_content_studio as studio
    from backend.app.models import IPContentScheduleTemplate

    name = studio._PERSONAL_DEFAULT_TEMPLATE_NAME
    db_session.add(
        IPContentScheduleTemplate(
            user_id=test_user.id,
            installation_id="",
            name=name,
            requirements={},
            meta={"is_personal_default": True, "digital_human_resources": {"avatars": [], "voices": []}},
            status="active",
        )
    )
    db_session.add(
        IPContentScheduleTemplate(
            user_id=test_user.id,
            installation_id="slot-with-dh",
            name=name,
            requirements={},
            meta={
                "is_personal_default": True,
                "digital_human_template": {"scene": "realMan", "style_id": "style-1"},
                "digital_human_resources": {"avatars": [{"id": "a1"}], "voices": [{"id": "v1"}]},
            },
            status="active",
        )
    )
    db_session.commit()

    # 另一个槽位没存过自己的行 → 只返回 None，绝不借用别的槽位的数字人配置
    row = studio._personal_default_row_for_slot(db_session, test_user.id, "slot-unknown")
    assert row is None

    # 就算其它槽位的行被删掉，未知槽位也依然是 None（不存在任何回落）
    db_session.query(IPContentScheduleTemplate).filter(
        IPContentScheduleTemplate.installation_id == "slot-with-dh"
    ).update({IPContentScheduleTemplate.status: "deleted"})
    db_session.commit()
    assert studio._personal_default_row_for_slot(db_session, test_user.id, "slot-unknown") is None
    # 账号级调用仍然拿账号级行
    assert studio._personal_default_row_for_slot(db_session, test_user.id, "").installation_id == ""


def test_slot_row_without_persona_does_not_borrow_the_account_persona(db_session, test_user):
    """槽位行没有人设时**不能**回落到账号级人设：宁可拦下启动，也不要拿错的资料生成。"""
    from backend.app.api import scheduled_tasks
    from backend.app.models import IPContentScheduleTemplate

    account_row = IPContentScheduleTemplate(
        user_id=test_user.id,
        name="个人默认配置",
        installation_id="",
        status="active",
        requirements={"basic_profile": {"profile_name": "账号人设"}},
        keyword_ids=[1],
        meta={"source": "test"},
    )
    slot_row = IPContentScheduleTemplate(
        user_id=test_user.id,
        name="个人默认配置",
        installation_id="slot-empty-persona",
        status="active",
        requirements={},
        keyword_ids=[1, 2, 3],
        meta={"source": "test"},
    )
    db_session.add_all([account_row, slot_row])
    db_session.commit()

    context = scheduled_tasks._h5_dh_context_params(db_session, test_user.id, "slot-empty-persona")
    # 不再借用账号级人设（避免"模板没配人设，却拿旧默认人设生成"）
    assert not (context.get("requirements") or {}).get("basic_profile")
    # 资源仍然以槽位行为准
    assert context["keyword_ids"] == [1, 2, 3]

    # 槽位行自己有人设时，不回落到账号级
    slot_row.requirements = {"basic_profile": {"profile_name": "槽位人设"}}
    db_session.commit()
    context2 = scheduled_tasks._h5_dh_context_params(db_session, test_user.id, "slot-empty-persona")
    assert context2["requirements"]["basic_profile"]["profile_name"] == "槽位人设"


def test_saving_without_profile_fields_keeps_the_device_persona(db_session, test_user):
    """切模板/加关键词这类保存不带人设字段时，不能把设备行的人设抹成空壳。"""
    from backend.app.api import ip_content_studio as studio
    from backend.app.models import IPContentScheduleTemplate

    db_session.add(
        IPContentScheduleTemplate(
            user_id=test_user.id,
            name=studio._PERSONAL_DEFAULT_TEMPLATE_NAME,
            installation_id="slot-keep-profile",
            status="active",
            requirements={"basic_profile": {"name": "张三", "current_city": "深圳"}},
            meta={"source": "test"},
        )
    )
    db_session.commit()

    # 模拟客户端"切模板"时的请求：只带模板公共字段，人设字段被剥掉
    studio.save_personal_default_ip_content_config(
        studio.ScheduleTemplateBody(
            requirements={"common": "模板公共字段"},
            meta={"source": "personal_settings_current_template"},
        ),
        x_installation_id="slot-keep-profile",
        current_user=test_user,
        db=db_session,
    )

    row = (
        db_session.query(IPContentScheduleTemplate)
        .filter(
            IPContentScheduleTemplate.user_id == test_user.id,
            IPContentScheduleTemplate.installation_id == "slot-keep-profile",
        )
        .one()
    )
    assert row.requirements.get("basic_profile", {}).get("name") == "张三"
    assert row.requirements.get("common") == "模板公共字段"


def _add_memory_doc(db, user_id: int, doc_id: str, title: str = "口播稿") -> None:
    from backend.app.models import OpenClawMemoryDocument

    db.add(
        OpenClawMemoryDocument(
            doc_id=doc_id,
            target_user_id=user_id,
            installation_id="",
            origin="user",
            title=title,
            filename=title + ".md",
            content_text="正文内容",
            status="active",
        )
    )
    db.commit()


def _add_slot_row(db, user_id: int, slot: str, *, meta: dict, memory_doc_ids=None, keyword_ids=None, competitor_ids=None):
    from backend.app.api import ip_content_studio as studio
    from backend.app.models import IPContentScheduleTemplate

    row = IPContentScheduleTemplate(
        user_id=user_id,
        name=studio._PERSONAL_DEFAULT_TEMPLATE_NAME,
        installation_id=slot,
        status="active",
        requirements={},
        memory_doc_ids=list(memory_doc_ids or []),
        keyword_ids=list(keyword_ids or []),
        competitor_ids=list(competitor_ids or []),
        meta=dict(meta),
    )
    db.add(row)
    db.commit()
    return row


def _add_template_row(db, user_id: int, name: str, *, memory_doc_ids=None, keyword_ids=None, competitor_ids=None):
    from backend.app.models import IPContentScheduleTemplate

    row = IPContentScheduleTemplate(
        user_id=user_id,
        name=name,
        installation_id="",
        status="active",
        memory_doc_ids=list(memory_doc_ids or []),
        keyword_ids=list(keyword_ids or []),
        competitor_ids=list(competitor_ids or []),
        meta={"source": "test"},
    )
    db.add(row)
    db.commit()
    return row


def test_empty_selection_is_not_an_explicit_override(db_session, test_user):
    """口径（2026-10-16）：空 = "这项没定制"，不是"永久不要"。

    设备行“记忆文件”为空、但带 template_resource_overrides.memory_doc_ids=true 的历史脏数据，
    也必须回落用户自己点过「设为当前」的那个模板 —— 否则一次误存就把模板资源永久锁死
    （线上 109/142/210 的"数字人生成缺少：当前启用模板的记忆文件"就是这么来的）。
    """
    from backend.app.api import ip_content_studio as studio

    _add_memory_doc(db_session, test_user.id, "doc-mem-1")
    template = _add_template_row(db_session, test_user.id, "Tengwin", memory_doc_ids=["doc-mem-1"], keyword_ids=[11])
    slot = _add_slot_row(
        db_session, test_user.id, "slot-empty-mem",
        meta={
            "source": "test",
            "current_template_id": template.id,
            "template_resource_overrides": {"keyword_ids": False, "competitor_ids": False, "memory_doc_ids": True},
        },
    )

    overrides = studio._personal_default_resource_overrides(slot, template)
    assert overrides == {"keyword_ids": False, "competitor_ids": False, "memory_doc_ids": False}

    payload = studio._personal_default_template_payload_with_resources(db_session, slot)
    assert payload["memory_doc_ids"] == ["doc-mem-1"], "空值不该锁死：要跟随当前模板"


def test_legacy_empty_row_follows_the_current_template(db_session, test_user):
    """老数据（没有 marker）：设备行为空时同样跟随当前模板，而不是判成"差异=自定义"。"""
    from backend.app.api import ip_content_studio as studio

    _add_memory_doc(db_session, test_user.id, "doc-legacy-1")
    template = _add_template_row(db_session, test_user.id, "老模板", memory_doc_ids=["doc-legacy-1"], keyword_ids=[21])
    slot = _add_slot_row(
        db_session, test_user.id, "slot-legacy",
        meta={"source": "test", "current_template_id": template.id},
    )

    overrides = studio._personal_default_resource_overrides(slot, template)
    assert overrides == {"keyword_ids": False, "competitor_ids": False, "memory_doc_ids": False}
    payload = studio._personal_default_template_payload_with_resources(db_session, slot)
    assert payload["memory_doc_ids"] == ["doc-legacy-1"]


def test_non_empty_difference_is_still_a_user_override(db_session, test_user):
    """用户真的自己选过（非空且与模板不同）时，仍然保留设备行上的选择。"""
    from backend.app.api import ip_content_studio as studio

    _add_memory_doc(db_session, test_user.id, "doc-mine")
    _add_memory_doc(db_session, test_user.id, "doc-tpl")
    template = _add_template_row(db_session, test_user.id, "模板", memory_doc_ids=["doc-tpl"])
    slot = _add_slot_row(
        db_session, test_user.id, "slot-custom",
        meta={"source": "test", "current_template_id": template.id},
        memory_doc_ids=["doc-mine"],
    )

    overrides = studio._personal_default_resource_overrides(slot, template)
    assert overrides["memory_doc_ids"] is True, "非空且不同 = 用户自定义，要保留"
    payload = studio._personal_default_template_payload_with_resources(db_session, slot)
    assert payload["memory_doc_ids"] == ["doc-mine"]


def test_empty_selection_survives_a_later_template_change(db_session, test_user):
    """点了「设为当前」之后改模板数据要能生效：设备行为空时跟着模板变。"""
    from backend.app.api import ip_content_studio as studio

    _add_memory_doc(db_session, test_user.id, "doc-v1")
    _add_memory_doc(db_session, test_user.id, "doc-v2")
    template = _add_template_row(db_session, test_user.id, "会变的模板", memory_doc_ids=["doc-v1"])
    slot = _add_slot_row(
        db_session, test_user.id, "slot-live",
        meta={
            "source": "test",
            "current_template_id": template.id,
            "template_resource_overrides": {"memory_doc_ids": True},
        },
    )
    assert studio._personal_default_template_payload_with_resources(db_session, slot)["memory_doc_ids"] == ["doc-v1"]

    template.memory_doc_ids = ["doc-v2"]
    db_session.commit()
    assert studio._personal_default_template_payload_with_resources(db_session, slot)["memory_doc_ids"] == ["doc-v2"]