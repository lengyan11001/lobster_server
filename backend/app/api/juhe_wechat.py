"""Whitelisted server-side JuheBot/WeChat protocol proxy.

The upstream GuidRequest endpoint is intentionally not exposed as a generic
proxy. Online clients only call the few product-level actions below.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import JuheWechatCallLog, JuheWechatConfig, User
from ..services.juhe_wechat import (
    guid_request,
    mask_secret,
    safe_request_snapshot,
)
from .auth import get_current_user

router = APIRouter()


def _normalize_guid(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="GUID cannot be empty")
    if len(value) > 96:
        raise HTTPException(status_code=400, detail="GUID is too long")
    return value


def _normalize_label(raw: Optional[str], guid: str) -> str:
    value = (raw or "").strip()
    if not value:
        value = "Wechat instance " + guid[-6:]
    return value[:120]


def _get_config_or_404(db: Session, user_id: int, config_id: int) -> JuheWechatConfig:
    row = (
        db.query(JuheWechatConfig)
        .filter(
            JuheWechatConfig.id == config_id,
            JuheWechatConfig.user_id == user_id,
            JuheWechatConfig.status != "deleted",
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Config not found")
    return row


def _config_out(row: JuheWechatConfig) -> Dict[str, Any]:
    return {
        "id": row.id,
        "label": row.label,
        "guid": row.guid,
        "has_app_key": bool((row.app_key or "").strip()),
        "has_app_secret": bool((row.app_secret or "").strip()),
        "has_custom_app_key": bool((row.app_key or "").strip()),
        "masked_app_key": mask_secret(row.app_key or "") if row.app_key else "",
        "uses_server_default_key": False,
        "status": row.status,
        "last_status": row.last_status,
        "last_status_at": row.last_status_at.isoformat() if row.last_status_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _log_call(
    db: Session,
    *,
    user_id: int,
    config_id: Optional[int],
    action: str,
    upstream_path: str,
    request_payload: Dict[str, Any],
    response_payload: Optional[Dict[str, Any]],
    http_status: Optional[int],
    latency_ms: Optional[int],
    success: bool,
    error_message: str = "",
) -> None:
    db.add(
        JuheWechatCallLog(
            user_id=user_id,
            config_id=config_id,
            action=action,
            upstream_path=upstream_path,
            success=success,
            http_status=http_status,
            latency_ms=latency_ms,
            request_payload=safe_request_snapshot(request_payload),
            response_payload=response_payload,
            error_message=error_message[:2000] if error_message else None,
        )
    )


class ConfigUpsertBody(BaseModel):
    id: Optional[int] = None
    label: Optional[str] = None
    guid: str
    app_key: Optional[str] = None
    app_secret: Optional[str] = None


class SendTextBody(BaseModel):
    config_id: int
    to_username: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=4000)


@router.get("/api/juhe-wechat/configs")
def list_configs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(JuheWechatConfig)
        .filter(JuheWechatConfig.user_id == current_user.id, JuheWechatConfig.status != "deleted")
        .order_by(JuheWechatConfig.created_at.desc())
        .all()
    )
    return {
        "configs": [_config_out(r) for r in rows],
        "server_default_ready": False,
    }


@router.post("/api/juhe-wechat/configs")
def save_config(
    body: ConfigUpsertBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    guid = _normalize_guid(body.guid)
    is_update = bool(body.id)
    if is_update:
        row = _get_config_or_404(db, current_user.id, int(body.id))
    else:
        row = (
            db.query(JuheWechatConfig)
            .filter(JuheWechatConfig.user_id == current_user.id, JuheWechatConfig.guid == guid)
            .first()
        )
        if row and row.status == "deleted":
            row.status = "active"
        if row is None:
            row = JuheWechatConfig(user_id=current_user.id, guid=guid, label=_normalize_label(body.label, guid))
            db.add(row)
    row.guid = guid
    row.label = _normalize_label(body.label, guid)

    app_key = (body.app_key or "").strip() if body.app_key is not None else ""
    app_secret = (body.app_secret or "").strip() if body.app_secret is not None else ""
    if not is_update or app_key:
        row.app_key = app_key or None
    if not is_update or app_secret:
        row.app_secret = app_secret or None
    if not (row.app_key or "").strip() or not (row.app_secret or "").strip():
        raise HTTPException(status_code=400, detail="App Key/App Secret are required for this instance")

    row.status = "active"
    db.commit()
    db.refresh(row)
    return {"ok": True, "config": _config_out(row)}


@router.delete("/api/juhe-wechat/configs/{config_id}")
def delete_config(
    config_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _get_config_or_404(db, current_user.id, config_id)
    row.status = "deleted"
    db.commit()
    return {"ok": True}


@router.post("/api/juhe-wechat/configs/{config_id}/status")
async def check_status(
    config_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _get_config_or_404(db, current_user.id, config_id)
    payload = {"guid": row.guid}
    try:
        data, http_status, latency_ms = await guid_request(
            path="/client/get_client_status",
            data=payload,
            config=row,
        )
        success = http_status == 200 and int(data.get("errcode") or 0) == 0
        status_value = None
        if success and isinstance(data.get("data"), dict):
            try:
                status_value = int(data["data"].get("status"))
            except Exception:
                status_value = None
        row.last_status = status_value
        row.last_status_at = datetime.utcnow()
        _log_call(
            db,
            user_id=current_user.id,
            config_id=row.id,
            action="status",
            upstream_path="/client/get_client_status",
            request_payload=payload,
            response_payload=data,
            http_status=http_status,
            latency_ms=latency_ms,
            success=success,
            error_message="" if success else str(data)[:500],
        )
        db.commit()
        return {
            "ok": success,
            "status": status_value,
            "status_label": {0: "\u505c\u6b62", 1: "\u8fd0\u884c", 2: "\u5728\u7ebf"}.get(status_value, "\u672a\u77e5"),
            "upstream": data,
            "latency_ms": latency_ms,
        }
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        _log_call(
            db,
            user_id=current_user.id,
            config_id=row.id,
            action="status",
            upstream_path="/client/get_client_status",
            request_payload=payload,
            response_payload=None,
            http_status=None,
            latency_ms=None,
            success=False,
            error_message=str(exc),
        )
        db.commit()
        raise HTTPException(status_code=502, detail=f"Juhe WeChat status query failed: {exc}") from exc


@router.post("/api/juhe-wechat/send-text")
async def send_text(
    body: SendTextBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _get_config_or_404(db, current_user.id, body.config_id)
    payload = {
        "guid": row.guid,
        "to_username": body.to_username.strip(),
        "content": body.content.strip(),
    }
    try:
        data, http_status, latency_ms = await guid_request(
            path="/msg/send_text",
            data=payload,
            config=row,
            timeout_seconds=45,
        )
        success = http_status == 200 and int(data.get("errcode") or 0) == 0
        _log_call(
            db,
            user_id=current_user.id,
            config_id=row.id,
            action="send_text",
            upstream_path="/msg/send_text",
            request_payload=payload,
            response_payload=data,
            http_status=http_status,
            latency_ms=latency_ms,
            success=success,
            error_message="" if success else str(data)[:500],
        )
        db.commit()
        if not success:
            raise HTTPException(status_code=502, detail=data.get("errmsg") or data.get("message") or "Send failed")
        return {"ok": True, "upstream": data, "latency_ms": latency_ms}
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        _log_call(
            db,
            user_id=current_user.id,
            config_id=row.id,
            action="send_text",
            upstream_path="/msg/send_text",
            request_payload=payload,
            response_payload=None,
            http_status=None,
            latency_ms=None,
            success=False,
            error_message=str(exc),
        )
        db.commit()
        raise HTTPException(status_code=502, detail=f"Juhe WeChat send failed: {exc}") from exc


@router.get("/api/juhe-wechat/call-logs")
def list_call_logs(
    config_id: Optional[int] = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    limit = max(1, min(int(limit or 50), 100))
    q = db.query(JuheWechatCallLog).filter(JuheWechatCallLog.user_id == current_user.id)
    if config_id:
        q = q.filter(JuheWechatCallLog.config_id == config_id)
    rows = q.order_by(JuheWechatCallLog.created_at.desc()).limit(limit).all()
    return {
        "items": [
            {
                "id": r.id,
                "config_id": r.config_id,
                "action": r.action,
                "upstream_path": r.upstream_path,
                "success": r.success,
                "http_status": r.http_status,
                "latency_ms": r.latency_ms,
                "request_payload": r.request_payload,
                "response_payload": r.response_payload,
                "error_message": r.error_message,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }
