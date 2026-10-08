"""设备备注（用户可见的设备名）按机器身份保存 —— 槽位 ID 变了名字还在。

背景（2026-09-17）：用户的设备备注（例如「全天抖音获客」）以前只存在
``h5_chat_device_presence.display_name``，键是 installation_id。客户端换槽位后
（换账号登录、换品牌、OTA 后新签名槽位、machine_identity.json 重建），新槽位那行是空的，
界面上就退回默认名字 ``local-online``，看起来像「备注丢了」。

这里把备注挂到 machine_instance_id：
- 写：人工改名 / 客户端上报名字 → remember_device_label()
- 读：resolve_device_label() 先按机器身份找，再按槽位找，再退回 presence 上的名字
- 自动沿用：adopt_device_label() 在心跳时把旧槽位的名字套到新槽位上（source=auto）
- 沿用建议：suggest_device_label() 给「机器身份也变了」的情况兜底：
  同账号下最近改过、且那条槽位已经离线的备注，界面可以提示「是否沿用 X」
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import H5ChatDevicePresence, UserDeviceLabel, UserMachineIdentity

logger = logging.getLogger(__name__)

# 这些名字等于「没改过」，不能当成用户设置的备注
DEFAULT_DEVICE_NAMES = {"", "local-online", "online", "在线设备", "本机"}

SUGGEST_MAX_IDLE_DAYS = 30
SUGGEST_MIN_OFFLINE_MINUTES = 60


def is_custom_label(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and text.lower() not in DEFAULT_DEVICE_NAMES


def machine_instance_id_for(db: Session, user_id: int, installation_id: str) -> str:
    """本机机器身份（user_machine_identities）：按槽位反查，取最近一条。"""
    iid = str(installation_id or "").strip()
    if not iid:
        return ""
    row = (
        db.query(UserMachineIdentity)
        .filter(
            UserMachineIdentity.user_id == int(user_id),
            UserMachineIdentity.installation_id == iid,
        )
        .order_by(UserMachineIdentity.last_seen_at.desc())
        .first()
    )
    return str(getattr(row, "machine_instance_id", "") or "").strip()


def label_key_for(machine_instance_id: str, installation_id: str) -> str:
    machine = str(machine_instance_id or "").strip()
    if machine:
        return machine[:192]
    iid = str(installation_id or "").strip()
    return (f"slot:{iid}" if iid else "")[:192]


def _slot_label_row(db: Session, user_id: int, installation_id: str) -> Optional[UserDeviceLabel]:
    iid = str(installation_id or "").strip()
    if not iid:
        return None
    return (
        db.query(UserDeviceLabel)
        .filter(
            UserDeviceLabel.user_id == int(user_id),
            UserDeviceLabel.last_installation_id == iid,
        )
        .order_by(UserDeviceLabel.updated_at.desc())
        .first()
    )


def machine_for_slot(db: Session, user_id: int, installation_id: str) -> str:
    """写备注时用的机器身份：先查身份表，查不到再按「这个槽位以前的备注行」反查。

    身份表一台机器只有一行（槽位变化时就地更新），所以在「已经不是当前槽位」的老槽位上
    改名时查不到机器身份；这时用备注行里记着的 machine_instance_id，名字才会继续跟机器走。
    """
    machine = machine_instance_id_for(db, user_id, installation_id)
    if machine:
        return machine
    row = _slot_label_row(db, user_id, installation_id)
    return str(getattr(row, "machine_instance_id", "") or "").strip()


def _rekey_label_to_machine(db: Session, user_id: int, machine: str, installation_id: str) -> None:
    """机器身份后来才有（客户端重新 bind）时，把按 slot: 存的备注改成按机器存。

    这样老数据（改名时还没上报机器身份）在第一次心跳/读设备列表后也会升级为「跟机器走」，
    同一台机器只留一行，避免两行名字打架。
    """
    machine = str(machine or "").strip()
    iid = str(installation_id or "").strip()
    if not machine or not iid:
        return
    slot_row = _slot_label_row(db, user_id, iid)
    if slot_row is None or str(slot_row.label_key or "") == machine[:192]:
        return
    machine_row = (
        db.query(UserDeviceLabel)
        .filter(
            UserDeviceLabel.user_id == int(user_id),
            UserDeviceLabel.label_key == machine[:192],
        )
        .first()
    )
    now = datetime.utcnow()
    if machine_row is None:
        slot_row.label_key = machine[:192]
        slot_row.machine_instance_id = machine
        slot_row.last_installation_id = iid
        slot_row.updated_at = now
        db.add(slot_row)
        logger.info("[device-label] 备注改为按机器身份保存 slot=%s machine=%s", iid[:18], machine[:18])
    else:
        if is_custom_label(slot_row.display_name):
            machine_row.display_name = str(slot_row.display_name)[:128]
        machine_row.last_installation_id = iid
        machine_row.updated_at = now
        db.add(machine_row)
        db.delete(slot_row)
        logger.info("[device-label] 同一台机器两行备注已合并 machine=%s", machine[:18])
    db.flush()


def remember_device_label(
    db: Session,
    *,
    user_id: int,
    installation_id: str,
    display_name: str,
    source: str = "manual",
) -> Optional[UserDeviceLabel]:
    """写设备备注（按机器身份；同一台机器换槽位后仍命中同一条）。"""
    name = str(display_name or "").strip()[:128]
    iid = str(installation_id or "").strip()[:128]
    if not name or not iid:
        return None
    machine = machine_for_slot(db, user_id, iid)
    key = label_key_for(machine, iid)
    row = (
        db.query(UserDeviceLabel)
        .filter(UserDeviceLabel.user_id == int(user_id), UserDeviceLabel.label_key == key)
        .first()
    )
    now = datetime.utcnow()
    if row is None:
        row = UserDeviceLabel(
            user_id=int(user_id),
            label_key=key,
            machine_instance_id=machine or None,
            display_name=name,
            last_installation_id=iid,
            source=source,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.display_name = name
        row.last_installation_id = iid
        if machine:
            row.machine_instance_id = machine
        row.updated_at = now
        if source == "manual":
            row.source = "manual"
    db.flush()
    return row


def resolve_device_label(db: Session, user_id: int, installation_id: str) -> Tuple[str, str]:
    """设备显示名 + 来源：label_machine / label_slot / presence / none。"""
    iid = str(installation_id or "").strip()
    if not iid:
        return "", "none"
    machine = machine_instance_id_for(db, user_id, iid)
    if machine:
        row = (
            db.query(UserDeviceLabel)
            .filter(
                UserDeviceLabel.user_id == int(user_id),
                UserDeviceLabel.label_key == machine[:192],
            )
            .first()
        )
        if row and is_custom_label(row.display_name):
            return str(row.display_name), "label_machine"
    row = (
        db.query(UserDeviceLabel)
        .filter(
            UserDeviceLabel.user_id == int(user_id),
            UserDeviceLabel.last_installation_id == iid,
        )
        .order_by(UserDeviceLabel.updated_at.desc())
        .first()
    )
    if row and is_custom_label(row.display_name):
        return str(row.display_name), "label_slot"
    presence = (
        db.query(H5ChatDevicePresence)
        .filter(
            H5ChatDevicePresence.user_id == int(user_id),
            H5ChatDevicePresence.installation_id == iid,
        )
        .first()
    )
    if presence is not None and is_custom_label(presence.display_name):
        return str(presence.display_name), "presence"
    return "", "none"


def adopt_device_label(db: Session, user_id: int, installation_id: str) -> Optional[str]:
    """心跳时调用：presence 那行没有自定义名字时，自动沿用本机备注。"""
    iid = str(installation_id or "").strip()
    if not iid:
        return None
    presence = (
        db.query(H5ChatDevicePresence)
        .filter(
            H5ChatDevicePresence.user_id == int(user_id),
            H5ChatDevicePresence.installation_id == iid,
        )
        .first()
    )
    if presence is None:
        return None
    # 老数据升级：备注原来按 slot: 存的（改名时客户端还没上报机器身份），
    # 现在机器身份有了，就先把它改挂到机器身份上，之后换槽位才能自动沿用。
    _rekey_label_to_machine(db, user_id, machine_for_slot(db, user_id, iid), iid)
    if is_custom_label(presence.display_name):
        return str(presence.display_name)
    name, source = resolve_device_label(db, user_id, iid)
    if not name:
        return None
    presence.display_name = name[:128]
    db.add(presence)
    machine = machine_for_slot(db, user_id, iid)
    row = (
        db.query(UserDeviceLabel)
        .filter(
            UserDeviceLabel.user_id == int(user_id),
            UserDeviceLabel.display_name == name,
            UserDeviceLabel.label_key == label_key_for(machine, iid),
        )
        .first()
    )
    if row is not None and row.last_installation_id != iid:
        row.last_installation_id = iid
        row.updated_at = datetime.utcnow()
        db.add(row)
    logger.info(
        "[device-label] 槽位 %s 自动沿用设备名「%s」(来源=%s)", iid[:18], name, source
    )
    return name


def apply_label_to_machine_slots(
    db: Session,
    *,
    user_id: int,
    installation_id: str,
    display_name: str,
) -> List[str]:
    """人工改名后，把同一台机器（同 machine_instance_id）的其它槽位也同步改名。"""
    machine = machine_for_slot(db, user_id, installation_id)
    if not machine:
        return []
    ids = [
        str(row.installation_id)
        for row in db.query(UserMachineIdentity)
        .filter(
            UserMachineIdentity.user_id == int(user_id),
            UserMachineIdentity.machine_instance_id == machine,
        )
        .all()
    ]
    iid = str(installation_id or "").strip()
    if iid and iid not in ids:
        ids.append(iid)
    updated: List[str] = []
    if not ids:
        return updated
    rows = (
        db.query(H5ChatDevicePresence)
        .filter(
            H5ChatDevicePresence.user_id == int(user_id),
            H5ChatDevicePresence.installation_id.in_(ids),
        )
        .all()
    )
    for row in rows:
        if str(row.display_name or "") != display_name:
            row.display_name = display_name[:128]
            db.add(row)
            updated.append(str(row.installation_id))
    return updated


def suggest_device_labels(
    db: Session,
    *,
    user_id: int,
    installation_id: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """本机没有备注时的沿用候选：同账号最近改名、且那条槽位已离线的备注（新的在前）。

    只做建议（返回给界面提示），不会自动改名。
    """
    iid = str(installation_id or "").strip()
    current_name, _source = resolve_device_label(db, user_id, iid)
    if current_name:
        return []
    now = datetime.utcnow()
    rows = (
        db.query(UserDeviceLabel)
        .filter(
            UserDeviceLabel.user_id == int(user_id),
            UserDeviceLabel.source == "manual",
            UserDeviceLabel.updated_at >= now - timedelta(days=SUGGEST_MAX_IDLE_DAYS),
        )
        .order_by(UserDeviceLabel.updated_at.desc())
        .limit(20)
        .all()
    )
    out: List[Dict[str, Any]] = []
    for row in rows:
        name = str(row.display_name or "").strip()
        from_slot = str(row.last_installation_id or "").strip()
        if not is_custom_label(name) or not from_slot or from_slot == iid:
            continue
        if any(item["display_name"] == name for item in out):
            continue  # 同一台机器改过几次名：只留最近那条
        other = (
            db.query(H5ChatDevicePresence)
            .filter(
                H5ChatDevicePresence.user_id == int(user_id),
                H5ChatDevicePresence.installation_id == from_slot,
            )
            .first()
        )
        idle_minutes = None
        if other is not None and other.last_seen_at:
            idle_minutes = (now - other.last_seen_at).total_seconds() / 60.0
        if other is not None and idle_minutes is not None and idle_minutes < SUGGEST_MIN_OFFLINE_MINUTES:
            continue  # 那台设备还活着，不能建议搬名字
        out.append({
            "display_name": name,
            "from_installation_id": from_slot,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "idle_minutes": int(idle_minutes) if idle_minutes is not None else None,
            "machine_instance_id": row.machine_instance_id or "",
        })
        if len(out) >= max(1, int(limit)):
            break
    return out


def suggest_device_label(
    db: Session,
    *,
    user_id: int,
    installation_id: str,
) -> Optional[Dict[str, Any]]:
    """沿用建议的第一条（界面只放一条建议时用）。"""
    items = suggest_device_labels(db, user_id=user_id, installation_id=installation_id, limit=1)
    return items[0] if items else None


def device_names_for(db: Session, user_id: int, installation_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """批量解析一批槽位的显示名（设备列表用）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for iid in installation_ids:
        name, source = resolve_device_label(db, user_id, iid)
        out[str(iid)] = {"display_name": name, "label_source": source}
    return out
