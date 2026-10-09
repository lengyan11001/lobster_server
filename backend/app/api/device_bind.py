# -*- coding: utf-8 -*-
"""设备绑定（扫码配网）接口。

- POST /api/device/bind-ticket    App 用当前登录态换一次性绑定票据
- POST /api/device/report         设备凭票据回报 device_id/name（建立绑定关系）
- GET  /api/device/bind/status     查询绑定状态（支持 ticket 或 device_id）
- GET  /api/device/list            App 查看自己绑定的设备
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from .auth import get_current_user
from ..models import User

router = APIRouter(prefix="/api/device", tags=["device-bind"])
logger = logging.getLogger("device.bind")

TICKET_TTL_SECONDS = 300  # 一次性票据 5 分钟

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS device_bind_tickets (
        id SERIAL PRIMARY KEY,
        ticket VARCHAR(64) UNIQUE NOT NULL,
        user_id INTEGER NOT NULL,
        brand VARCHAR(32) DEFAULT '',
        api_base_url TEXT DEFAULT '',
        expires_at BIGINT NOT NULL,
        used_at TIMESTAMP NULL,
        device_id VARCHAR(128) DEFAULT '',
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS device_pair_sessions (
        id SERIAL PRIMARY KEY,
        code VARCHAR(64) UNIQUE NOT NULL,
        user_id INTEGER NOT NULL,
        ticket VARCHAR(64) DEFAULT '',
        device_id VARCHAR(128) DEFAULT '',
        status VARCHAR(16) DEFAULT 'pending',
        expires_at BIGINT NOT NULL,
        claimed_at TIMESTAMP DEFAULT NOW(),
        consumed_at TIMESTAMP NULL,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bound_devices (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL,
        device_id VARCHAR(128) UNIQUE NOT NULL,
        name VARCHAR(160) DEFAULT '',
        protocol_version INTEGER DEFAULT 1,
        bind_status VARCHAR(16) DEFAULT 'bound',
        api_base_url TEXT DEFAULT '',
        last_ip VARCHAR(64) DEFAULT '',
        meta TEXT DEFAULT '',
        bound_at TIMESTAMP DEFAULT NOW(),
        last_seen_at TIMESTAMP DEFAULT NOW(),
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """,
)

_tables_ready = False


def ensure_tables(db: Session) -> None:
    global _tables_ready
    if _tables_ready:
        return
    for ddl in _DDL:
        db.execute(text(ddl))
    db.commit()
    _tables_ready = True


def _now() -> float:
    return time.time()


def _brand_of(request: Optional[Request]) -> str:
    if request is None:
        return ""
    return str(request.headers.get("X-Lobster-Brand") or "").strip().lower()


def _client_ip(request: Optional[Request]) -> str:
    if request is None:
        return ""
    fwd = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if fwd:
        return fwd
    return str(getattr(request.client, "host", "") or "")


def _api_base(request: Optional[Request], override: str = "") -> str:
    value = str(override or "").strip().rstrip("/")
    if value:
        return value
    if request is None:
        return ""
    return str(request.base_url).rstrip("/")


class BindTicketIn(BaseModel):
    api_base_url: str = ""


class DeviceReportIn(BaseModel):
    ticket: str
    device_id: str
    name: str = ""
    protocol_version: int = 1
    api_base_url: str = ""
    meta: Dict[str, Any] = {}


@router.post("/bind-ticket", summary="App：换一次性设备绑定票据")
def create_bind_ticket(
    request: Request,
    body: Optional[BindTicketIn] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tables(db)
    ticket = "bt_" + secrets.token_urlsafe(24)
    expires_at = int(_now()) + TICKET_TTL_SECONDS
    api_base = _api_base(request, (body.api_base_url if body else "") or "")
    db.execute(
        text(
            "INSERT INTO device_bind_tickets (ticket, user_id, brand, api_base_url, expires_at)"
            " VALUES (:t, :u, :b, :a, :e)"
        ),
        {"t": ticket, "u": int(current_user.id), "b": _brand_of(request), "a": api_base, "e": expires_at},
    )
    db.commit()
    logger.info("[device] bind-ticket issued uid=%s expires_at=%s", current_user.id, expires_at)
    return {
        "ok": True,
        "ticket": ticket,
        "expires_at": expires_at,
        "expires_in": TICKET_TTL_SECONDS,
        "api_base_url": api_base,
    }


@router.post("/report", summary="设备：凭票据回报并建立绑定")
def device_report(
    body: DeviceReportIn,
    request: Request,
    db: Session = Depends(get_db),
):
    ensure_tables(db)
    ticket = str(body.ticket or "").strip()
    device_id = str(body.device_id or "").strip()
    if not ticket or not device_id:
        raise HTTPException(status_code=400, detail="缺少 ticket 或 device_id")
    row = db.execute(
        text("SELECT id, user_id, api_base_url, expires_at, used_at FROM device_bind_tickets WHERE ticket = :t"),
        {"t": ticket},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="票据不存在")
    m = dict(row._mapping)
    if int(m["expires_at"] or 0) < int(_now()):
        raise HTTPException(status_code=410, detail="票据已过期，请重新扫码")
    user_id = int(m["user_id"])
    api_base = str(body.api_base_url or m.get("api_base_url") or "").rstrip("/")
    name = str(body.name or "")
    protocol_version = int(body.protocol_version or 1)
    db.execute(
        text(
            """
            INSERT INTO bound_devices (user_id, device_id, name, protocol_version, bind_status,
                                       api_base_url, last_ip, meta, bound_at, last_seen_at, updated_at)
            VALUES (:u, :d, :n, :pv, 'bound', :a, :ip, :meta, NOW(), NOW(), NOW())
            ON CONFLICT (device_id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                name = EXCLUDED.name,
                protocol_version = EXCLUDED.protocol_version,
                bind_status = 'bound',
                api_base_url = EXCLUDED.api_base_url,
                last_ip = EXCLUDED.last_ip,
                meta = EXCLUDED.meta,
                last_seen_at = NOW(),
                updated_at = NOW()
            """
        ),
        {
            "u": user_id, "d": device_id, "n": name, "pv": protocol_version, "a": api_base,
            "ip": _client_ip(request), "meta": json.dumps(body.meta or {}, ensure_ascii=False)[:4000],
        },
    )
    db.execute(
        text("UPDATE device_bind_tickets SET used_at = NOW(), device_id = :d WHERE id = :i"),
        {"d": device_id, "i": int(m["id"])},
    )
    db.commit()
    # 绑定成功时下发长期设备 token（只返回这一次，库里只存 hash）
    from .device_ai import issue_device_token

    device_token = issue_device_token(db, device_id)
    logger.info("[device] bound uid=%s device=%s name=%s from=%s", user_id, device_id, name, _client_ip(request))
    return {
        "ok": True,
        "device_token": device_token,
        "device_id": device_id,
        "name": name,
        "user_id": user_id,
        "bind_status": "bound",
        "protocol_version": protocol_version,
        "api_base_url": api_base,
    }


@router.get("/bind/status", summary="查询绑定状态（ticket 或 device_id）")
def bind_status(
    ticket: str = Query("", description="绑定票据（设备/App 绑定后立即查询用）"),
    device_id: str = Query("", description="设备号"),
    db: Session = Depends(get_db),
):
    ensure_tables(db)
    ticket = str(ticket or "").strip()
    device_id = str(device_id or "").strip()
    if ticket:
        row = db.execute(
            text("SELECT device_id, user_id, expires_at, used_at FROM device_bind_tickets WHERE ticket = :t"),
            {"t": ticket},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="票据不存在")
        m = dict(row._mapping)
        device_id = str(m.get("device_id") or "").strip()
        if not device_id:
            expired = int(m["expires_at"] or 0) < int(_now())
            return {"device_id": "", "name": "", "bind_status": "expired" if expired else "waiting",
                    "protocol_version": 0}
    if not device_id:
        raise HTTPException(status_code=400, detail="缺少 ticket 或 device_id")
    dev = db.execute(
        text("SELECT device_id, name, bind_status, protocol_version FROM bound_devices WHERE device_id = :d"),
        {"d": device_id},
    ).fetchone()
    if not dev:
        return {"device_id": device_id, "name": "", "bind_status": "waiting", "protocol_version": 0}
    d = dict(dev._mapping)
    return {
        "device_id": d["device_id"],
        "name": d.get("name") or "",
        "bind_status": d.get("bind_status") or "bound",
        "protocol_version": int(d.get("protocol_version") or 1),
    }


@router.get("/list", summary="App：我绑定的设备列表")
def list_devices(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tables(db)
    rows = db.execute(
        text(
            "SELECT device_id, name, protocol_version, bind_status, api_base_url, last_ip,"
            " bound_at, last_seen_at FROM bound_devices WHERE user_id = :u ORDER BY last_seen_at DESC"
        ),
        {"u": int(current_user.id)},
    ).fetchall()
    return {"ok": True, "items": [dict(r._mapping) for r in rows]}

# ────────────────────────── 服务器中转绑定（2026-10-09） ──────────────────────────
# 手机不需要和设备在同一 WiFi：设备屏幕上显示 https://bhzn.top/pair?code=XXXX，
# 手机扫到后把票据挂到这个 code 上；设备自己轮询 /api/device/pair/poll 领走票据。


def _norm_code(raw: str) -> str:
    return "".join(ch for ch in str(raw or "").strip().upper() if ch.isalnum())[:64]


class PairClaimIn(BaseModel):
    code: str
    device_id: str = ""


@router.post("/pair/claim", summary="App：把绑定票据挂到设备二维码里的配对码上（服务器中转，无需同一 WiFi）")
def pair_claim(
    request: Request,
    body: PairClaimIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tables(db)
    code = _norm_code(body.code)
    if not code:
        raise HTTPException(status_code=400, detail="缺少配对码 code")
    ticket = "bt_" + secrets.token_urlsafe(24)
    expires_at = int(_now()) + TICKET_TTL_SECONDS
    api_base = _api_base(request)
    db.execute(
        text(
            "INSERT INTO device_bind_tickets (ticket, user_id, brand, api_base_url, expires_at)"
            " VALUES (:t, :u, :b, :a, :e)"
        ),
        {"t": ticket, "u": int(current_user.id), "b": _brand_of(request), "a": api_base, "e": expires_at},
    )
    db.execute(
        text(
            "INSERT INTO device_pair_sessions (code, user_id, ticket, device_id, status, expires_at, claimed_at, consumed_at)"
            " VALUES (:c, :u, :t, :d, 'pending', :e, NOW(), NULL)"
            " ON CONFLICT (code) DO UPDATE SET user_id = :u, ticket = :t, device_id = :d, status = 'pending',"
            " expires_at = :e, claimed_at = NOW(), consumed_at = NULL"
        ),
        {"c": code, "u": int(current_user.id), "t": ticket, "d": str(body.device_id or "")[:128], "e": expires_at},
    )
    db.commit()
    logger.info("[device] pair claim uid=%s code=%s", current_user.id, code[:8])
    return {
        "ok": True,
        "code": code,
        "ticket": ticket,
        "expires_at": expires_at,
        "expires_in": TICKET_TTL_SECONDS,
        "api_base_url": api_base,
    }


@router.get("/pair/poll", summary="设备：按配对码轮询领取绑定票据（无需 token）")
def pair_poll(
    request: Request,
    code: str,
    wait: int = 0,
    db: Session = Depends(get_db),
):
    ensure_tables(db)
    clean = _norm_code(code)
    if not clean:
        raise HTTPException(status_code=400, detail="缺少配对码 code")
    deadline = _now() + max(0, min(int(wait or 0), 25))
    api_base = _api_base(request)
    while True:
        row = db.execute(
            text("SELECT ticket, status, expires_at FROM device_pair_sessions WHERE code = :c"),
            {"c": clean},
        ).first()
        if row is None:
            return {"ok": True, "status": "unknown"}
        ticket, status, expires_at = str(row[0] or ""), str(row[1] or ""), int(row[2] or 0)
        if expires_at and expires_at < int(_now()):
            if status == "pending":
                db.execute(
                    text("UPDATE device_pair_sessions SET status = 'expired' WHERE code = :c AND status = 'pending'"),
                    {"c": clean},
                )
                db.commit()
            return {"ok": True, "status": "expired"}
        if status == "pending" and ticket:
            updated = db.execute(
                text(
                    "UPDATE device_pair_sessions SET status = 'consumed', consumed_at = NOW()"
                    " WHERE code = :c AND status = 'pending'"
                ),
                {"c": clean},
            )
            db.commit()
            if updated.rowcount:
                logger.info("[device] pair poll consumed code=%s", clean[:8])
                return {
                    "ok": True,
                    "status": "issued",
                    "bind_ticket": ticket,
                    "api_base_url": api_base,
                    "expires_at": expires_at,
                    "expires_in": max(0, expires_at - int(_now())),
                }
        if _now() >= deadline:
            return {"ok": True, "status": "consumed" if status == "consumed" else "waiting"}
        time.sleep(0.6)

