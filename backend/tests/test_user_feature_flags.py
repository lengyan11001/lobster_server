from backend.app.services.user_feature_flags import (
    AI_SECRETARY_ENTRY_ID,
    AI_MARKETING_ENTRY_ID,
    BIHUO_25_VIDEO_SKILL_ID,
    HOME_AI_CHAT_ENTRY_ID,
    MY_AI_EMPLOYEES_ENTRY_ID,
    MULTI_CLIP_MIXER_SKILL_ID,
    OPENAI_OFFICIAL_IMAGE_CHANNEL_ACCESS_KEY,
    OPENAI_OFFICIAL_IMAGE_CHANNEL_FEATURE_ID,
    user_feature_flags,
)


def test_ai_secretary_is_disabled_for_new_users_until_granted(db_session, test_user):
    from backend.app.models import UserSkillVisibility

    flags = user_feature_flags(db_session, test_user.id)
    assert flags[AI_SECRETARY_ENTRY_ID] is False

    db_session.add(UserSkillVisibility(user_id=test_user.id, package_id=AI_SECRETARY_ENTRY_ID))
    db_session.commit()

    flags = user_feature_flags(db_session, test_user.id)
    assert flags[AI_SECRETARY_ENTRY_ID] is True


def test_new_user_default_entry_groups_and_retired_permissions(db_session, test_user):
    flags = user_feature_flags(db_session, test_user.id)
    assert flags[HOME_AI_CHAT_ENTRY_ID] is True
    assert flags[MY_AI_EMPLOYEES_ENTRY_ID] is True
    assert flags[AI_MARKETING_ENTRY_ID] is True
    assert flags["skill_store_entry"] is True
    assert flags["publish_center_entry"] is True
    assert flags["production_records_entry"] is False
    assert flags["openclaw_weixin_channel"] is False
    assert flags["browser_use_skill"] is False
    assert flags["computer_use_skill"] is False
    assert flags["media_edit_skill"] is False


def test_openai_official_image_channel_flag_uses_user_skill_visibility(db_session, test_user):
    from backend.app.models import UserSkillVisibility

    flags = user_feature_flags(db_session, test_user.id)
    assert flags[OPENAI_OFFICIAL_IMAGE_CHANNEL_ACCESS_KEY] is False
    assert flags[OPENAI_OFFICIAL_IMAGE_CHANNEL_FEATURE_ID] is False

    db_session.add(
        UserSkillVisibility(
            user_id=test_user.id,
            package_id=OPENAI_OFFICIAL_IMAGE_CHANNEL_FEATURE_ID,
        )
    )
    db_session.commit()

    flags = user_feature_flags(db_session, test_user.id)
    assert flags[OPENAI_OFFICIAL_IMAGE_CHANNEL_ACCESS_KEY] is True
    assert flags[OPENAI_OFFICIAL_IMAGE_CHANNEL_FEATURE_ID] is True


def test_bihuo_25_flag_is_absent_until_server_grants_visibility(db_session, test_user):
    from backend.app.models import UserSkillVisibility

    flags = user_feature_flags(db_session, test_user.id)
    assert flags[BIHUO_25_VIDEO_SKILL_ID] is False

    db_session.add(
        UserSkillVisibility(
            user_id=test_user.id,
            package_id=BIHUO_25_VIDEO_SKILL_ID,
        )
    )
    db_session.commit()

    flags = user_feature_flags(db_session, test_user.id)
    assert flags[BIHUO_25_VIDEO_SKILL_ID] is True


def test_multi_clip_mixer_flag_follows_server_visibility(db_session, test_user):
    from backend.app.models import UserSkillVisibility

    # New users receive only the configured default entry groups.
    flags = user_feature_flags(db_session, test_user.id)
    assert flags[MULTI_CLIP_MIXER_SKILL_ID] is True

    db_session.add(UserSkillVisibility(user_id=test_user.id, package_id="custom_permission_marker"))
    db_session.commit()
    flags = user_feature_flags(db_session, test_user.id)
    assert flags[MULTI_CLIP_MIXER_SKILL_ID] is False

    db_session.add(
        UserSkillVisibility(
            user_id=test_user.id,
            package_id=MULTI_CLIP_MIXER_SKILL_ID,
        )
    )
    db_session.commit()

    flags = user_feature_flags(db_session, test_user.id)
    assert flags[MULTI_CLIP_MIXER_SKILL_ID] is True
