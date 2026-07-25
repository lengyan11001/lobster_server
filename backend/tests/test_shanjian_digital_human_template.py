from backend.app.api.shanjian_digital_human import (
    CreateVideoBody,
    _resolve_video_template_meta,
    _stored_digital_human_template,
)
from backend.app.models import IPContentScheduleTemplate


def test_active_personal_template_supplies_digital_human_editing_template(db_session, test_user):
    current = IPContentScheduleTemplate(
        user_id=test_user.id,
        name="销售内容模板",
        status="active",
        meta={
            "digital_human_template": {
                "scene": "realMan",
                "style_id": "style-current",
                "name": "当前剪辑模板",
                "cover_url": "https://example.test/current.jpg",
            }
        },
    )
    db_session.add(current)
    db_session.flush()
    personal = IPContentScheduleTemplate(
        user_id=test_user.id,
        name="个人默认配置",
        status="active",
        meta={
            "current_template_id": current.id,
            "digital_human_template": {"style_id": "style-stale"},
        },
    )
    db_session.add(personal)
    db_session.commit()

    template_meta, source = _resolve_video_template_meta(db_session, test_user.id, CreateVideoBody())

    assert source == "active_personal_template"
    assert template_meta is not None
    assert template_meta["style_id"] == "style-current"
    assert template_meta["template_scene"] == "realMan"


def test_explicit_video_template_overrides_active_personal_template(db_session, test_user):
    db_session.add(
        IPContentScheduleTemplate(
            user_id=test_user.id,
            name="个人默认配置",
            status="active",
            meta={"digital_human_template": {"style_id": "style-default"}},
        )
    )
    db_session.commit()

    body = CreateVideoBody(style_id="style-request", template_scene="realMan")
    template_meta, source = _resolve_video_template_meta(db_session, test_user.id, body)

    assert source == "request"
    assert template_meta is not None
    assert template_meta["style_id"] == "style-request"


def test_current_template_can_explicitly_disable_automatic_editing(db_session, test_user):
    current = IPContentScheduleTemplate(
        user_id=test_user.id,
        name="不剪辑模板",
        status="active",
        meta={"digital_human_template": None},
    )
    db_session.add(current)
    db_session.flush()
    db_session.add(
        IPContentScheduleTemplate(
            user_id=test_user.id,
            name="个人默认配置",
            status="active",
            meta={
                "current_template_id": current.id,
                "digital_human_template": {"style_id": "style-stale"},
            },
        )
    )
    db_session.commit()

    template_meta, source = _resolve_video_template_meta(db_session, test_user.id, CreateVideoBody())

    assert template_meta is None
    assert source == ""


def test_stored_template_rules_are_normalized_without_trusting_bad_duration():
    configured, template_meta = _stored_digital_human_template(
        {
            "digital_human_template": {
                "id": "style-1",
                "video_duration": "invalid",
                "pack_rules": {"subtitleSwitch": False},
                "process_rules": {"watermarkShow": True},
            }
        }
    )

    assert configured is True
    assert template_meta is not None
    assert template_meta["style_id"] == "style-1"
    assert template_meta["video_duration"] == 30
    assert template_meta["subtitle_switch"] is False
    assert template_meta["watermark_show"] is True
