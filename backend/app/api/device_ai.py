# -*- coding: utf-8 -*-
"""设备侧 AI 调度接口（ESP32 等嵌入式设备通过设备 token 调用）。

鉴权：设备绑定后拿到 device_token，请求头 Authorization: Bearer <device_token>。
所有接口都在设备所属用户（绑定它的账号）名下执行，等于用户本人在 AI 调度助手里说话。
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
import uuid
from datetime import timedelta
from pathlib import Path
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import H5ChatMessage, H5ChatSession, User
from .auth import create_access_token
from .installation_slots import optional_installation_id_from_request

router = APIRouter(prefix="/api/device", tags=["device-ai"])
logger = logging.getLogger("device.ai")

_H5_STATIC_DIR = Path(__file__).resolve().parents[3] / "h5_static"
_DEVICE_UPLOAD_DIR = _H5_STATIC_DIR / "device-audio"
_MAX_AUDIO_BYTES = 20 * 1024 * 1024

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS device_commands (
        id SERIAL PRIMARY KEY,
        device_id VARCHAR(128) NOT NULL,
        user_id INTEGER NOT NULL,
        kind VARCHAR(32) DEFAULT 'text',
        payload TEXT DEFAULT '',
        status VARCHAR(16) DEFAULT 'queued',
        result TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT NOW(),
        sent_at TIMESTAMP NULL,
        acked_at TIMESTAMP NULL
    )
    """,
    "ALTER TABLE bound_devices ADD COLUMN IF NOT EXISTS device_token_hash VARCHAR(128) DEFAULT ''",
    "ALTER TABLE bound_devices ADD COLUMN IF NOT EXISTS device_token_hint VARCHAR(24) DEFAULT ''",
)
_ready = False


def ensure_tables(db: Session) -> None:
    global _ready
    if _ready:
        return
    for ddl in _DDL:
        db.execute(text(ddl))
    db.commit()
    _ready = True


def issue_device_token(db: Session, device_id: str) -> str:
    """给设备发一个长期 token（只在绑定时返回一次），库里只存 sha256。"""
    ensure_tables(db)
    token = "dev_" + secrets.token_urlsafe(32)
    hint = token[:14] + "…"
    db.execute(
        text("UPDATE bound_devices SET device_token_hash = :h, device_token_hint = :n, updated_at = NOW()"
             " WHERE device_id = :d"),
        {"h": hashlib.sha256(token.encode("utf-8")).hexdigest(), "n": hint, "d": device_id},
    )
    db.commit()
    return token


def _bearer(request: Request) -> str:
    raw = str(request.headers.get("authorization") or "")
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return str(request.headers.get("x-device-token") or "").strip()


def current_device(request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    ensure_tables(db)
    token = _bearer(request)
    if not token:
        raise HTTPException(status_code=401, detail="缺少设备 token")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = db.execute(
        text("SELECT device_id, user_id, name, protocol_version, api_base_url FROM bound_devices"
             " WHERE device_token_hash = :h"),
        {"h": digest},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="设备 token 无效")
    dev = dict(row._mapping)
    db.execute(text("UPDATE bound_devices SET last_seen_at = NOW() WHERE device_id = :d"), {"d": dev["device_id"]})
    db.commit()
    return dev


def _owner(db: Session, device: Dict[str, Any]) -> User:
    user = db.query(User).filter(User.id == int(device["user_id"])).first()
    if not user:
        raise HTTPException(status_code=401, detail="设备所属账号不存在")
    return user


def _public_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


# ---------------- 1) 文本提交（= AI 调度助手发消息） ----------------
@router.post("/message", summary="设备提交文本给 AI 调度助手")
def device_message(
    request: Request,
    payload: Dict[str, Any],
    device: Dict[str, Any] = Depends(current_device),
    db: Session = Depends(get_db),
):
    text_in = str((payload or {}).get("text") or "").strip()
    if not text_in:
        raise HTTPException(status_code=400, detail="缺少 text")
    session_id = str((payload or {}).get("session_id") or "").strip()
    reply = _submit_to_orchestrator(request, db, device, text_in, session_id)
    return {"ok": True, **reply}


def _submit_to_orchestrator(request: Request, db: Session, device: Dict[str, Any], content: str,
                            session_id: str = "") -> Dict[str, Any]:
    """把设备文本塞进 AI 调度助手的同一条管线（内部用所属用户的短期 token）。"""
    from .mastra_chat import MastraMessageCreate, create_mastra_message

    owner = _owner(db, device)
    internal_token = create_access_token(
        {"sub": str(owner.id), "email": getattr(owner, "email", "") or ""},
        expires_delta=timedelta(minutes=10),
    )
    claims = {"authorization": "Bearer " + internal_token}
    request._headers = None  # 不修改原始请求；仅用于下方透传
    result = create_mastra_message(
        body=MastraMessageCreate(content=content, session_id=session_id or "", installation_id=None),
        request=request,
        current_user=owner,
        db=db,
    )
    message = (result or {}).get("message") or {}
    return {
        "message_id": message.get("id") or "",
        "session_id": message.get("session_id") or session_id,
        "status": message.get("status") or "pending",
        "internal_token": internal_token,
        "events": (result or {}).get("events") or [],
    }


# ---------------- 2) 回复查询（长轮询） ----------------
@router.get("/message/{message_id}", summary="查询设备消息的回复")
def device_message_status(
    message_id: str,
    wait: int = Query(0, ge=0, le=60, description="长轮询秒数，0=立即返回"),
    device: Dict[str, Any] = Depends(current_device),
    db: Session = Depends(get_db),
):
    deadline = time.time() + max(0, int(wait))
    while True:
        row = db.query(H5ChatMessage).filter(H5ChatMessage.id == str(message_id)).first()
        if not row or int(row.user_id) != int(device["user_id"]):
            raise HTTPException(status_code=404, detail="消息不存在")
        status = str(row.status or "")
        reply_text = str(row.reply_text or "")
        if reply_text or status in ("completed", "failed", "error"):
            return {
                "ok": True,
                "message_id": row.id,
                "status": status,
                "reply_text": reply_text,
                "reply_audio_url": getattr(row, "reply_audio_url", "") or "",
            }
        if time.time() >= deadline:
            return {"ok": True, "message_id": row.id, "status": status or "pending", "reply_text": "", "timeout": True}
        db.commit()
        time.sleep(1.0)


# ---------------- 3) 音频提交（上传 -> 转写 -> 送 AI） ----------------
@router.post("/audio", summary="设备上传音频：服务端转写后交给 AI 调度助手")
async def device_audio(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Form(""),
    transcribe_only: int = Form(0),
    device: Dict[str, Any] = Depends(current_device),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="音频为空")
    if len(raw) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="音频过大（上限 20MB）")
    suffix = Path(file.filename or "").suffix.lower() or ".wav"
    if suffix not in (".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".amr", ".pcm", ".silk"):
        suffix = ".wav"
    _DEVICE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = "dev_%s_%s%s" % (int(device["user_id"]), uuid.uuid4().hex[:12], suffix)
    path = _DEVICE_UPLOAD_DIR / name
    path.write_bytes(raw)
    audio_url = _public_base(request) + "/h5-static/device-audio/" + name
    text_out = transcribe_audio_url(db, int(device["user_id"]), audio_url)
    if not text_out:
        raise HTTPException(status_code=502, detail="转写失败或结果为空")
    if transcribe_only:
        return {"ok": True, "text": text_out, "audio_url": audio_url}
    reply = _submit_to_orchestrator(request, db, device, text_out, session_id)
    return {"ok": True, "text": text_out, "audio_url": audio_url, **reply}


def transcribe_audio_url(db: Session, user_id: int, audio_url: str) -> str:
    """复用速推 STT（cutcli_templates 里的私有流程）把公网音频转成文本。"""
    from .cutcli_templates import _load_sutui_token_for_stt, _stt_create_task, _stt_poll_task

    token, _source = _load_sutui_token_for_stt(db, int(user_id))
    db.commit()
    job_dir = _DEVICE_UPLOAD_DIR / "stt"
    job_dir.mkdir(parents=True, exist_ok=True)
    created = _stt_create_task(token, audio_url, job_dir=job_dir)
    stt_data = _stt_poll_task(token, created["task_id"], job_dir=job_dir)
    return extract_stt_text(stt_data)


def extract_stt_text(stt_data: Any) -> str:
    data = stt_data.get("data") if isinstance(stt_data, dict) and isinstance(stt_data.get("data"), dict) else stt_data
    if not isinstance(data, dict):
        return ""
    for key in ("text", "result", "transcription", "full_text", "asr_text"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("utterances", "sentences", "segments"):
        items = data.get(key)
        if isinstance(items, list):
            parts = []
            for item in items:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("sentence") or ""))
                elif isinstance(item, str):
                    parts.append(item)
            joined = "".join(parts).strip()
            if joined:
                return joined
    return ""


# ---------------- 4) 命令下发（AI -> 设备，设备轮询） ----------------
@router.get("/commands", summary="设备轮询待执行命令")
def device_commands_poll(
    wait: int = Query(0, ge=0, le=55, description="长轮询秒数"),
    device: Dict[str, Any] = Depends(current_device),
    db: Session = Depends(get_db),
):
    deadline = time.time() + max(0, int(wait))
    while True:
        rows = db.execute(
            text("SELECT id, kind, payload, created_at FROM device_commands"
                 " WHERE device_id = :d AND status = 'queued' ORDER BY id ASC LIMIT 10"),
            {"d": device["device_id"]},
        ).fetchall()
        if rows:
            ids = [int(r._mapping["id"]) for r in rows]
            db.execute(text("UPDATE device_commands SET status = 'sent', sent_at = NOW() WHERE id = ANY(:ids)"),
                       {"ids": ids})
            db.commit()
            return {"ok": True, "commands": [
                {"id": int(r._mapping["id"]), "kind": r._mapping["kind"],
                 "payload": _loads(r._mapping["payload"])} for r in rows]}
        if time.time() >= deadline:
            return {"ok": True, "commands": [], "timeout": True}
        time.sleep(1.0)


@router.post("/commands/{command_id}/ack", summary="设备回报命令执行结果")
def device_command_ack(
    command_id: int,
    payload: Dict[str, Any],
    device: Dict[str, Any] = Depends(current_device),
    db: Session = Depends(get_db),
):
    status = str((payload or {}).get("status") or "done").lower()
    if status not in ("done", "failed"):
        raise HTTPException(status_code=400, detail="status 必须是 done 或 failed")
    result = json.dumps((payload or {}).get("result") or {}, ensure_ascii=False)[:4000]
    updated = db.execute(
        text("UPDATE device_commands SET status = :s, result = :r, acked_at = NOW()"
             " WHERE id = :i AND device_id = :d"),
        {"s": status, "r": result, "i": int(command_id), "d": device["device_id"]},
    ).rowcount
    db.commit()
    if not updated:
        raise HTTPException(status_code=404, detail="命令不存在")
    return {"ok": True, "command_id": int(command_id), "status": status}


def enqueue_device_command(db: Session, *, device_id: str, user_id: int, kind: str = "text",
                           payload: Any = None) -> int:
    """给设备排队一条命令（AI 调度助手 / 后台都可以调）。"""
    ensure_tables(db)
    row = db.execute(
        text("INSERT INTO device_commands (device_id, user_id, kind, payload) VALUES (:d, :u, :k, :p) RETURNING id"),
        {"d": device_id, "u": int(user_id), "k": str(kind or "text")[:32],
         "p": json.dumps(payload or {}, ensure_ascii=False)[:8000]},
    ).fetchone()
    db.commit()
    return int(row[0])


# ---------------- 5) 心跳 ----------------
@router.post("/heartbeat", summary="设备心跳（更新在线时间）")
def device_heartbeat(
    payload: Dict[str, Any],
    device: Dict[str, Any] = Depends(current_device),
    db: Session = Depends(get_db),
):
    battery = (payload or {}).get("battery")
    db.execute(
        text("UPDATE bound_devices SET last_seen_at = NOW(), meta = :m WHERE device_id = :d"),
        {"d": device["device_id"], "m": json.dumps({"battery": battery}, ensure_ascii=False)},
    )
    db.commit()
    return {"ok": True, "server_time": int(time.time()), "device_id": device["device_id"]}


class DeviceDiagIn(BaseModel):
    lines: List[str] = Field(default_factory=list)
    build: str = ""
    ua: str = ""
    url: str = ""
    raw: str = ""


@router.post("/diag", summary="设备绑定页日志上报（排查用，无需登录）")
def device_diag(body: DeviceDiagIn, request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    import json as _json

    folder = Path(os.environ.get("LOBSTER_RUNTIME_DIR", "/tmp")) / "device_diag"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (datetime.utcnow().strftime("%Y%m%d") + ".jsonl")
        rec = {
            "ts": datetime.utcnow().isoformat(),
            "ip": request.client.host if request.client else "",
            "build": (body.build or "")[:64],
            "ua": (body.ua or "")[:300],
            "page": (body.url or "")[:300],
            "raw": (body.raw or "")[:500],
            "lines": [(x or "")[:300] for x in (body.lines or [])][-40:],
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


def _loads(raw: Any) -> Any:
    try:
        return json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
    except Exception:
        return raw
