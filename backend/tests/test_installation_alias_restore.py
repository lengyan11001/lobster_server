"""设备别名 → 原槽位：槽位乱了之后按别名找回原来那套数据。"""

from datetime import datetime

import pytest
from fastapi import HTTPException

from backend.app.api.settings_api import (
    AliasRestoreRequest,
    list_installation_aliases,
    restore_installation_alias,
)
from backend.app.models import H5ChatDevicePresence, User, UserDeviceLabel, UserMachineIdentity


def _user(db_session, email):
    row = User(email=email, hashed_password="x")
    db_session.add(row)
    db_session.flush()
    return row


def _label(db_session, user_id, alias, slot, updated_at=None):
    row = UserDeviceLabel(
        user_id=user_id,
        label_key=f"slot:{slot}",
        machine_instance_id=f"m-{slot}",
        display_name=alias,
        last_installation_id=slot,
        source="manual",
        updated_at=updated_at or datetime.utcnow(),
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_alias_list_returns_alias_and_slot(db_session, test_user):
    _label(db_session, test_user.id, "kaifaji", "u31-f55a8e813d428cd055e7e5eba0bc2aab")
    _label(db_session, test_user.id, "local-online", "u31-another-slot")
    db_session.commit()

    result = list_installation_aliases(current_user=test_user, db=db_session)

    aliases = {item["alias"]: item for item in result["items"]}
    assert "kaifaji" in aliases
    assert aliases["kaifaji"]["installation_id"] == "u31-f55a8e813d428cd055e7e5eba0bc2aab"
    assert aliases["kaifaji"]["online"] is False
    assert "local-online" not in aliases


def test_restore_alias_returns_original_slot(db_session, test_user):
    _label(db_session, test_user.id, "kaifaji", "u31-f55a8e813d428cd055e7e5eba0bc2aab")
    db_session.commit()

    result = restore_installation_alias(
        AliasRestoreRequest(alias="kaifaji"), current_user=test_user, db=db_session
    )

    assert result["installation_id"] == "u31-f55a8e813d428cd055e7e5eba0bc2aab"


def test_restore_unknown_alias_is_rejected(db_session, test_user):
    with pytest.raises(HTTPException) as exc_info:
        restore_installation_alias(
            AliasRestoreRequest(alias="不存在"), current_user=test_user, db=db_session
        )
    assert exc_info.value.status_code == 404


def test_restore_refuses_slot_used_live_by_another_machine(db_session, test_user):
    other = _user(db_session, "other-alias@test.local")
    slot = "u31-f55a8e813d428cd055e7e5eba0bc2aab"
    _label(db_session, test_user.id, "kaifaji", slot)
    db_session.add_all(
        [
            UserMachineIdentity(
                user_id=test_user.id, machine_instance_id="machine-a", installation_id=slot
            ),
            UserMachineIdentity(
                user_id=other.id, machine_instance_id="machine-b", installation_id=slot
            ),
            H5ChatDevicePresence(
                user_id=other.id, installation_id=slot, last_seen_at=datetime.utcnow()
            ),
        ]
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        restore_installation_alias(
            AliasRestoreRequest(alias="kaifaji"), current_user=test_user, db=db_session
        )
    assert exc_info.value.status_code == 409
