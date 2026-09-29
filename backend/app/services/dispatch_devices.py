"""可调度设备（系统设备）与 H5 设备选择。

管理后台（bhzn.top/admin）按槽位号维护一份可调度设备清单；H5 用户可以在「我的」
里选择其中之一。选中系统设备后，该用户只能使用 AI 营销创作，不能启动工作流或
其它工作（服务端兜底 + 前端只显示 AI 营销入口）。
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import DispatchDevice, H5ChatDevicePresence, UserDeviceSelection

SYSTEM_DEVICE_SOURCE = "system"
OWN_DEVICE_SOURCE = "own"
MARKETING_ONLY_MESSAGE = "当前选中的是可调度系统设备，只能使用 AI 营销创作"


def normalize_slot(value: object) -> str:
    return str(value or "").strip()[:128]


def system_device_ids(db: Session) -> set:
    rows = db.query(DispatchDevice.installation_id).filter(DispatchDevice.status == "enabled").all()
    return {normalize_slot(r[0]) for r in rows if normalize_slot(r[0])}


def is_system_device(db: Session, installation_id: str) -> bool:
    slot = normalize_slot(installation_id)
    if not slot:
        return False
    row = (
        db.query(DispatchDevice.id)
        .filter(DispatchDevice.installation_id == slot, DispatchDevice.status == "enabled")
        .first()
    )
    return row is not None


def get_selection(db: Session, user_id: int) -> Tuple[str, str]:
    row = db.query(UserDeviceSelection).filter(UserDeviceSelection.user_id == int(user_id)).first()
    if row is None:
        return "", ""
    source = str(row.source or OWN_DEVICE_SOURCE).strip().lower()
    if source not in (OWN_DEVICE_SOURCE, SYSTEM_DEVICE_SOURCE):
        source = OWN_DEVICE_SOURCE
    return normalize_slot(row.installation_id), source


def save_selection(db: Session, user_id: int, installation_id: str, source: str) -> Tuple[str, str]:
    slot = normalize_slot(installation_id)
    kind = SYSTEM_DEVICE_SOURCE if str(source or "").strip().lower() == SYSTEM_DEVICE_SOURCE else OWN_DEVICE_SOURCE
    row = db.query(UserDeviceSelection).filter(UserDeviceSelection.user_id == int(user_id)).first()
    if row is None:
        row = UserDeviceSelection(user_id=int(user_id), installation_id=slot, source=kind)
        db.add(row)
    else:
        row.installation_id = slot
        row.source = kind
        row.updated_at = datetime.utcnow()
        db.add(row)
    db.commit()
    return slot, kind


def user_uses_system_device(db: Session, user_id: int) -> bool:
    slot, source = get_selection(db, int(user_id))
    return bool(slot) and source == SYSTEM_DEVICE_SOURCE


def assert_marketing_only_allowed(db: Session, user_id: int, action: str = "") -> None:
    """选中系统设备时只允许 AI 营销创作；其他工作流/任务一律拒绝。"""
    if user_uses_system_device(db, int(user_id)):
        raise HTTPException(status_code=403, detail=MARKETING_ONLY_MESSAGE)


def system_device_rows(db: Session, now: Optional[datetime] = None) -> List[dict]:
    from .device_presence import is_device_online

    moment = now or datetime.utcnow()
    devices = (
        db.query(DispatchDevice)
        .filter(DispatchDevice.status == "enabled")
        .order_by(DispatchDevice.id.desc())
        .all()
    )
    slots = [normalize_slot(d.installation_id) for d in devices if normalize_slot(d.installation_id)]
    presence = {}
    if slots:
        rows = (
            db.query(H5ChatDevicePresence)
            .filter(H5ChatDevicePresence.installation_id.in_(slots))
            .order_by(H5ChatDevicePresence.last_seen_at.desc())
            .all()
        )
        for row in rows:
            key = normalize_slot(row.installation_id)
            if key and key not in presence:
                presence[key] = row
    out = []
    for device in devices:
        slot = normalize_slot(device.installation_id)
        if not slot:
            continue
        row = presence.get(slot)
        last_seen = getattr(row, "last_seen_at", None)
        out.append(
            {
                "installation_id": slot,
                "name": str(device.name or "").strip(),
                "note": str(device.note or "").strip(),
                "online": bool(row is not None and is_device_online(last_seen, now=moment)),
                "last_seen_at": last_seen.isoformat() if last_seen else "",
            }
        )
    return out
