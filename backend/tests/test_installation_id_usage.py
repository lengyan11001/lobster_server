"""槽位占用判定：历史记录/同一台机器的其它账号不能算“被占用”。"""

from datetime import datetime, timedelta

from backend.app.api.settings_api import _installation_id_usage
from backend.app.models import H5ChatDevicePresence, User, UserInstallation, UserMachineIdentity


def _user(db_session, email):
    row = User(email=email, hashed_password="x")
    db_session.add(row)
    db_session.flush()
    return row


def test_historical_slot_rows_do_not_flag_duplicate(db_session, test_user):
    other = _user(db_session, "other@test.local")
    db_session.add(
        UserInstallation(
            user_id=other.id,
            installation_id="slot-abc12345",
            last_seen_at=datetime.utcnow() - timedelta(days=2),
        )
    )
    db_session.commit()

    usage = _installation_id_usage(
        db_session, "slot-abc12345", exclude_user_id=test_user.id
    )

    assert usage["live_taken"] is False
    assert usage["taken"] is True  # 历史口径仍算“其它账号用过”，/bind 签名要用
    assert usage["presence_user_ids"] == []


def test_recent_presence_from_other_account_flags_duplicate(db_session, test_user):
    other = _user(db_session, "other2@test.local")
    db_session.add(
        H5ChatDevicePresence(
            user_id=other.id,
            installation_id="slot-abc12345",
            last_seen_at=datetime.utcnow() - timedelta(minutes=1),
        )
    )
    db_session.commit()

    usage = _installation_id_usage(
        db_session, "slot-abc12345", exclude_user_id=test_user.id
    )

    assert usage["live_taken"] is True
    assert usage["presence_user_ids"] == [other.id]


def test_stale_presence_is_not_online_conflict(db_session, test_user):
    other = _user(db_session, "other3@test.local")
    db_session.add(
        H5ChatDevicePresence(
            user_id=other.id,
            installation_id="slot-abc12345",
            last_seen_at=datetime.utcnow() - timedelta(minutes=30),
        )
    )
    db_session.commit()

    usage = _installation_id_usage(
        db_session, "slot-abc12345", exclude_user_id=test_user.id
    )

    assert usage["live_taken"] is False


def test_same_machine_other_account_is_not_a_conflict(db_session, test_user):
    other = _user(db_session, "other4@test.local")
    db_session.add_all(
        [
            UserMachineIdentity(
                user_id=test_user.id,
                machine_instance_id="machine-same",
                installation_id="slot-abc12345",
            ),
            UserMachineIdentity(
                user_id=other.id,
                machine_instance_id="machine-same",
                installation_id="slot-abc12345",
            ),
            H5ChatDevicePresence(
                user_id=other.id,
                installation_id="slot-abc12345",
                last_seen_at=datetime.utcnow(),
            ),
        ]
    )
    db_session.commit()

    usage = _installation_id_usage(
        db_session, "slot-abc12345", exclude_user_id=test_user.id
    )

    assert usage["live_taken"] is False
    assert usage["presence_user_ids"] == []


def test_different_machine_recent_presence_is_a_conflict(db_session, test_user):
    other = _user(db_session, "other5@test.local")
    db_session.add_all(
        [
            UserMachineIdentity(
                user_id=test_user.id,
                machine_instance_id="machine-a",
                installation_id="slot-abc12345",
            ),
            UserMachineIdentity(
                user_id=other.id,
                machine_instance_id="machine-b",
                installation_id="slot-abc12345",
            ),
            H5ChatDevicePresence(
                user_id=other.id,
                installation_id="slot-abc12345",
                last_seen_at=datetime.utcnow(),
            ),
        ]
    )
    db_session.commit()

    usage = _installation_id_usage(
        db_session, "slot-abc12345", exclude_user_id=test_user.id
    )

    assert usage["live_taken"] is True
