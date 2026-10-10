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
from datetime import datetime, timedelta
from pathlib import Path
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.credits_amount import credits_json_float
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


def public_base(request: Optional[Request] = None) -> str:
    """音频/文件给上游（火山 STT 等）拉取用的公网基址。

    必须是对外可访问的地址：nginx 反代时 request.base_url 往往是 127.0.0.1:8000，
    上游拿这个地址去下载音频必然失败（火山报 Invalid audio URI / audio download failed）。
    优先级：环境变量 PUBLIC_BASE_URL / LOBSTER_PUBLIC_BASE_URL → 转发头 → request.base_url。
    request 传 None（WS / 后台任务）时只走环境变量与 custom_configs，取不到返回 ""。
    """
    for key in ("PUBLIC_BASE_URL", "LOBSTER_PUBLIC_BASE_URL"):
        value = str(os.environ.get(key) or "").strip().rstrip("/")
        if value:
            return value
    try:
        cfg = _custom_public_base()
        if cfg:
            return cfg
    except Exception:  # noqa: BLE001
        pass
    if request is None:
        return ""
    host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or "").strip()
    proto = str(request.headers.get("x-forwarded-proto") or "https").split(",")[0].strip() or "https"
    if host and "127.0.0.1" not in host and "localhost" not in host:
        return "%s://%s" % (proto, host)
    return str(request.base_url).rstrip("/")


def public_base_from_headers(headers: Any, *, default: str = "") -> str:
    """WebSocket 等没有 Request 的场景：用连接 headers（x-forwarded-host / host）拼公网基址。"""
    configured = public_base(None)
    if configured:
        return configured
    host = ""
    proto = "https"
    try:
        host = str(headers.get("x-forwarded-host") or headers.get("host") or "").split(",")[0].strip()
        proto = str(headers.get("x-forwarded-proto") or "https").split(",")[0].strip() or "https"
    except Exception:  # noqa: BLE001
        host, proto = "", "https"
    if host and "127.0.0.1" not in host and "localhost" not in host:
        return "%s://%s" % (proto, host)
    return default


# 兼容旧调用名
_public_base = public_base


def device_audio_dir() -> Path:
    """设备音频落地目录（h5_static/device-audio），不存在时自动创建。"""
    _DEVICE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return _DEVICE_UPLOAD_DIR


def _custom_public_base() -> str:
    """从 custom_configs.json 里取 public_base_url（有就用，运维不用改代码换域名）。"""
    try:
        from ..custom_config import load_custom_configs  # type: ignore

        data = load_custom_configs() or {}
        for key in ("public_base_url", "PUBLIC_BASE_URL"):
            value = str((data.get("configs") or {}).get(key) or data.get(key) or "").strip().rstrip("/")
            if value:
                return value
    except Exception:  # noqa: BLE001
        return ""
    return ""


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
    want_tts = _as_flag((payload or {}).get("tts")) or _as_flag(request.query_params.get("tts"))
    reply = submit_to_orchestrator(request, db, device, text_in, session_id)
    if want_tts and reply.get("message_id"):
        _TTS_WANT[str(reply["message_id"])] = True
    return {"ok": True, **reply}


_TTS_WANT: Dict[str, bool] = {}


def _as_flag(value: Any) -> bool:
    """tts 开关：1/true/yes/on/y 视为开，其它（含 None、0、false、"0"）为关。"""
    text = str(value if value is not None else "").strip().lower()
    return text in ("1", "true", "yes", "on", "y")


def submit_to_orchestrator(request: Any, db: Session, device: Dict[str, Any], content: str,
                            session_id: str = "") -> Dict[str, Any]:
    """把设备文本塞进 AI 调度助手的同一条管线（内部用所属用户的短期 token）。"""
    from .mastra_chat import MastraMessageCreate, create_mastra_message

    owner = _owner(db, device)
    internal_token = create_access_token(
        {"sub": str(owner.id), "email": getattr(owner, "email", "") or ""},
        expires_delta=timedelta(minutes=10),
    )
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


# 兼容旧调用名
_submit_to_orchestrator = submit_to_orchestrator


# ---------------- 2) 回复查询（长轮询） ----------------
def _safe_audio_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isalnum() or ch in "-_")[:64]


def synth_and_store_audio(*, db: Session, user_id: int, text: str, cache_key: str,
                          description: str = "") -> Dict[str, Any]:
    """合成一段语音并落到 device-audio（按 cache_key 缓存），轮询回包与 WS 逐句语音共用。

    返回 {"audio_url", "credits", "cached"}；失败返回 {"error": "..."}（不抛异常）。
    费用从「绑定该设备的账号」扣，走 credit_ledger。
    """
    import urllib.request  # noqa: PLC0415

    from ..services import device_tts  # noqa: PLC0415

    clean = str(text or "").strip()
    if not clean:
        return {"error": "TTS 文本为空"}
    safe = _safe_audio_key(cache_key)
    if not safe:
        return {"error": "TTS 缓存键为空"}
    name = "tts_%s.mp3" % safe
    path = device_audio_dir() / name
    if path.exists() and path.stat().st_size > 0:
        return {"audio_url": "/api/device/audio/" + name, "credits": 0, "cached": True}
    owner = db.query(User).filter(User.id == int(user_id)).first()
    if owner is None:
        return {"error": "找不到归属账号"}
    try:
        from .cutcli_templates import _load_sutui_token_for_stt  # noqa: PLC0415

        token, _source = _load_sutui_token_for_stt(db, int(owner.id))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        return {"error": "取速推 token 失败：%s" % str(exc)[:120]}
    price = device_tts.price_for(clean)
    try:
        device_tts.ensure_balance(db, owner, price)
        audio_url = device_tts.synthesize(clean, token=token)
        with urllib.request.urlopen(audio_url, timeout=90) as resp:  # noqa: S310
            data = resp.read(20 * 1024 * 1024)
        if not data:
            raise HTTPException(status_code=502, detail="TTS 音频为空")
        path.write_bytes(data)
        charged = device_tts.charge(
            db, owner, price,
            ref_id=safe,
            description=description or ("设备语音(TTS) %d 字" % len(clean)),
        )
        db.commit()
        logger.info("[device] tts ok key=%s chars=%s credits=%s", safe, len(clean), charged)
        return {
            "audio_url": "/api/device/audio/" + name,
            "credits": credits_json_float(charged),
            "cached": False,
        }
    except HTTPException as exc:
        db.rollback()
        logger.warning("[device] tts failed key=%s: %s", safe, str(exc.detail)[:200])
        return {"error": str(exc.detail)[:200]}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("[device] tts error key=%s: %s", safe, str(exc)[:200])
        return {"error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}


def concat_audio_segments(*, keys: Any, out_key: str) -> str:
    """按顺序把已落盘的分句音频拼成一个「整段」音频（不再扣费），返回可访问路径。"""
    safe_out = _safe_audio_key(out_key)
    if not safe_out:
        return ""
    root = device_audio_dir()
    out_path = root / ("tts_%s.mp3" % safe_out)
    try:
        written = 0
        with open(out_path, "wb") as fh:
            for key in keys or []:
                safe = _safe_audio_key(key)
                if not safe:
                    continue
                part = root / ("tts_%s.mp3" % safe)
                if not part.is_file() or part.stat().st_size <= 0:
                    continue
                fh.write(part.read_bytes())
                written += 1
        if written and out_path.stat().st_size > 0:
            return "/api/device/audio/" + out_path.name
    except Exception as exc:  # noqa: BLE001
        logger.warning("[device] tts merge failed out=%s: %s", safe_out, str(exc)[:200])
    return ""


def _tts_payload_for_message(*, request, db: Session, row: Any, text: str) -> Dict[str, Any]:
    """按需合成设备语音回包：同一条消息只合成一次（文件缓存），失败不影响文本返回。"""
    result = synth_and_store_audio(
        db=db,
        user_id=int(row.user_id),
        text=text,
        cache_key=str(row.id),
        description="设备语音回包(TTS) 消息 #%s，%d 字" % (str(row.id)[:12], len(str(text or ""))),
    )
    if result.get("error"):
        return {"reply_audio_url": "", "tts_error": result["error"]}
    return {
        "reply_audio_url": result.get("audio_url") or "",
        "tts_credits": result.get("credits", 0),
        "tts_cached": bool(result.get("cached")),
    }


@router.get("/message/{message_id}", summary="查询设备消息的回复")
def device_message_status(
    message_id: str,
    wait: int = Query(0, ge=0, le=60, description="长轮询秒数，0=立即返回"),
    tts: int = Query(0, ge=0, le=1, description="1=需要语音回包（设备开关打开时带 1）"),
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
            payload: Dict[str, Any] = {
                "ok": True,
                "message_id": row.id,
                "status": status,
                "reply_text": reply_text,
                "reply_audio_url": getattr(row, "reply_audio_url", "") or "",
            }
            want_tts = bool(int(tts or 0)) or bool(_TTS_WANT.get(str(row.id)))
            if want_tts and reply_text and status == "completed":
                payload.update(_tts_payload_for_message(request=None, db=db, row=row, text=reply_text))
            return payload
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
    upload_dir = device_audio_dir()
    name = "dev_%s_%s%s" % (int(device["user_id"]), uuid.uuid4().hex[:12], suffix)
    path = upload_dir / name
    path.write_bytes(raw)
    audio_url = public_base(request) + "/api/device/audio/" + name
    text_out = transcribe_audio_url(db, int(device["user_id"]), audio_url)
    if not text_out:
        raise HTTPException(status_code=502, detail="转写失败或结果为空")
    if transcribe_only:
        return {"ok": True, "text": text_out, "audio_url": audio_url}
    try:
        reply = submit_to_orchestrator(request, db, device, text_out, session_id)
    except HTTPException as exc:
        logger.warning("[device] audio orchestrator HTTP %s: %s", exc.status_code, str(exc.detail)[:300])
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[device] audio orchestrator failed: %s: %s", type(exc).__name__, str(exc)[:400], exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="送 AI 调度失败：%s: %s" % (type(exc).__name__, str(exc)[:300]),
        ) from exc
    return {"ok": True, "text": text_out, "audio_url": audio_url, **reply}


@router.get("/audio/{name}", summary="设备音频文件（给上游 STT 拉取，无需 token）")
def device_audio_file(name: str):
    from fastapi.responses import FileResponse  # noqa: PLC0415

    safe = Path(str(name or "")).name
    if not safe or "/" in safe or ".." in safe:
        raise HTTPException(status_code=404, detail="文件不存在")
    root = _DEVICE_UPLOAD_DIR.resolve()
    path = (root / safe).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    media = "audio/wav"
    if safe.endswith(".mp3"):
        media = "audio/mpeg"
    elif safe.endswith(".m4a"):
        media = "audio/mp4"
    elif safe.endswith(".ogg") or safe.endswith(".opus"):
        media = "audio/ogg"
    return FileResponse(str(path), media_type=media, headers={"Cache-Control": "no-store"})


def _stt_error_text(exc: Any) -> str:
    """从上游异常里只抽真正的错误正文（output.error / result.error），避免被参数部分挤掉。"""
    raw = str(exc or "")
    data: Any = None
    try:
        start = raw.find("{")
        if start >= 0:
            data = json.loads(raw[start:])
    except Exception:  # noqa: BLE001
        data = None
    if isinstance(data, dict):
        for container in ("output", "result"):
            node = data.get(container)
            if isinstance(node, dict) and node.get("error"):
                return str(node["error"])[:300]
        if data.get("error"):
            return str(data["error"])[:300]
        if data.get("message"):
            return str(data["message"])[:300]
    return raw[:300]


def transcribe_audio_url(db: Session, user_id: int, audio_url: str) -> str:
    """复用速推 STT 把公网音频转成文本。

    失败时把上游原文塞进 502 的 detail（设备端日志能直接看到原因），并写一条服务器日志。
    """
    from .cutcli_templates import (  # noqa: PLC0415
        AutoCaptionJobError,
        _load_sutui_token_for_stt,
        _stt_create_task,
        _stt_poll_task,
    )

    token, _source = _load_sutui_token_for_stt(db, int(user_id))
    db.commit()
    job_dir = _DEVICE_UPLOAD_DIR / "stt"
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        created = _stt_create_task(token, audio_url, job_dir=job_dir)
        stt_data = _stt_poll_task(token, created["task_id"], job_dir=job_dir)
    except AutoCaptionJobError as exc:
        reason = _stt_error_text(exc)
        logger.warning("[device] audio STT failed url=%s reason=%s", audio_url, reason)
        raise HTTPException(status_code=502, detail="转写失败（上游）：%s" % reason) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[device] audio STT error url=%s err=%s", audio_url, str(exc)[:500])
        raise HTTPException(status_code=502, detail="转写失败（异常）：%s" % str(exc)[:400]) from exc
    text = extract_stt_text(stt_data)
    if not text:
        snippet = ""
        try:
            snippet = json.dumps(stt_data, ensure_ascii=False)[:400]
        except Exception:  # noqa: BLE001
            snippet = str(stt_data)[:400]
        logger.warning("[device] audio STT empty url=%s payload=%s", audio_url, snippet)
        raise HTTPException(status_code=502, detail="转写结果为空（上游返回）：%s" % snippet)
    return text


def extract_stt_text(stt_data: Any) -> str:
    """从速推 STT 的返回里取文本。

    实测返回形如 {"status":"completed","output":{"text":"..."},"result":{"text":"..."}}，
    文本在 output/result 里，不在顶层 —— 老实现只看顶层，导致转写成功也被判成"结果为空"。
    """
    if not isinstance(stt_data, dict):
        return ""
    candidates: list[Any] = [stt_data]
    for key in ("data", "output", "result"):
        value = stt_data.get(key)
        if isinstance(value, dict):
            candidates.append(value)
            for sub in ("data", "output", "result"):
                if isinstance(value.get(sub), dict):
                    candidates.append(value[sub])
    for data in candidates:
        for key in ("text", "full_text", "transcription", "asr_text"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in ("utterances", "sentences", "segments"):
            items = data.get(key)
            if isinstance(items, list) and items:
                parts = []
                for item in items:
                    if isinstance(item, dict):
                        piece = str(item.get("text") or item.get("sentence") or "").strip()
                        if piece:
                            parts.append(piece)
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
