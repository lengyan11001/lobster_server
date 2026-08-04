from backend.app.services.user_feature_flags import (
    BIHUO_25_VIDEO_SKILL_ID,
    MULTI_CLIP_MIXER_SKILL_ID,
    OPENAI_OFFICIAL_IMAGE_CHANNEL_ACCESS_KEY,
    OPENAI_OFFICIAL_IMAGE_CHANNEL_FEATURE_ID,
    user_feature_flags,
)


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

    # Users without a custom permission set receive the domestic defaults.
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
