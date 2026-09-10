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


def test_new_device_falls_back_to_account_level_row(db_session, test_user):
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

    # A brand new device still sees the shared default ...
    payload = studio.get_personal_default_ip_content_config(
        x_installation_id="slot-new-0003",
        current_user=test_user,
        db=db_session,
    )
    assert payload["item"]["requirements"]["common"] == "账号默认"

    # ... and saving on that device must not rewrite the shared row.
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
    # Unknown slots and slot-less callers keep the account-level behaviour.
    assert studio._personal_default_row_for_slot(db_session, test_user.id, "slot-y").requirements["profile_name"] == "账号"
    assert studio._personal_default_row_for_slot(db_session, test_user.id, "").requirements["profile_name"] == "账号"


def test_task_payload_slot_is_used_by_run_payload(db_session, test_user):
    from backend.app.api import scheduled_tasks

    payload = {"h5_context": {"installation_id": "slot-x"}}
    assert scheduled_tasks._slot_from_payload(payload) == "slot-x"
    assert scheduled_tasks._slot_from_payload({"h5_context": {}}) == ""
    assert scheduled_tasks._slot_from_payload({"h5_context": {}}, "slot-fallback") == "slot-fallback"
