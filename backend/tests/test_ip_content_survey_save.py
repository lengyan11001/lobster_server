from backend.app.api.ip_content_studio import (
    _PERSONAL_DEFAULT_TEMPLATE_NAME,
    ScheduleTemplateBody,
    save_personal_default_ip_content_config,
)
from backend.app.models import IPContentProfileSurvey, IPContentScheduleTemplate


def _survey(user_id, name, status):
    return IPContentProfileSurvey(
        user_id=user_id,
        name=name,
        status=status,
        requirements={"basic_profile": {"name": name}},
        meta={"source": "test"},
    )


def test_profile_save_repairs_template_that_points_to_deleted_survey(db_session, test_user):
    stale = _survey(test_user.id, "旧资料", "deleted")
    fresh = _survey(test_user.id, "新资料", "active")
    template = IPContentScheduleTemplate(
        user_id=test_user.id,
        installation_id="",
        name="当前模板",
        status="active",
        requirements={"basic_profile": {"name": "旧资料", "industry": "火锅"}},
        meta={},
    )
    db_session.add_all([stale, fresh, template])
    db_session.commit()
    template.survey_id = stale.id
    db_session.commit()

    body = ScheduleTemplateBody(
        name=_PERSONAL_DEFAULT_TEMPLATE_NAME,
        keyword_ids=[],
        competitor_ids=[],
        memory_doc_ids=[],
        memory_docs=[],
        survey_id=fresh.id,
        requirements={"basic_profile": {"name": "新资料"}},
        meta={
            "source": "online_personal_profile",
            "current_template_id": template.id,
            "resource_selection_complete": True,
        },
    )

    result = save_personal_default_ip_content_config(
        body,
        x_installation_id="",
        current_user=test_user,
        db=db_session,
    )

    assert result["ok"] is True
    row = (
        db_session.query(IPContentScheduleTemplate)
        .filter(
            IPContentScheduleTemplate.user_id == test_user.id,
            IPContentScheduleTemplate.name == _PERSONAL_DEFAULT_TEMPLATE_NAME,
            IPContentScheduleTemplate.installation_id == "",
        )
        .one()
    )
    assert row.survey_id == fresh.id
    assert int(row.meta["current_template_id"]) == int(template.id)
    db_session.refresh(template)
    assert template.survey_id == fresh.id


def test_profile_save_clears_dangling_template_survey_instead_of_400(db_session, test_user):
    stale = _survey(test_user.id, "已删除资料", "deleted")
    template = IPContentScheduleTemplate(
        user_id=test_user.id,
        installation_id="",
        name="当前模板",
        status="active",
        requirements={"basic_profile": {"name": "旧资料"}},
        meta={},
    )
    db_session.add_all([stale, template])
    db_session.commit()
    template.survey_id = stale.id
    db_session.commit()

    body = ScheduleTemplateBody(
        name=_PERSONAL_DEFAULT_TEMPLATE_NAME,
        survey_id=stale.id,
        requirements={"basic_profile": {"name": "旧资料"}},
        meta={
            "source": "personal_settings",
            "current_template_id": template.id,
            "resource_selection_complete": True,
        },
    )

    result = save_personal_default_ip_content_config(
        body,
        x_installation_id="",
        current_user=test_user,
        db=db_session,
    )

    assert result["ok"] is True
    row = (
        db_session.query(IPContentScheduleTemplate)
        .filter(
            IPContentScheduleTemplate.user_id == test_user.id,
            IPContentScheduleTemplate.name == _PERSONAL_DEFAULT_TEMPLATE_NAME,
            IPContentScheduleTemplate.installation_id == "",
        )
        .one()
    )
    assert row.survey_id is None
    db_session.refresh(template)
    assert template.survey_id is None
