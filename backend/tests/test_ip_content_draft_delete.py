import pytest
from fastapi import HTTPException

from backend.app.api.ip_content_studio import delete_draft_record, delete_draft_record_group
from backend.app.models import IPContentDraftRecord


def _record(*, user_id: int, record_id: str, group_id: str, task: str = "industry_hot_oral"):
    return IPContentDraftRecord(
        record_id=record_id,
        user_id=user_id,
        task=task,
        platform="douyin",
        title=record_id,
        content="test content",
        meta={"group_id": group_id, "generation_status": "failed"},
    )


def test_delete_ip_content_draft_record_is_user_scoped(db_session, test_user, other_user):
    db_session.add_all(
        [
            _record(user_id=test_user.id, record_id="mine-001", group_id="group-delete-001"),
            _record(user_id=other_user.id, record_id="other-001", group_id="group-delete-001"),
        ]
    )
    db_session.commit()

    result = delete_draft_record(record_id="mine-001", current_user=test_user, db=db_session)

    assert result == {"ok": True, "deleted": 1, "record_id": "mine-001"}
    assert db_session.query(IPContentDraftRecord).filter_by(record_id="mine-001").first() is None
    assert db_session.query(IPContentDraftRecord).filter_by(record_id="other-001").first() is not None


def test_delete_ip_content_draft_group_removes_every_status_for_current_user(db_session, test_user, other_user):
    db_session.add_all(
        [
            _record(user_id=test_user.id, record_id="mine-101", group_id="group-delete-101"),
            _record(user_id=test_user.id, record_id="mine-102", group_id="group-delete-101", task="professional_ip_oral"),
            _record(user_id=other_user.id, record_id="other-101", group_id="group-delete-101"),
        ]
    )
    db_session.commit()

    result = delete_draft_record_group(group_id="group-delete-101", current_user=test_user, db=db_session)

    assert result == {"ok": True, "deleted": 2, "group_id": "group-delete-101"}
    assert db_session.query(IPContentDraftRecord).filter_by(user_id=test_user.id).count() == 0
    assert db_session.query(IPContentDraftRecord).filter_by(user_id=other_user.id).count() == 1


def test_delete_ip_content_draft_record_rejects_another_user(db_session, test_user, other_user):
    db_session.add(_record(user_id=other_user.id, record_id="private-001", group_id="group-private-001"))
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        delete_draft_record(record_id="private-001", current_user=test_user, db=db_session)

    assert exc_info.value.status_code == 404
