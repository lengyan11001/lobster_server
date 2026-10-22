"""User settings: model selection, preferences."""
import hashlib
import hmac
import json
import logging
import re
import secrets
import socket
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from .auth import get_current_user, request_auth_session_id
from .mobile_identity import online_user_for_mobile_user
from .installation_slots import ensure_installation_slot, optional_installation_id_from_request
from ..models import H5ChatDevicePresence, H5MountedAccountDefault, User, UserDeviceLabel, UserInstallation, UserMachineIdentity
from ..services.brand_context import normalize_brand_mark, scoped_installation_id, user_brand_mark
from ..services.installation_slot_ownership import (
    claim_installation_slot,
    migrate_installation_slot_references,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_CUSTOM_CONFIGS_FILE = Path(__file__).resolve().parent.parent.parent.parent / "custom_configs.json"
_INSTALLATION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,128}$")


class InstallationIdEnsureRequest(BaseModel):
    candidate: Optional[str] = None
    force_new: bool = False
    brand_mark: Optional[str] = None


class InstallationIdBindRequest(BaseModel):
    installation_id: Optional[str] = None
    device_id: Optional[str] = None
    machine_instance_id: Optional[str] = None
    legacy_machine_instance_id: Optional[str] = None
    force_new: bool = False


def _normalize_installation_id(raw: Optional[str]) -> str:
    value = (raw or "").strip()
    return value if _INSTALLATION_ID_RE.fullmatch(value) else ""


def _normalize_identity_list(raw: Optional[str]) -> list[str]:
    """把 "id1,id2 id3" 解析成合法 id 列表（换机器 id 后新旧值都要认）。"""
    values: list[str] = []
    for part in re.split(r"[,;\s]+", str(raw or "").strip()):
        item = _normalize_installation_id(part)
        if item and item not in values:
            values.append(item)
    return values


def _request_installation_brand(request: Request, raw_brand: Optional[str] = None) -> str:
    return normalize_brand_mark(
        raw_brand
        or request.headers.get("x-lobster-brand")
        or request.headers.get("X-Lobster-Brand")
        or "bihuo",
        strict=False,
    )


def _installation_id_variants(installation_id: str, brand_mark: str = "") -> set[str]:
    raw = _normalize_installation_id(installation_id)
    if not raw:
        return set()
    values = {raw}
    if brand_mark:
        scoped = scoped_installation_id(raw, brand_mark)
        if scoped:
            values.add(scoped)
    return values


_INSTALLATION_PRESENCE_WINDOW_MINUTES = 10


def _installation_id_usage(
    db: Session,
    installation_id: str,
    *,
    brand_mark: str = "",
    exclude_user_id: Optional[int] = None,
) -> dict[str, Any]:
    """槽位占用判断：只有“另一台设备/账号现在还在线用这个槽位”才算占用。

    历史 `user_installations` 行不参与判定：同一台机器换账号登录、重装、OTA
    都会留下旧账号的槽位记录，按历史行判占用会永远提示“被占用”。
    同一台机器（machine_instance_id 相同）上的其它账号也不算冲突。
    """
    raw = _normalize_installation_id(installation_id)
    if not raw:
        return {
            "valid": False,
            "taken": False,
            "user_ids": [],
            "presence_user_ids": [],
            "installation_ids": [],
        }
    variants = _installation_id_variants(raw, brand_mark)
    suffix = f"--{raw}"
    user_ids: set[int] = set()
    presence_user_ids: set[int] = set()
    matched_ids: set[str] = set()

    q = db.query(UserInstallation.user_id, UserInstallation.installation_id).filter(
        or_(
            UserInstallation.installation_id.in_(tuple(variants)),
            UserInstallation.installation_id.like(f"%{suffix}"),
        )
    )
    for uid, iid in q.all():
        try:
            user_id = int(uid)
        except (TypeError, ValueError):
            continue
        if exclude_user_id is not None and user_id == int(exclude_user_id):
            continue
        user_ids.add(user_id)
        if iid:
            matched_ids.add(str(iid))

    # 只有最近还在心跳的设备才算“正在占用”。
    cutoff = datetime.utcnow() - timedelta(minutes=_INSTALLATION_PRESENCE_WINDOW_MINUTES)
    p = db.query(H5ChatDevicePresence.user_id, H5ChatDevicePresence.installation_id).filter(
        or_(
            H5ChatDevicePresence.installation_id.in_(tuple(variants)),
            H5ChatDevicePresence.installation_id.like(f"%{suffix}"),
        ),
        H5ChatDevicePresence.last_seen_at >= cutoff,
    )
    for uid, iid in p.all():
        try:
            user_id = int(uid)
        except (TypeError, ValueError):
            continue
        if exclude_user_id is not None and user_id == int(exclude_user_id):
            continue
        presence_user_ids.add(user_id)
        if iid:
            matched_ids.add(str(iid))

    # 同一台机器上的其它账号（换账号登录/多开）不算冲突。
    candidates = user_ids | presence_user_ids
    if exclude_user_id is not None and candidates:
        mine = {
            str(mid)
            for (mid,) in db.query(UserMachineIdentity.machine_instance_id)
            .filter(UserMachineIdentity.user_id == int(exclude_user_id))
            .all()
            if mid
        }
        if mine:
            same_machine_uids = {
                int(uid)
                for uid, mid in db.query(
                    UserMachineIdentity.user_id, UserMachineIdentity.machine_instance_id
                )
                .filter(UserMachineIdentity.user_id.in_(tuple(candidates)))
                .all()
                if mid and str(mid) in mine
            }
            user_ids -= same_machine_uids
            presence_user_ids -= same_machine_uids

    return {
        "valid": True,
        # 历史口径（其它账号用过这个槽位）：/bind 签名仍需要它。
        "taken": bool(user_ids),
        # 实时口径（另一台设备/账号现在还在心跳）：只用于 /status 的“被占用”提示。
        "live_taken": bool(presence_user_ids),
        "user_ids": sorted(user_ids),
        "presence_user_ids": sorted(presence_user_ids),
        "installation_ids": sorted(matched_ids),
    }


def _new_unique_installation_id(db: Session, *, brand_mark: str = "") -> str:
    for _ in range(32):
        candidate = secrets.token_hex(16)
        if not _installation_id_usage(db, candidate, brand_mark=brand_mark).get("taken"):
            return candidate
    raise HTTPException(status_code=503, detail="unable to allocate unique installation id")


def _signed_installation_id_for_user(
    user: User,
    device_id: str,
    brand_mark: str,
    machine_instance_id: str = "",
) -> str:
    raw_device_id = _normalize_installation_id(device_id)
    if not raw_device_id:
        raise HTTPException(status_code=400, detail="missing or invalid device id")
    machine_id = _normalize_installation_id(machine_instance_id) or raw_device_id
    user_id = int(user.id)
    account = str(getattr(user, "email", "") or user_id).strip().lower()
    secret = str(getattr(settings, "secret_key", "") or "lobster-installation-slot").encode("utf-8")
    payload = (
        f"{normalize_brand_mark(brand_mark or 'bihuo', strict=False)}\0"
        f"{user_id}\0{account}\0{machine_id}\0{raw_device_id}"
    ).encode("utf-8")
    digest = hmac.new(secret, payload, hashlib.sha256).hexdigest()[:32]
    return f"u{user_id}-{digest}"


def _is_signed_installation_id_for_user(value: str, user_id: int) -> bool:
    return bool(re.fullmatch(rf"u{int(user_id)}-[a-f0-9]{{32}}", str(value or "").strip(), flags=re.IGNORECASE))


def _signed_installation_id_conflicts(
    db: Session,
    installation_id: str,
    *,
    user_id: int,
    machine_instance_id: str,
    brand_mark: str,
) -> bool:
    if _installation_id_usage(
        db,
        installation_id,
        brand_mark=brand_mark,
        exclude_user_id=user_id,
    ).get("taken"):
        return True
    return bool(
        db.query(UserMachineIdentity.id)
        .filter(
            UserMachineIdentity.user_id == user_id,
            UserMachineIdentity.installation_id == installation_id,
            UserMachineIdentity.machine_instance_id != machine_instance_id,
        )
        .first()
    )


def _unique_signed_installation_id_for_user(
    db: Session,
    user: User,
    device_id: str,
    brand_mark: str,
    machine_instance_id: str,
) -> str:
    machine_id = _normalize_installation_id(machine_instance_id) or _normalize_installation_id(device_id)
    for attempt in range(16):
        signed_machine_id = machine_id if attempt == 0 else f"{machine_id[:96]}-{secrets.token_hex(8)}"
        signed_id = _signed_installation_id_for_user(
            user,
            device_id,
            brand_mark,
            machine_instance_id=signed_machine_id,
        )
        if not _signed_installation_id_conflicts(
            db,
            signed_id,
            user_id=int(user.id),
            machine_instance_id=machine_id,
            brand_mark=brand_mark,
        ):
            return signed_id
    raise HTTPException(status_code=503, detail="unable to allocate unique signed installation id")


def _stale_single_slot_for_rebind(
    db: Session,
    *,
    user_id: int,
    current_installation_id: str,
    now: datetime,
) -> str:
    """Return a sole long-unused slot for a reinstall recovery.

    This is intentionally conservative: only one historical slot, no recent
    heartbeat, and the incoming id must not already be registered.  Multiple
    active devices therefore keep their independent slots.
    """
    current = _normalize_installation_id(current_installation_id)
    if not current:
        return ""
    if db.query(UserInstallation.id).filter(
        UserInstallation.user_id == user_id,
        UserInstallation.installation_id == current,
    ).first() is not None:
        return ""
    rows = (
        db.query(UserInstallation)
        .filter(UserInstallation.user_id == user_id)
        .order_by(UserInstallation.last_seen_at.desc())
        .all()
    )
    # A legacy account may have both raw and brand-scoped copies of the same
    # slot. Treat those as one slot, but keep genuinely different devices
    # independent.
    groups: dict[str, list[UserInstallation]] = {}
    for row in rows:
        value = str(row.installation_id or "").strip()
        base = value.split("--", 1)[-1] if value else ""
        if base:
            groups.setdefault(base, []).append(row)
    if len(groups) != 1:
        return ""
    candidate = max(next(iter(groups.values())), key=lambda row: row.last_seen_at or datetime.min)
    if not candidate.installation_id or candidate.installation_id == current:
        return ""
    seen = candidate.last_seen_at
    if seen and (now - seen).total_seconds() < 24 * 3600:
        return ""
    candidate_ids = {str(row.installation_id or "").strip() for row in next(iter(groups.values()))}
    candidate_ids.add(next(iter(groups.keys())))
    recent_cutoff = now - timedelta(hours=2)
    if db.query(H5ChatDevicePresence.id).filter(
        H5ChatDevicePresence.user_id == user_id,
        H5ChatDevicePresence.installation_id.in_(tuple(candidate_ids)),
        H5ChatDevicePresence.last_seen_at >= recent_cutoff,
    ).first() is not None:
        return ""
    value = str(candidate.installation_id or "").strip()
    # UserInstallation stores non-default brands as ``brand--raw`` while the
    # client and workflow rows keep the raw id. Return the raw portion here so
    # all references converge on the same value.
    if "--" in value:
        value = value.split("--", 1)[1]
    return _normalize_installation_id(value)


def _read_server_tos_config_dict() -> Optional[Dict[str, Any]]:
    """Read server-side TOS_CONFIG for status checks; never return AK/SK to clients."""
    if not _CUSTOM_CONFIGS_FILE.exists():
        return None
    try:
        data = json.loads(_CUSTOM_CONFIGS_FILE.read_text(encoding="utf-8"))
        cfg = (data.get("configs") or {}).get("TOS_CONFIG")
        if not isinstance(cfg, dict):
            return None
        ak = str(cfg.get("access_key", "")).strip()
        sk = str(cfg.get("secret_key", "")).strip()
        if not ak or not sk:
            return None
        return cfg
    except Exception:
        return None


def _use_own_wechat_login() -> bool:
    return bool((getattr(settings, "wechat_app_id", None) or "").strip() and (getattr(settings, "wechat_app_secret", None) or "").strip())


def _use_fuiou_pay() -> bool:
    from ..services.fuiou_pay import fuiou_configured
    return fuiou_configured()


@router.get("/api/edition", summary="当前版本（本构建仅在线版）")
def get_edition():
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    if edition != "online":
        edition = "online"
    out = {"edition": edition}
    use_independent = getattr(settings, "lobster_independent_auth", True)
    out["use_independent_auth"] = bool(use_independent)
    out["use_own_wechat_login"] = _use_own_wechat_login()
    out["use_fuiou_pay"] = _use_fuiou_pay()
    if edition == "online":
        out["allow_self_config_model"] = getattr(settings, "sutui_online_model_self_config", True)
        if not use_independent:
            out["recharge_url"] = (getattr(settings, "sutui_recharge_url", None) or "").strip() or None
    return out


@router.post("/api/installation-id/ensure", summary="Ensure an installation id is not already used")
def ensure_unique_installation_id(
    body: InstallationIdEnsureRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Public lightweight endpoint used only when a client creates a fresh local slot id."""
    brand_mark = _request_installation_brand(request, body.brand_mark)
    candidate = _normalize_installation_id(body.candidate)
    usage = _installation_id_usage(db, candidate, brand_mark=brand_mark) if candidate else {
        "valid": False,
        "taken": False,
        "user_ids": [],
        "presence_user_ids": [],
        "installation_ids": [],
    }
    if body.force_new or not candidate or usage.get("taken"):
        installation_id = _new_unique_installation_id(db, brand_mark=brand_mark)
    else:
        installation_id = candidate
    return {
        "ok": True,
        "installation_id": installation_id,
        "candidate": candidate,
        "changed": installation_id != candidate,
        "duplicate": bool(usage.get("taken")),
        "duplicate_user_count": len(usage.get("user_ids") or []),
        "presence_user_count": len(usage.get("presence_user_ids") or []),
    }


@router.get("/api/installation-id/status", summary="Current installation id duplicate status")
def current_installation_id_status(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    raw = optional_installation_id_from_request(request)
    installation_id = _normalize_installation_id(raw)
    if not installation_id:
        raise HTTPException(status_code=400, detail="missing or invalid installation id")
    brand_mark = user_brand_mark(current_user)
    scoped_id = scoped_installation_id(installation_id, brand_mark) or installation_id
    ensure_installation_slot(db, current_user.id, scoped_id)
    usage = _installation_id_usage(
        db,
        installation_id,
        brand_mark=brand_mark,
        exclude_user_id=current_user.id,
    )
    return {
        "ok": True,
        "installation_id": installation_id,
        "scoped_installation_id": scoped_id,
        # 提示只针对“现在还在线的另一台设备/账号”；历史行不再报警。
        "duplicate": bool(usage.get("live_taken")),
        "duplicate_user_count": len(usage.get("presence_user_ids") or []),
        "history_user_count": len(usage.get("user_ids") or []),
        "presence_user_count": len(usage.get("presence_user_ids") or []),
        "matched_installation_ids": usage.get("installation_ids") or [],
    }


class AliasRestoreRequest(BaseModel):
    alias: str = ""
    machine_instance_id: Optional[str] = None


def _alias_owner_ids(db: Session, current_user: User) -> set[int]:
    ids = {int(current_user.id)}
    try:
        owner = online_user_for_mobile_user(db, current_user)
    except Exception:
        owner = None
    owner_id = getattr(owner, "id", None)
    if owner_id:
        ids.add(int(owner_id))
    return ids


@router.get("/api/installation-id/aliases", summary="当前账号的设备别名及原槽位")
def list_installation_aliases(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_ids = _alias_owner_ids(db, current_user)
    rows = (
        db.query(UserDeviceLabel)
        .filter(UserDeviceLabel.user_id.in_(tuple(owner_ids)))
        .order_by(UserDeviceLabel.updated_at.desc())
        .all()
    )
    cutoff = datetime.utcnow() - timedelta(minutes=_INSTALLATION_PRESENCE_WINDOW_MINUTES)
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for row in rows:
        alias = str(row.display_name or "").strip()
        slot = str(row.last_installation_id or "").strip()
        if not alias or not slot:
            continue
        key = alias.lower()
        if key in seen or key.startswith("local-"):
            continue
        seen.add(key)
        online = (
            db.query(H5ChatDevicePresence.id)
            .filter(
                H5ChatDevicePresence.installation_id == slot,
                H5ChatDevicePresence.last_seen_at >= cutoff,
            )
            .first()
            is not None
        )
        items.append(
            {
                "alias": alias,
                "installation_id": slot,
                "online": online,
                "updated_at": row.updated_at.isoformat() if row.updated_at else "",
            }
        )
    return {"ok": True, "items": items}


@router.post("/api/installation-id/aliases/restore", summary="按设备别名找回原来的槽位")
def restore_installation_alias(
    body: AliasRestoreRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    alias = str(body.alias or "").strip()
    if not alias:
        raise HTTPException(status_code=400, detail="请选择设备别名")
    owner_ids = _alias_owner_ids(db, current_user)
    rows = (
        db.query(UserDeviceLabel)
        .filter(UserDeviceLabel.user_id.in_(tuple(owner_ids)))
        .all()
    )
    matched = [row for row in rows if str(row.display_name or "").strip().lower() == alias.lower()]
    if not matched:
        raise HTTPException(status_code=404, detail="没有找到这个设备别名")
    matched.sort(key=lambda row: row.updated_at or datetime.min, reverse=True)
    slot = str(matched[0].last_installation_id or "").strip()
    if not slot:
        raise HTTPException(status_code=409, detail="这个别名还没有绑定过槽位")
    usage = _installation_id_usage(db, slot, exclude_user_id=current_user.id)
    if usage.get("live_taken"):
        raise HTTPException(
            status_code=409,
            detail="该别名对应的槽位正被另一台设备在线使用，请先在原设备上停止使用后再恢复",
        )
    # 机器身份也要一起改到恢复后的槽位，否则下一次登录 bind 会按 known_machine
    # 把槽位又改回旧的。
    current_raw = optional_installation_id_from_request(request)
    machine_raw = _normalize_installation_id(body.machine_instance_id)
    updated_machines = 0
    machine_query = db.query(UserMachineIdentity).filter(
        UserMachineIdentity.user_id == int(current_user.id)
    )
    if machine_raw:
        machine_query = machine_query.filter(UserMachineIdentity.machine_instance_id == machine_raw)
    elif current_raw:
        candidates_for_machine = {current_raw}
        scoped_current = scoped_installation_id(current_raw, user_brand_mark(current_user))
        if scoped_current:
            candidates_for_machine.add(scoped_current)
        machine_query = machine_query.filter(
            UserMachineIdentity.installation_id.in_(tuple(candidates_for_machine))
        )
    else:
        machine_query = None
    if machine_query is not None:
        updated_machines = machine_query.update(
            {UserMachineIdentity.installation_id: slot},
            synchronize_session=False,
        )
        db.commit()
    # 发布账号/接管账号是按槽位绑定的，换槽位后必须跟着这台机器一起走，
    # 否则启动工作流会报“默认发布账号不在当前启用设备上”。
    moved_defaults = 0
    if current_raw:
        candidates = {current_raw}
        scoped_current = scoped_installation_id(current_raw, user_brand_mark(current_user))
        if scoped_current:
            candidates.add(scoped_current)
        candidates.discard(slot)
        if candidates:
            moved_defaults = (
                db.query(H5MountedAccountDefault)
                .filter(
                    H5MountedAccountDefault.user_id == int(current_user.id),
                    H5MountedAccountDefault.installation_id.in_(tuple(candidates)),
                )
                .update(
                    {H5MountedAccountDefault.installation_id: slot},
                    synchronize_session=False,
                )
            )
            db.commit()
    return {
        "ok": True,
        "alias": alias,
        "installation_id": slot,
        "moved_defaults": int(moved_defaults or 0),
        "updated_machines": int(updated_machines or 0),
    }


@router.post("/api/installation-id/bind", summary="Bind a unique installation id to current user")
def bind_unique_installation_id(
    body: InstallationIdBindRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    brand_mark = user_brand_mark(current_user)
    current_installation_id = _normalize_installation_id(body.installation_id) or _normalize_installation_id(
        optional_installation_id_from_request(request)
    )
    device_id = _normalize_installation_id(body.device_id)
    machine_instance_id = _normalize_installation_id(body.machine_instance_id)
    has_machine_identity = bool(machine_instance_id)
    if body.force_new and not device_id:
        device_id = _new_unique_installation_id(db, brand_mark=brand_mark)
    if not device_id:
        device_id = current_installation_id
    if not device_id:
        raise HTTPException(status_code=400, detail="missing or invalid device id")

    # Old clients do not send a machine identity. Falling back to device_id
    # preserves their previous behavior while new clients can separate two
    # machines that arrived with the same copied legacy slot.
    machine_id = machine_instance_id or device_id
    # 出厂镜像换了机器身份（m… -> mw…）：把该用户的设备别名迁到新机器 id，
    # 否则别名会“消失”。只迁当前用户的行。
    legacy_machine_ids = _normalize_identity_list(body.legacy_machine_instance_id)
    if legacy_machine_ids and machine_id and current_installation_id:
        # 只迁本槽位自己的备注：克隆机会共用一个机器 id，不按槽位限定会互相抢备注。
        migrated_labels = 0
        for legacy_machine_id in legacy_machine_ids:
            if legacy_machine_id == machine_id:
                continue
            migrated_labels += (
                db.query(UserDeviceLabel)
                .filter(
                    UserDeviceLabel.user_id == int(current_user.id),
                    UserDeviceLabel.machine_instance_id == legacy_machine_id,
                    UserDeviceLabel.last_installation_id == current_installation_id,
                )
                .update(
                    {
                        UserDeviceLabel.machine_instance_id: machine_id,
                        UserDeviceLabel.label_key: machine_id[:192],
                    },
                    synchronize_session=False,
                )
            )
        if migrated_labels:
            db.commit()
        logger.info(
            "[installation-identity] machine id migrated user=%s legacy=%s new=%s slot=%s labels=%s",
            getattr(current_user, "id", ""),
            ",".join(legacy_machine_ids),
            machine_id,
            current_installation_id[:18],
            int(migrated_labels or 0),
        )
    known_machine = (
        db.query(UserMachineIdentity)
        .filter(
            UserMachineIdentity.user_id == current_user.id,
            UserMachineIdentity.machine_instance_id == machine_id,
        )
        .first()
    )
    current_is_signed = _is_signed_installation_id_for_user(current_installation_id, current_user.id)
    # A copied installation can present a signed slot that is already active
    # on another machine.  Only split it when that other machine is currently
    # online; an offline/reinstalled client is allowed to retain its signed
    # slot so OTA/restart does not unexpectedly move its data.
    signed_slot_machine_conflict = False
    if current_is_signed and has_machine_identity:
        other_machine = (
            db.query(UserMachineIdentity)
            .filter(
                UserMachineIdentity.user_id == current_user.id,
                UserMachineIdentity.installation_id == current_installation_id,
                UserMachineIdentity.machine_instance_id != machine_id,
            )
            .first()
        )
        if other_machine is not None:
            # 机器身份现在来自硬件（mw…）：这个 iid 已经被另一台物理机器占用，
            # 说明是出厂镜像复制出来的“克隆设备”，必须给它重新分配槽位。
            recent_cutoff = datetime.utcnow() - timedelta(days=7)
            other_id = str(other_machine.machine_instance_id or "").strip()
            other_is_self = other_id in legacy_machine_ids
            other_is_hardware = other_id.startswith("mw")
            signed_slot_machine_conflict = bool(
                not other_is_self
                and other_is_hardware
                and other_machine.last_seen_at
                and other_machine.last_seen_at >= recent_cutoff
            )
    stale_slot = ""
    if body.force_new:
        installation_id = _unique_signed_installation_id_for_user(db, current_user, device_id, brand_mark, f"{machine_id}-{secrets.token_hex(8)}")
        duplicate_before = False
        signed = True
        signature_reason = "force_new"
    elif known_machine is not None:
        # 2026-10-22 事故：机器行记的是旧槽位（34c5），客户端文件是固化槽位（205b），
        # bind 每次把 34c5 塞回客户端，客户端文件又拒绝覆盖 → 两边永远对不上。
        # 口径：客户端文件里的已签名槽位是权威；机器行跟着它更新，而不是反过来。
        if current_is_signed and not signed_slot_machine_conflict and current_installation_id:
            installation_id = current_installation_id
            duplicate_before = False
            signed = True
            if _normalize_installation_id(known_machine.installation_id) == installation_id:
                signature_reason = "known_machine"
            else:
                signature_reason = "current_file_wins"
            if _normalize_installation_id(known_machine.installation_id) != installation_id:
                logger.info(
                    "[installation-identity] machine row corrected user=%s machine=%s old=%s new=%s",
                    getattr(current_user, "id", ""),
                    machine_id,
                    known_machine.installation_id,
                    installation_id,
                )
                known_machine.installation_id = installation_id
                db.add(known_machine)
                db.commit()
        else:
            # 客户端没带有效已签名槽位（老客户端/文件丢失）：沿用机器行，保持稳定。
            installation_id = _normalize_installation_id(known_machine.installation_id) or current_installation_id
            duplicate_before = False
            signed = _is_signed_installation_id_for_user(installation_id, current_user.id)
            signature_reason = "known_machine"
    elif current_is_signed and not signed_slot_machine_conflict:
        # A signed slot is the final effective slot. Keep it stable even if an
        # OTA repairs/recreates the local machine identity later.
        installation_id = current_installation_id
        duplicate_before = False
        signed = True
        signature_reason = "already_signed"
    else:
        preferred_id = device_id if body.force_new or not current_installation_id else current_installation_id
        # A pre-machine-identity client may have left one old slot behind.
        # When the freshly installed client presents a new id, recover that
        # sole long-unused slot so existing workflows keep receiving work.
        if (
            not body.force_new
            and has_machine_identity
            and current_installation_id
            and device_id == current_installation_id
        ):
            stale_slot = _stale_single_slot_for_rebind(
                db,
                user_id=current_user.id,
                current_installation_id=current_installation_id,
                now=datetime.utcnow(),
            )
        if stale_slot:
            preferred_id = stale_slot
        usage_before = _installation_id_usage(
            db,
            preferred_id,
            brand_mark=brand_mark,
            exclude_user_id=current_user.id,
        )
        same_user_machine_conflict = bool(
            has_machine_identity
            and not stale_slot
            and db.query(UserMachineIdentity)
            .filter(
                UserMachineIdentity.user_id == current_user.id,
                UserMachineIdentity.installation_id == preferred_id,
                UserMachineIdentity.machine_instance_id != machine_id,
            )
            .first()
        )
        duplicate_before = bool(
            usage_before.get("taken")
            or same_user_machine_conflict
            or signed_slot_machine_conflict
        )
        if duplicate_before:
            installation_id = _unique_signed_installation_id_for_user(db, current_user, device_id, brand_mark, machine_id)
            signed = True
            signature_reason = (
                "duplicate_machine"
                if (same_user_machine_conflict or signed_slot_machine_conflict)
                and not usage_before.get("taken")
                else "duplicate"
            )
        else:
            installation_id = preferred_id
            signed = False
            signature_reason = ""
    # Splitting a copied online slot is allocation, not migration.  Keep the
    # original device's tasks/templates untouched.
    replaced = (
        current_installation_id
        if current_installation_id
        and current_installation_id != installation_id
        and not signed_slot_machine_conflict
        else ""
    )

    migrated = {}
    if replaced:
        migrated = migrate_installation_slot_references(
            db,
            user_id=current_user.id,
            previous_installation_id=replaced,
            installation_id=installation_id,
        )

    scoped_id = scoped_installation_id(installation_id, brand_mark) or installation_id
    ensure_installation_slot(db, current_user.id, scoped_id)
    claim = claim_installation_slot(
        db,
        user_id=current_user.id,
        installation_id=installation_id,
        brand_mark=brand_mark,
        auth_session_id=request_auth_session_id(request),
    )
    if known_machine is None:
        if stale_slot:
            # Retire the pre-reinstall machine mapping so the old installation
            # cannot reclaim this slot when it comes back online later.
            db.query(UserMachineIdentity).filter(
                UserMachineIdentity.user_id == current_user.id,
                UserMachineIdentity.installation_id == installation_id,
                UserMachineIdentity.machine_instance_id != machine_id,
            ).delete(synchronize_session=False)
        known_machine = UserMachineIdentity(
            user_id=current_user.id,
            machine_instance_id=machine_id,
            installation_id=installation_id,
            created_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
        )
        db.add(known_machine)
    else:
        known_machine.installation_id = installation_id
        known_machine.last_seen_at = datetime.utcnow()
    db.commit()
    usage_after = _installation_id_usage(
        db,
        installation_id,
        brand_mark=brand_mark,
        exclude_user_id=current_user.id,
    )
    logger.info(
        "[installation-identity] bind user=%s current=%s device_prefix=%s machine=%s returned=%s reason=%s replaced=%s",
        getattr(current_user, "id", ""),
        current_installation_id or "",
        str(device_id or "")[:12],
        machine_id or "",
        installation_id,
        signature_reason,
        replaced or "",
    )
    return {
        "ok": True,
        "installation_id": installation_id,
        "device_id": device_id,
        "machine_instance_id": machine_id,
        "scoped_installation_id": scoped_id,
        "replaced_installation_id": replaced,
        "signed": signed,
        "signature_reason": signature_reason,
        "migrated": migrated,
        "duplicate": bool(duplicate_before or usage_after.get("taken")),
        "duplicate_user_count": len(usage_after.get("user_ids") or []),
        "presence_user_count": len(usage_after.get("presence_user_ids") or []),
        "claim": claim,
    }


def _get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class UpdateSettingsRequest(BaseModel):
    preferred_model: Optional[str] = None
    language: Optional[str] = None


@router.get("/api/settings", summary="获取用户设置")
def get_settings(current_user: User = Depends(get_current_user)):
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    if edition == "online":
        preferred = "sutui"
    else:
        preferred = getattr(current_user, "preferred_model", "openclaw") or "openclaw"
    return {
        "preferred_model": preferred,
        "language": str(getattr(current_user, "language", None) or "zh-CN"),
    }


@router.post("/api/settings", summary="更新用户设置")
def update_settings(
    body: UpdateSettingsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.preferred_model is not None:
        current_user.preferred_model = body.preferred_model.strip() or "openclaw"
    if body.language is not None:
        language = body.language.strip()
        if language not in {"zh-CN", "en-US"}:
            raise HTTPException(status_code=400, detail="language must be zh-CN or en-US")
        current_user.language = language
    db.commit()
    return {
        "preferred_model": current_user.preferred_model,
        "language": str(getattr(current_user, "language", None) or "zh-CN"),
    }


@router.get("/api/settings/models", summary="可选模型列表（需登录）")
def list_models(current_user: User = Depends(get_current_user)):
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    if edition == "online":
        return {
            "models": [
                {
                    "id": "sutui_aggregate",
                    "name": "速推聚合",
                    "description": "速推多模型；进入智能会话后在子下拉选择具体模型",
                }
            ]
        }

    base_dir = Path(__file__).resolve().parent.parent.parent.parent
    models = []

    config_path = base_dir / "models_config.json"
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            models = data.get("models", [])
        except Exception:
            pass
    if not models:
        models = [
            {"id": "openclaw", "name": "默认 (OpenClaw)", "description": "OpenClaw 默认路由"},
            {"id": "anthropic/claude-sonnet-4-5", "name": "Claude Sonnet 4.5", "description": "Anthropic 快速模型"},
            {"id": "openai/gpt-4o", "name": "GPT-4o", "description": "OpenAI 多模态模型"},
            {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat", "description": "DeepSeek 对话模型"},
        ]

    existing_ids = {m.get("id") for m in models}

    custom_path = base_dir / "custom_configs.json"
    if custom_path.exists():
        try:
            custom_data = json.loads(custom_path.read_text(encoding="utf-8"))
            for cm in custom_data.get("custom_models", []):
                mid = cm.get("model_id", "")
                if mid and mid not in existing_ids:
                    models.append({
                        "id": mid,
                        "name": cm.get("display_name") or mid,
                        "description": cm.get("provider", "自定义模型"),
                        "custom": True,
                    })
                    existing_ids.add(mid)
        except Exception:
            pass

    return {"models": models}


@router.get(
    "/api/settings/tos-config",
    summary="TOS 上传模式状态（需登录）",
)
def get_tos_config_for_online_client(current_user: User = Depends(get_current_user)):
    """Return server-side upload status and keep old online clients compatible."""
    cfg = _read_server_tos_config_dict()
    if not cfg:
        raise HTTPException(
            status_code=404,
            detail="服务器未在 custom_configs.json 中配置有效 TOS_CONFIG（需 access_key/secret_key 等）",
        )
    return {
        "ok": True,
        "mode": "server-side-upload",
        "tos_configured": True,
        "bucket_name": str(cfg.get("bucket_name") or ""),
        "public_domain": str(cfg.get("public_domain") or ""),
        "TOS_CONFIG": cfg,
    }


@router.get("/api/settings/lan-info", summary="获取局域网访问信息（需登录）")
def get_lan_info(current_user: User = Depends(get_current_user)):
    ip = _get_lan_ip()
    port = getattr(settings, "port", 8000)
    return {
        "lan_ip": ip,
        "port": port,
        "url": f"http://{ip}:{port}",
    }
