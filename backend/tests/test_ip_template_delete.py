from backend.app.api.ip_content_studio import delete_schedule_template
from backend.app.models import H5AgentTemplateGrant, IPContentScheduleTemplate


def test_delete_template_clears_default_reference_and_active_grants(db_session, test_user, other_user):
    template = IPContentScheduleTemplate(
        user_id=test_user.id,
        name="待删除模板",
        status="active",
    )
    db_session.add(template)
    db_session.flush()
    personal_default = IPContentScheduleTemplate(
        user_id=test_user.id,
        name="个人默认配置",
        status="active",
        meta={
            "current_template_id": template.id,
            "current_template_name": template.name,
            "keep": "value",
        },
    )
    grant = H5AgentTemplateGrant(
        template_id=template.id,
        owner_user_id=test_user.id,
        target_user_id=other_user.id,
        status="active",
    )
    db_session.add_all([personal_default, grant])
    db_session.commit()

    result = delete_schedule_template(template.id, current_user=test_user, db=db_session)

    db_session.expire_all()
    assert result == {"ok": True}
    assert db_session.get(IPContentScheduleTemplate, template.id).status == "deleted"
    refreshed_default = db_session.get(IPContentScheduleTemplate, personal_default.id)
    assert refreshed_default.meta == {"keep": "value"}
    assert db_session.get(H5AgentTemplateGrant, grant.id).status == "deleted"
