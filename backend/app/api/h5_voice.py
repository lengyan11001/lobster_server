from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

import websockets
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import SessionLocal
from ..models import User
from ..services.xfyun_realtime_asr import (
    XfyunTranscriptState,
    build_xfyun_continue_frame,
    build_xfyun_first_frame,
    build_xfyun_iat_ws_url,
    build_xfyun_last_frame,
    xfyun_is_configured,
    xfyun_missing_config_fields,
)
from ..services.voice_intent_llm import resolve_voice_intent_with_llm
from ..services.brand_context import explicit_request_brand_mark
from .auth import ALGORITHM, get_current_user, validate_token_brand

logger = logging.getLogger(__name__)
router = APIRouter()


class MicrophoneStartupDiagnostic(BaseModel):
    error_name: str = Field(default="", max_length=80)
    error_message: str = Field(default="", max_length=500)
    diagnostics: str = Field(default="", max_length=1000)
    user_agent: str = Field(default="", max_length=600)
    brand: str = Field(default="", max_length=64)


class H5LifecycleDiagnostic(BaseModel):
    event: str = Field(default="", max_length=80)
    timeline_json: str = Field(default="", max_length=12000)
    user_agent: str = Field(default="", max_length=600)
    path: str = Field(default="", max_length=500)
    brand: str = Field(default="", max_length=64)


def _log_value(value: str, limit: int) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ")[:limit]


@router.post("/api/h5-chat/voice/diagnostics")
async def h5_voice_diagnostics(
    body: MicrophoneStartupDiagnostic,
    current_user: User = Depends(get_current_user),
):
    logger.warning(
        "[h5_voice] microphone_start_failed user_id=%s brand=%s error=%s message=%s diagnostics=%s ua=%s",
        current_user.id,
        _log_value(body.brand, 64),
        _log_value(body.error_name, 80),
        _log_value(body.error_message, 500),
        _log_value(body.diagnostics, 1000),
        _log_value(body.user_agent, 600),
    )
    return {"ok": True}


@router.post("/api/h5-chat/client/diagnostics")
async def h5_client_diagnostics(
    body: H5LifecycleDiagnostic,
    current_user: User = Depends(get_current_user),
):
    event = _log_value(body.event, 80)
    log = logger.warning if event in {"window_error", "unhandled_rejection", "resume_timeout"} else logger.info
    log(
        "[h5_lifecycle] user_id=%s brand=%s event=%s path=%s timeline=%s ua=%s",
        current_user.id,
        _log_value(body.brand, 64),
        event,
        _log_value(body.path, 500),
        _log_value(body.timeline_json, 12000),
        _log_value(body.user_agent, 600),
    )
    return {"ok": True}


@router.get("/api/h5-chat/voice/config")
async def h5_voice_config():
    provider = str(getattr(settings, "h5_voice_asr_provider", "") or "xfyun").strip().lower()
    configured = provider == "xfyun" and xfyun_is_configured()
    return JSONResponse(
        {
            "provider": provider or "xfyun",
            "configured": configured,
            "missing": [] if configured else xfyun_missing_config_fields(),
            "ws_path": "/api/h5-chat/voice/session",
        }
    )


def _user_from_query_token(db: Session, token: str, brand_mark: str = "") -> User:
    credentials_exception = RuntimeError("invalid credentials")
    raw = str(token or "").strip()
    if not raw:
        raise credentials_exception
    try:
        payload = jwt.decode(raw, settings.secret_key, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise credentials_exception
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception
    validate_token_brand(payload, user=user, explicit_brand=brand_mark or None)
    return user


async def _send_json_safe(websocket: WebSocket, payload: Dict[str, Any]) -> None:
    try:
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))
    except Exception:
        logger.debug("h5 voice send skipped because websocket already closed")


@router.websocket("/api/h5-chat/voice/session")
async def h5_voice_session(
    websocket: WebSocket,
    token: str = Query(""),
    brand: str = Query(""),
    installation_id: str = Query(""),
    resolve_intent: bool = Query(True),
):
    await websocket.accept()

    db = SessionLocal()
    try:
        _user_from_query_token(db, token, explicit_request_brand_mark(websocket) or brand)
    except Exception:
        db.close()
        await _send_json_safe(websocket, {"type": "error", "message": "登录已失效，请重新登录后再试"})
        await websocket.close(code=4401)
        return
    finally:
        try:
            db.close()
        except Exception:
            pass

    provider = str(getattr(settings, "h5_voice_asr_provider", "") or "xfyun").strip().lower()
    upstream_ws = None
    upstream_reader_task: Optional[asyncio.Task] = None
    upstream_started = False
    tracker = XfyunTranscriptState()
    closed = False

    async def close_upstream():
        nonlocal upstream_ws, upstream_reader_task, closed
        if closed:
            return
        closed = True
        if upstream_reader_task:
            upstream_reader_task.cancel()
            try:
                await upstream_reader_task
            except BaseException:
                pass
            upstream_reader_task = None
        if upstream_ws is not None:
            try:
                await upstream_ws.close()
            except Exception:
                pass
            upstream_ws = None

    async def upstream_reader():
        nonlocal tracker
        assert upstream_ws is not None
        try:
            async for message in upstream_ws:
                try:
                    payload = json.loads(message)
                except Exception:
                    await _send_json_safe(websocket, {"type": "error", "message": f"识别服务返回了无法解析的数据: {str(message)[:120]}"})
                    continue
                event = tracker.apply_payload(payload)
                if not event:
                    continue
                await _send_json_safe(websocket, event)
                if event.get("type") == "final" and resolve_intent:
                    intent = await resolve_voice_intent_with_llm(
                        text=str(event.get("text") or ""),
                        token=token,
                        installation_id=installation_id,
                    )
                    await _send_json_safe(websocket, {"type": "intent", **intent})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[h5_voice] upstream_reader failed: %s", exc)
            await _send_json_safe(websocket, {"type": "error", "message": f"实时识别连接中断: {str(exc)[:160]}"})

    try:
        while True:
            message = await websocket.receive()
            msg_type = message.get("type")
            if msg_type == "websocket.disconnect":
                break

            text = message.get("text")
            data = message.get("bytes")

            if text is not None:
                try:
                    payload = json.loads(text)
                except Exception:
                    await _send_json_safe(websocket, {"type": "error", "message": "语音控制消息不是合法 JSON"})
                    continue
                action = str(payload.get("type") or "").strip().lower()
                if action == "ping":
                    await _send_json_safe(websocket, {"type": "pong"})
                    continue
                if action == "start":
                    tracker = XfyunTranscriptState()
                    upstream_started = False
                    if provider != "xfyun":
                        await _send_json_safe(websocket, {"type": "error", "message": f"当前未支持的语音识别 provider: {provider}"})
                        continue
                    if not xfyun_is_configured():
                        await _send_json_safe(
                            websocket,
                            {
                                "type": "error",
                                "code": "provider_not_configured",
                                "message": "讯飞实时语音识别尚未配置，请补充 xfyun_app_id / xfyun_api_key / xfyun_api_secret",
                                "missing": xfyun_missing_config_fields(),
                            },
                        )
                        continue
                    try:
                        upstream_ws = await websockets.connect(
                            build_xfyun_iat_ws_url(),
                            ping_interval=20,
                            ping_timeout=20,
                            max_size=2 * 1024 * 1024,
                        )
                        upstream_reader_task = asyncio.create_task(upstream_reader())
                        await _send_json_safe(websocket, {"type": "listening", "provider": "xfyun"})
                    except Exception as exc:
                        logger.warning("[h5_voice] connect xfyun failed: %s", exc)
                        await _send_json_safe(websocket, {"type": "error", "message": f"连接讯飞实时识别失败: {str(exc)[:180]}"})
                    continue
                if action == "stop":
                    if upstream_ws is None:
                        await _send_json_safe(websocket, {"type": "error", "message": "语音会话尚未开始"})
                        continue
                    try:
                        await upstream_ws.send(json.dumps(build_xfyun_last_frame(), ensure_ascii=False))
                    except Exception as exc:
                        await _send_json_safe(websocket, {"type": "error", "message": f"结束语音会话失败: {str(exc)[:160]}"})
                    continue
                continue

            if data is not None:
                if upstream_ws is None:
                    continue
                try:
                    frame = build_xfyun_first_frame(data) if not upstream_started else build_xfyun_continue_frame(data)
                    upstream_started = True
                    await upstream_ws.send(json.dumps(frame, ensure_ascii=False))
                except Exception as exc:
                    logger.warning("[h5_voice] send audio frame failed: %s", exc)
                    await _send_json_safe(websocket, {"type": "error", "message": f"发送音频分片失败: {str(exc)[:160]}"})
                    continue
    except WebSocketDisconnect:
        pass
    finally:
        await close_upstream()
