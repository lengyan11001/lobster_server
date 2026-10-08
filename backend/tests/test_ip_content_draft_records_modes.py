from __future__ import annotations


def _seed(db_session, test_user_id: int) -> None:
    from backend.app.models import IPContentDraftRecord

    db_session.add_all(
        [
            IPContentDraftRecord(
                record_id="oral-record-1",
                user_id=test_user_id,
                task="professional_ip_oral",
                title="IP口播",
                content="口播正文",
            ),
            IPContentDraftRecord(
                record_id="oral-record-2",
                user_id=test_user_id,
                task="industry_hot_oral",
                title="行业口播",
                content="行业口播正文",
            ),
            IPContentDraftRecord(
                record_id="moments-record-1",
                user_id=test_user_id,
                task="moments_candidate",
                title="朋友圈",
                content="朋友圈文案",
            ),
        ]
    )
    db_session.commit()


def _ids(db_session, test_user, **kwargs) -> set[str]:
    from backend.app.api import ip_content_studio as studio

    payload = studio.list_draft_records(
        limit=80,
        offset=0,
        current_user=test_user,
        db=db_session,
        **kwargs,
    )
    return {item["record_id"] for item in payload["items"]}


def test_oral_mode_lists_only_oral_drafts(db_session, test_user):
    _seed(db_session, test_user.id)

    assert _ids(db_session, test_user, task="", mode="oral") == {"oral-record-1", "oral-record-2"}


def test_moments_mode_lists_only_moments_drafts(db_session, test_user):
    _seed(db_session, test_user.id)

    assert _ids(db_session, test_user, task="", mode="moments") == {"moments-record-1"}


def test_without_mode_the_legacy_entry_still_lists_everything(db_session, test_user):
    _seed(db_session, test_user.id)

    assert _ids(db_session, test_user, task="", mode="") == {
        "oral-record-1",
        "oral-record-2",
        "moments-record-1",
    }


def test_explicit_task_filter_wins_over_mode(db_session, test_user):
    _seed(db_session, test_user.id)

    assert _ids(db_session, test_user, task="professional_ip_oral", mode="moments") == {"oral-record-1"}
