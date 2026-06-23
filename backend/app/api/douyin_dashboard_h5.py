from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import DouyinDashboardDeviceState, H5ChatDevicePresence, User
from .auth import get_current_user
from .installation_slots import INSTALLATION_ID_HEADER, ensure_installation_slot
from .mobile_identity import online_user_for_mobile_user

router = APIRouter()


class DouyinDashboardReportIn(BaseModel):
    payload: Dict[str, Any] = Field(default_factory=dict)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _header_installation_id(request: Request) -> str:
    return (
        request.headers.get(INSTALLATION_ID_HEADER)
        or request.headers.get("x-installation-id")
        or ""
    ).strip()


def _device_online_map(rows: List[H5ChatDevicePresence]) -> Dict[str, bool]:
    now = datetime.utcnow()
    result: Dict[str, bool] = {}
    for row in rows:
        iid = str(row.installation_id or "").strip()
        if not iid:
            continue
        age = (now - row.last_seen_at).total_seconds() if row.last_seen_at else 999999
        result[iid] = age <= 20
    return result


def _default_payload() -> Dict[str, Any]:
    return {
        "accounts": [],
        "runtime": {
            "comment_message": "",
            "interaction_message": "",
            "monitor_message": "",
        },
        "metrics": {
            "collected_videos": 0,
            "all_customers": 0,
            "precise_customers": 0,
            "commented_videos": 0,
            "private_messages_sent": 0,
            "monitor_tasks": 0,
            "today_new_customers": 0,
            "today_task_runs": 0,
        },
        "updated_at": "",
    }


@router.post("/api/douyin/dashboard-status/report", summary="本地 online 上报抖音获客工作台状态")
def report_douyin_dashboard_status(
    body: DouyinDashboardReportIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    installation_id = _header_installation_id(request)
    if not installation_id:
        raise HTTPException(status_code=400, detail="缺少 X-Installation-Id")
    ensure_installation_slot(db, current_user.id, installation_id)
    now = datetime.utcnow()
    payload = body.payload if isinstance(body.payload, dict) else {}
    row = (
        db.query(DouyinDashboardDeviceState)
        .filter(
            DouyinDashboardDeviceState.user_id == current_user.id,
            DouyinDashboardDeviceState.installation_id == installation_id,
        )
        .first()
    )
    if row:
        row.payload = payload
        row.updated_at = now
    else:
        row = DouyinDashboardDeviceState(
            user_id=current_user.id,
            installation_id=installation_id,
            payload=payload,
            updated_at=now,
            created_at=now,
        )
        db.add(row)
    db.commit()
    return {"ok": True, "installation_id": installation_id, "updated_at": _iso(now)}


@router.get("/api/douyin/dashboard-status", summary="H5 查询抖音获客工作台状态")
def get_douyin_dashboard_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    device_rows = (
        db.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.user_id == owner_user.id)
        .order_by(H5ChatDevicePresence.last_seen_at.desc())
        .limit(20)
        .all()
    )
    state_rows = (
        db.query(DouyinDashboardDeviceState)
        .filter(DouyinDashboardDeviceState.user_id == owner_user.id)
        .order_by(DouyinDashboardDeviceState.updated_at.desc())
        .limit(20)
        .all()
    )
    device_online = _device_online_map(device_rows)

    preferred: Optional[DouyinDashboardDeviceState] = None
    for row in state_rows:
        iid = str(row.installation_id or "").strip()
        if iid and device_online.get(iid):
            preferred = row
            break
    if preferred is None and state_rows:
        preferred = state_rows[0]

    payload = _default_payload()
    if preferred and isinstance(preferred.payload, dict):
        payload.update(preferred.payload)

    raw_accounts = payload.get("accounts")
    accounts: List[Dict[str, Any]] = []
    if isinstance(raw_accounts, list):
        for item in raw_accounts:
            if not isinstance(item, dict):
                continue
            installation_id = str(item.get("installation_id") or (preferred.installation_id if preferred else "") or "").strip()
            accounts.append(
                {
                    "account_id": item.get("account_id"),
                    "nickname": str(item.get("nickname") or item.get("name") or "").strip(),
                    "status": str(item.get("status") or ("active" if item.get("online") else "offline")).strip(),
                    "online": bool(item.get("online")) if "online" in item else bool(device_online.get(installation_id)),
                    "installation_id": installation_id,
                    "last_login": str(item.get("last_login") or "").strip(),
                }
            )

    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    updated_at = str(payload.get("updated_at") or (preferred.updated_at.isoformat() if preferred else "")).strip()

    return {
        "ok": True,
        "accounts": accounts,
        "runtime": {
            "comment_message": str(runtime.get("comment_message") or "").strip(),
            "interaction_message": str(runtime.get("interaction_message") or "").strip(),
            "monitor_message": str(runtime.get("monitor_message") or "").strip(),
        },
        "metrics": {
            "collected_videos": int(metrics.get("collected_videos") or 0),
            "all_customers": int(metrics.get("all_customers") or 0),
            "precise_customers": int(metrics.get("precise_customers") or 0),
            "commented_videos": int(metrics.get("commented_videos") or 0),
            "private_messages_sent": int(metrics.get("private_messages_sent") or 0),
            "monitor_tasks": int(metrics.get("monitor_tasks") or 0),
            "today_new_customers": int(metrics.get("today_new_customers") or 0),
            "today_task_runs": int(metrics.get("today_task_runs") or 0),
        },
        "updated_at": updated_at,
        "installation_id": str(preferred.installation_id or "").strip() if preferred else "",
    }
