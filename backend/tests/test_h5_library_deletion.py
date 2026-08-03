from backend.app.api.hifly_assets import delete_my_avatar, delete_my_voice
from backend.app.models import UserHiflyAvatarAsset, UserHiflyVoiceAsset


def test_failed_digital_human_assets_can_be_deleted(db_session, test_user):
    avatar = UserHiflyAvatarAsset(
        user_id=test_user.id,
        title="失败形象",
        status="failed",
        hifly_task_id="failed-avatar-delete-001",
        error_message="training failed",
    )
    voice = UserHiflyVoiceAsset(
        user_id=test_user.id,
        title="失败声音",
        status="failed",
        hifly_task_id="failed-voice-delete-001",
        error_message="training failed",
    )
    db_session.add_all([avatar, voice])
    db_session.commit()
    db_session.refresh(avatar)
    db_session.refresh(voice)

    avatar_result = delete_my_avatar(avatar.id, current_user=test_user, db=db_session)
    voice_result = delete_my_voice(voice.id, current_user=test_user, db=db_session)

    assert avatar_result["ok"] is True
    assert voice_result["ok"] is True
    db_session.refresh(avatar)
    db_session.refresh(voice)
    assert avatar.status == "deleted"
    assert voice.status == "deleted"
