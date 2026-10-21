"""系统设置-槽位模板关联：槽位乱了之后用户能手动把模板重新挂回来。"""

import pytest
from fastapi import HTTPException

from backend.app.api.ip_content_studio import (
    SlotTemplateLinkBody,
    link_slot_template,
    list_slot_templates,
)
from backend.app.models import IPContentScheduleTemplate


def _template(user_id, name, slot=""):
    return IPContentScheduleTemplate(
        user_id=user_id,
        installation_id=slot,
        name=name,
        status="active",
        meta={"source": "test"},
    )


def test_slot_template_list_reports_items_and_current(db_session, test_user):
    db_session.add_all(
        [
            _template(test_user.id, "阿迪人设"),
            _template(test_user.id, "诺诺人设", "u9-other"),
        ]
    )
    db_session.commit()

    result = list_slot_templates(x_installation_id="u1-abc", current_user=test_user, db=db_session)

    names = {item["name"] for item in result["items"]}
    assert {"阿迪人设", "诺诺人设"} <= names
    assert result["current_template_id"] is None
    assert result["installation_id"] == "u1-abc"


def test_link_by_name_binds_current_slot(db_session, test_user):
    target = _template(test_user.id, "阿迪人设")
    db_session.add(target)
    db_session.commit()

    result = link_slot_template(
        SlotTemplateLinkBody(template_name="阿迪人设"),
        x_installation_id="u1-abc",
        current_user=test_user,
        db=db_session,
    )

    assert result["template_id"] == target.id
    row = (
        db_session.query(IPContentScheduleTemplate)
        .filter(
            IPContentScheduleTemplate.user_id == test_user.id,
            IPContentScheduleTemplate.installation_id == "u1-abc",
        )
        .one()
    )
    assert row.name == "个人默认配置"
    assert int(row.meta["current_template_id"]) == int(target.id)
    assert row.meta["current_template_link_source"] == "manual_slot_link"

    listed = list_slot_templates(x_installation_id="u1-abc", current_user=test_user, db=db_session)
    assert listed["current_template_id"] == target.id


def test_link_by_partial_name_only_when_unique(db_session, test_user):
    db_session.add_all([_template(test_user.id, "阿迪人设"), _template(test_user.id, "诺诺人设")])
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        link_slot_template(
            SlotTemplateLinkBody(template_name="人设"),
            x_installation_id="u1-abc",
            current_user=test_user,
            db=db_session,
        )
    assert exc_info.value.status_code == 400

    result = link_slot_template(
        SlotTemplateLinkBody(template_name="阿迪"),
        x_installation_id="u1-abc",
        current_user=test_user,
        db=db_session,
    )
    assert result["template_name"] == "阿迪人设"


def test_link_unknown_name_is_rejected(db_session, test_user):
    with pytest.raises(HTTPException) as exc_info:
        link_slot_template(
            SlotTemplateLinkBody(template_name="不存在的模板"),
            x_installation_id="u1-abc",
            current_user=test_user,
            db=db_session,
        )
    assert exc_info.value.status_code == 404
