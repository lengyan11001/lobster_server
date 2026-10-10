from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import websockets
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import SessionLocal
from ..models import H5ChatMessage, User
from . import device_ai
from ..services.xfyun_realtime_asr import (
    XfyunTranscriptState,
    build_xfyun_continue_frame,
    build_xfyun_first_frame,
    build_xfyun_iat_ws_url,
    build_xfyun_last_frame,
    xfyun_is_configured,
    xfyun_missing_config_fields,
)
from ..services import device_tts
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


# ── 流式回复（设备 ask）：回复增量 + 逐句语音 ─────────────────────────
_ASK_FINAL_STATUSES = {"completed", "failed", "error", "cancelled"}
_ASK_POLL_SECONDS = 0.5
_ASK_MAX_SECONDS = 600


def _as_flag(value: Any) -> bool:
    """tts 开关：1/true/yes/on/y 视为开，其它（含 None、0、false）为关。"""
    text = str(value if value is not None else "").strip().lower()
    return text in ("1", "true", "yes", "on", "y")


def _load_reply_state(message_id: str) -> Optional[Dict[str, Any]]:
    """后台线程里读一次消息状态（每次新开 session，避免跨线程复用）。"""
    db = SessionLocal()
    try:
        row = db.query(H5ChatMessage).filter(H5ChatMessage.id == str(message_id)).first()
        if row is None:
            return None
        return {
            "status": str(row.status or ""),
            "reply_text": str(row.reply_text or ""),
            "error": str(row.error or ""),
        }
    finally:
        db.close()


def _submit_ask(user_id: int, request_obj: Any, text: str, session_id: str) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        return device_ai.submit_to_orchestrator(
            request_obj, db, {"user_id": int(user_id)}, text, session_id
        )
    finally:
        db.close()


def _synth_sentence(user_id: int, text: str, cache_key: str, description: str) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        return device_ai.synth_and_store_audio(
            db=db, user_id=int(user_id), text=text, cache_key=cache_key, description=description
        )
    finally:
        db.close()


def _closed_sentences(text: str) -> List[str]:
    """已经「封口」的句子：前缀稳定，可以立刻拿去逐句合成。"""
    body = str(text or "").strip()
    if not body:
        return []
    segments = device_tts.split_sentences(body)
    if not segments:
        return []
    if body[-1] in device_tts.SENTENCE_END_CHARS:
        return segments
    return segments[:-1]


def _audio_urls(path_url: str, audio_base: str) -> Dict[str, str]:
    if not path_url:
        return {}
    return {"url": path_url, "abs_url": (audio_base + path_url) if audio_base else ""}


async def _publish_full_audio(
    *,
    message_id: str,
    user_id: int,
    body: str,
    tail: str,
    segment_keys: List[str],
    audio_base: str,
) -> Tuple[str, float]:
    """收尾：分句音频按序拼成整段；不足阈值的残句补合成一次后并入整段。"""
    credits = 0.0
    keys = list(segment_keys)
    if tail:
        result = await asyncio.to_thread(
            _synth_sentence,
            user_id,
            tail,
            "%s_tail" % message_id,
            "设备整段语音(TTS) 尾句 消息 #%s，%d 字" % (message_id[:12], len(tail)),
        )
        if result.get("error"):
            logger.warning("[h5_voice] ask tail tts failed message=%s: %s", message_id, result["error"])
        else:
            keys.append("%s_tail" % message_id)
            credits += float(result.get("credits") or 0)
    url = ""
    if keys:
        url = await asyncio.to_thread(
            device_ai.concat_audio_segments, keys=keys, out_key="%s_full" % message_id
        )
    if not url and body:
        # 没有分句音频（整条回复不足阈值）或拼接失败 → 整段合成一次
        result = await asyncio.to_thread(
            _synth_sentence,
            user_id,
            body,
            "%s_full" % message_id,
            "设备整段语音(TTS) 消息 #%s，%d 字" % (message_id[:12], len(body)),
        )
        if result.get("error"):
            logger.warning("[h5_voice] ask full tts failed message=%s: %s", message_id, result["error"])
            return "", credits
        credits += float(result.get("credits") or 0)
        url = str(result.get("audio_url") or "")
    return url, credits


async def _run_ask_stream(
    websocket: WebSocket,
    *,
    user_id: int,
    text: str,
    session_id: str,
    tts: bool,
    audio_base: str,
) -> None:
    """设备/H5 在语音 WS 上发一条 ask：回复增量（reply_delta）+ 逐句语音（reply_audio）推回去。"""
    try:
        submitted = await asyncio.to_thread(_submit_ask, user_id, websocket, text, session_id)
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "detail", None) or str(exc)
        logger.warning("[h5_voice] ask submit failed user=%s: %s", user_id, str(detail)[:300])
        await _send_json_safe(
            websocket, {"type": "reply_error", "message": "送 AI 调度失败：%s" % str(detail)[:200]}
        )
        return

    message_id = str((submitted or {}).get("message_id") or "")
    if not message_id:
        await _send_json_safe(
            websocket, {"type": "reply_error", "message": "送 AI 调度失败：没有拿到 message_id"}
        )
        return

    await _send_json_safe(
        websocket,
        {
            "type": "reply_accepted",
            "message_id": message_id,
            "session_id": (submitted or {}).get("session_id") or session_id,
            "status": (submitted or {}).get("status") or "pending",
            "tts": bool(tts),
        },
    )

    floor = device_tts.sentence_min_chars()
    deadline = asyncio.get_running_loop().time() + _ASK_MAX_SECONDS
    seq = 0
    last_text = ""
    closed_index = 0
    cursor = 0
    segment_keys: List[str] = []
    total_credits = 0.0
    status = "pending"
    error_text = ""

    try:
        while True:
            state = await asyncio.to_thread(_load_reply_state, message_id)
            if state is None:
                await _send_json_safe(
                    websocket, {"type": "reply_error", "message_id": message_id, "message": "消息不存在"}
                )
                return
            status = state["status"] or "pending"
            error_text = state["error"]
            full_text = state["reply_text"]
            if len(full_text) > len(last_text):
                delta = full_text[len(last_text):]
                last_text = full_text
                seq += 1
                await _send_json_safe(
                    websocket,
                    {
                        "type": "reply_delta",
                        "message_id": message_id,
                        "seq": seq,
                        "delta": delta,
                        "text": full_text,
                        "chars": len(full_text),
                    },
                )
            if tts and last_text:
                body = last_text.strip()
                # 在「还没合成过的剩余文本」上切句：短句会被后面的句子吸收，
                # 所以不能拿整段文本切好再按下标取（下标会错位）。
                pending = body[cursor:]
                consumed = 0
                for sentence in _closed_sentences(pending):
                    pos = pending.find(sentence, consumed)
                    if pos < 0:
                        continue
                    consumed = pos + len(sentence)
                    if len(sentence) < floor:
                        continue  # 太短：不单独合成（并入收尾的整段音频）
                    closed_index += 1
                    key = "%s_%d" % (message_id, closed_index)
                    result = await asyncio.to_thread(
                        _synth_sentence,
                        user_id,
                        sentence,
                        key,
                        "设备逐句语音(TTS) 消息 #%s 第 %d 句，%d 字"
                        % (message_id[:12], closed_index, len(sentence)),
                    )
                    if result.get("error"):
                        await _send_json_safe(
                            websocket,
                            {
                                "type": "reply_audio_error",
                                "message_id": message_id,
                                "seq": seq,
                                "index": closed_index,
                                "text": sentence,
                                "error": result["error"],
                            },
                        )
                        continue
                    segment_keys.append(key)
                    total_credits += float(result.get("credits") or 0)
                    await _send_json_safe(
                        websocket,
                        {
                            "type": "reply_audio",
                            "message_id": message_id,
                            "seq": seq,
                            "index": closed_index,
                            "text": sentence,
                            "chars": len(sentence),
                            "credits": result.get("credits", 0),
                            "cached": bool(result.get("cached")),
                            **_audio_urls(str(result.get("audio_url") or ""), audio_base),
                        },
                    )
                cursor += consumed
            if status in _ASK_FINAL_STATUSES and full_text == last_text:
                break
            if asyncio.get_running_loop().time() >= deadline:
                logger.warning("[h5_voice] ask stream timeout message=%s", message_id)
                break
            await asyncio.sleep(_ASK_POLL_SECONDS)
    except asyncio.CancelledError:
        logger.info("[h5_voice] ask stream cancelled message=%s", message_id)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[h5_voice] ask stream failed message=%s: %s", message_id, exc)
        await _send_json_safe(
            websocket, {"type": "reply_error", "message_id": message_id, "message": str(exc)[:200]}
        )
        return

    if status in ("failed", "error"):
        await _send_json_safe(
            websocket,
            {"type": "reply_error", "message_id": message_id, "message": error_text or "任务失败"},
        )

    body = last_text.strip()
    full_url = ""
    if tts and body:
        full_url, extra = await _publish_full_audio(
            message_id=message_id,
            user_id=user_id,
            body=body,
            tail=(body[cursor:].strip() if cursor < len(body) else ""),
            segment_keys=segment_keys,
            audio_base=audio_base,
        )
        total_credits += extra
        if full_url:
            await _send_json_safe(
                websocket,
                {
                    "type": "reply_audio_full",
                    "message_id": message_id,
                    "segments": len(segment_keys),
                    "chars": len(body),
                    **_audio_urls(full_url, audio_base),
                },
            )

    await _send_json_safe(
        websocket,
        {
            "type": "reply_done",
            "message_id": message_id,
            "status": status,
            "text": last_text,
            "chars": len(last_text),
            "segments": len(segment_keys),
            "credits": round(total_credits, 4),
            "audio_full_url": full_url,
            "timeout": status not in _ASK_FINAL_STATUSES,
        },
    )


@router.websocket("/api/h5-chat/voice/session")
async def h5_voice_session(
    websocket: WebSocket,
    token: str = Query(""),
    brand: str = Query(""),
    installation_id: str = Query(""),
    resolve_intent: bool = Query(True),
    device_token: str = Query(""),
):
    await websocket.accept()

    # 设备侧（ESP32 等）没有用户 JWT，只持有 device_token；WebSocket 又不能带 Authorization 头，
    # 所以这里允许用 ?device_token=dev_xxx 连：查绑定表拿到所属账号，换成短期内部 JWT 继续走同一条链路。
    if not str(token or "").strip() and str(device_token or "").strip():
        try:
            import hashlib as _hashlib
            from datetime import timedelta as _timedelta

            from sqlalchemy import text as _sqltext

            from ..db import SessionLocal as _SessionLocal
            from .auth import create_access_token as _mk_token
            from .device_ai import ensure_tables as _ensure_device_tables

            _s = _SessionLocal()
            try:
                _ensure_device_tables(_s)
                _h = _hashlib.sha256(str(device_token).strip().encode("utf-8")).hexdigest()
                _row = _s.execute(
                    _sqltext("SELECT user_id FROM bound_devices WHERE device_token_hash = :h"),
                    {"h": _h},
                ).first()
                if _row:
                    token = _mk_token(
                        {"sub": str(int(_row[0])), "email": ""},
                        expires_delta=_timedelta(minutes=10),
                    )
            finally:
                try:
                    _s.close()
                except Exception:
                    pass
        except Exception:
            pass

    user_id = 0
    db = SessionLocal()
    try:
        _voice_user = _user_from_query_token(db, token, explicit_request_brand_mark(websocket) or brand)
        user_id = int(_voice_user.id)
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
    ask_task: Optional[asyncio.Task] = None
    upstream_started = False
    tracker = XfyunTranscriptState()
    closed = False
    audio_base = device_ai.public_base_from_headers(websocket.headers)

    async def close_upstream():
        nonlocal upstream_ws, upstream_reader_task, ask_task, closed
        if closed:
            return
        closed = True
        if ask_task is not None and not ask_task.done():
            ask_task.cancel()
            try:
                await ask_task
            except BaseException:
                pass
            ask_task = None
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
                if action == "ask":
                    text_in = str(payload.get("text") or "").strip()
                    if not text_in:
                        await _send_json_safe(websocket, {"type": "error", "message": "ask 缺少 text"})
                        continue
                    if ask_task is not None and not ask_task.done():
                        ask_task.cancel()
                        try:
                            await ask_task
                        except BaseException:
                            pass
                    ask_task = asyncio.create_task(
                        _run_ask_stream(
                            websocket,
                            user_id=user_id,
                            text=text_in,
                            session_id=str(payload.get("session_id") or ""),
                            tts=_as_flag(payload.get("tts")),
                            audio_base=audio_base,
                        )
                    )
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
