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
# 「系统设备」这个逻辑选项的哨兵值：用户不关心哪台，由系统挑空闲设备
SYSTEM_SELECTION = "system"
OWN_DEVICE_SOURCE = "own"
MARKETING_ONLY_MESSAGE = "当前选中的是可调度系统设备，只能使用 AI 营销创作"


def normalize_slot(value: object) -> str:
    return str(value or "").strip()[:128]


def system_device_ids(db: Session) -> set:
    rows = db.query(DispatchDevice.installation_id).filter(DispatchDevice.status == "enabled").all()
    return {normalize_slot(r[0]) for r in rows if normalize_slot(r[0])}


def is_system_selection(value: object) -> bool:
    """是否是「系统设备」这个逻辑选项（不是具体槽位）。"""
    return normalize_slot(value) == SYSTEM_SELECTION


def has_system_devices(db: Session) -> bool:
    return bool(system_device_ids(db))


def is_system_device(db: Session, installation_id: str) -> bool:
    slot = normalize_slot(installation_id)
    if not slot:
        return False
    if slot == SYSTEM_SELECTION:
        # 逻辑选项：只要可调度设备池非空就算合法（具体派给哪台由 pick_idle 决定）
        return has_system_devices(db)
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


def pick_idle_system_device(db: Session, now: Optional[datetime] = None) -> str:
    """挑一台空闲的系统设备：池内 enabled、在线、且没有进行中的任务。

    找不到空闲时返回空串（调用方据此排队等待，而不是报错）。"""
    from .device_presence import is_device_online
    from ..models import ScheduledTaskRun

    moment = now or datetime.utcnow()
    devices = (
        db.query(DispatchDevice.installation_id)
        .filter(DispatchDevice.status == "enabled")
        .order_by(DispatchDevice.id.asc())
        .all()
    )
    slots = [normalize_slot(r[0]) for r in devices if normalize_slot(r[0])]
    if not slots:
        return ""
    busy = {
        normalize_slot(r[0])
        for r in db.query(ScheduledTaskRun.installation_id)
        .filter(
            ScheduledTaskRun.status.in_(("pending", "running", "claimed", "processing")),
            ScheduledTaskRun.installation_id.in_(slots),
        )
        .all()
        if normalize_slot(r[0])
    }
    presence = {}
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
    online_slots = [
        slot for slot in slots
        if is_device_online(getattr(presence.get(slot), "last_seen_at", None), now=moment)
    ]
    idle_online = [slot for slot in online_slots if slot not in busy]
    if idle_online:
        return idle_online[0]
    # 没有"在线且空闲"的：退而求其次给一台在线但可能排队的（保持"排队等空闲"语义，别丢目标）
    return online_slots[0] if online_slots else ""


def resolve_selection_target(db: Session, user_id: int, now: Optional[datetime] = None) -> str:
    """当前选择该把任务派给谁：自己的设备→原样；系统设备→当前空闲的一台（没有则空串=排队）。"""
    slot, source = get_selection(db, int(user_id))
    if source == SYSTEM_DEVICE_SOURCE or is_system_selection(slot):
        return pick_idle_system_device(db, now=now)
    return slot


def user_uses_system_device(db: Session, user_id: int) -> bool:
    slot, source = get_selection(db, int(user_id))
    return bool(slot) and source == SYSTEM_DEVICE_SOURCE


# AI 营销创作的动作（技能/模板）前缀：在系统设备下放行
MARKETING_TARGET_PREFIXES = (
    # AI 营销创作里的技能/模板（H5 侧真实 key）
    "ip_content", "image", "image_composer", "comfly.", "local_bestseller", "viral_video_remix",
    "wewrite.", "moments", "marketing", "seedance", "hypit", "ai_marketing",
    "shanjian", "digital", "hifly", "tts", "article", "wechat", "seedream", "banana",
    "kling", "sora", "veo", "hailuo", "minimax", "gpt", "music", "voice", "poster", "copywriting",
    "营销创作", "营销", "文案", "图片", "视频", "音频", "音乐", "海报", "朋友圈", "公众号",
)


def is_marketing_target(target: str) -> bool:
    value = str(target or "").strip().lower()
    if not value:
        return False
    return any(value.startswith(prefix) or prefix in value for prefix in MARKETING_TARGET_PREFIXES)


def assert_marketing_only_allowed(db: Session, user_id: int, action: str = "",
                                  from_marketing: bool = False) -> None:
    """选中系统设备时只允许 AI 营销创作。

    from_marketing=True：这次提交来自 AI 营销创作页面（含二级菜单）—— 不拦。
    """
    if from_marketing:
        return
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
