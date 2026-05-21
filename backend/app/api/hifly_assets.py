from __future__ import annotations

import mimetypes
import json
import logging
import math
import os
import re
import hmac
import hashlib
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from ..models import Asset, User, UserHiflyAvatarAsset, UserHiflyVideoAsset, UserHiflyVoiceAsset
from .assets import _save_bytes_or_tos, _resolve_asset_public_base, build_asset_file_url
from .auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

_HIFLY_API_BASE = "https://hfw-api.hifly.cc"
_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_VIDEO_MAX_BYTES = 500 * 1024 * 1024
_AUDIO_MAX_BYTES = 20 * 1024 * 1024
_AUDIO_DRIVE_MAX_BYTES = 100 * 1024 * 1024
_MAX_AVATAR_PAGE_SIZE = 100
_MAX_VOICE_PAGE_SIZE = 300

_IMAGE_EXTS = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
_VIDEO_EXTS = {"mp4": "video/mp4", "mov": "video/quicktime"}
_AUDIO_EXTS = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav"}
_HIFLY_TTS_CAPABILITY_ID = "hifly.video.create_by_tts"
_HIFLY_AUDIO_CAPABILITY_ID = "hifly.video.create_by_audio"
_HIFLY_TTS_UNIT_CREDITS = 10
_HIFLY_TTS_CHARS_PER_SECOND = 4
_HIFLY_SHARE_SECRET = (getattr(settings, "secret_key", None) or os.getenv("SECRET_KEY") or "lobster-share-secret").encode("utf-8")
_VOICE_PREVIEW_EXPIRY_SEC = 86400
_AVATAR_COVER_EXPIRY_SEC = 30 * 86400
_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_HIFLY_PUBLIC_AVATARS_PATH = _DATA_DIR / "hifly_public_avatars.json"
_HIFLY_PUBLIC_AVATAR_COVERS_PATH = _DATA_DIR / "hifly_public_avatar_covers.json"
_HIFLY_PUBLIC_VOICES_SEED_PATH = _DATA_DIR / "hifly_public_voices_seed.json"
_HIFLY_PREVIEWS_MANIFEST_PATH = _DATA_DIR / "hifly_previews" / "manifest.json"


class HiflyTaskBody(BaseModel):
    task_id: str = Field(..., min_length=1)
    token: Optional[str] = None


class HiflyTokenBody(BaseModel):
    token: Optional[str] = None


class HiflyVoiceEditBody(BaseModel):
    voice: str = Field(..., min_length=1, max_length=128)
    rate: str = Field("1.0")
    volume: str = Field("1.0")
    pitch: str = Field("1.0")
    token: Optional[str] = None


class HiflyAvatarLibraryBody(HiflyTokenBody):
    page: int = 1
    size: int = 10
    include_mine: bool = False


def _resolved_token(token: Optional[str]) -> str:
    value = (token or "").strip()
    if value:
        return value
    fallback = (settings.hifly_default_token or "").strip()
    if fallback:
        return fallback
    raise HTTPException(status_code=400, detail="请先在服务端配置 HIFLY_DEFAULT_TOKEN，或提交时显式传入 token")


def _bearer_from_request(request: Optional[Request]) -> str:
    if request is None:
        return ""
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _billing_base() -> str:
    base = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="未配置 AUTH_SERVER_BASE，无法完成 HiFly 算力计费")
    return base


def _billing_headers(request: Request) -> Dict[str, str]:
    token = _bearer_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="请先登录后再生成 HiFly 视频")
    headers: Dict[str, str] = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    installation_id = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    billing_key = (getattr(settings, "lobster_mcp_billing_internal_key", None) or "").strip() or (os.environ.get("LOBSTER_MCP_BILLING_INTERNAL_KEY") or "").strip()
    if billing_key:
        headers["X-Lobster-Mcp-Billing"] = billing_key
    return headers


def _estimate_tts_seconds(text: str) -> int:
    clean = "".join(str(text or "").split())
    return max(1, int(math.ceil(len(clean) / _HIFLY_TTS_CHARS_PER_SECOND)))


def _duration_seconds(value: Any) -> int:
    try:
        return max(1, int(math.ceil(float(value or 0))))
    except (TypeError, ValueError):
        return 1


def _estimate_audio_seconds(value: Any) -> int:
    return _duration_seconds(value)


async def _hifly_pre_deduct_tts(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    estimated_seconds = _estimate_tts_seconds(str(payload.get("text") or ""))
    expected_credits = estimated_seconds * _HIFLY_TTS_UNIT_CREDITS
    body = {
        "capability_id": _HIFLY_TTS_CAPABILITY_ID,
        "model": "hifly-text-driven",
        "params": {
            "estimated_seconds": estimated_seconds,
            "unit_credits": _HIFLY_TTS_UNIT_CREDITS,
            "text_length": len(str(payload.get("text") or "")),
            "expected_credits": expected_credits,
        },
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{_billing_base()}/capabilities/pre-deduct", json=body, headers=_billing_headers(request))
    if resp.status_code == 402:
        detail = (resp.json() if resp.content else {}).get("detail", "算力不足")
        raise HTTPException(status_code=402, detail=f"算力不足，预计需预扣 {expected_credits} 算力。{detail}")
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录后再生成")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"HiFly 计费预扣失败 HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    data = resp.json() if resp.content else {}
    try:
        charged_value = float(data.get("credits_charged"))
    except (TypeError, ValueError):
        charged_value = float(expected_credits)
    return {"credits_pre_deducted": charged_value, "estimated_seconds": estimated_seconds, "expected_credits": expected_credits, "raw": data}


async def _hifly_pre_deduct_audio(request: Request, payload: Dict[str, Any], duration_seconds: int) -> Dict[str, Any]:
    estimated_seconds = _estimate_audio_seconds(duration_seconds)
    expected_credits = estimated_seconds * _HIFLY_TTS_UNIT_CREDITS
    body = {
        "capability_id": _HIFLY_AUDIO_CAPABILITY_ID,
        "model": "hifly-audio-driven",
        "params": {
            "estimated_seconds": estimated_seconds,
            "unit_credits": _HIFLY_TTS_UNIT_CREDITS,
            "audio_size": int(payload.get("audio_size") or 0),
            "expected_credits": expected_credits,
        },
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{_billing_base()}/capabilities/pre-deduct", json=body, headers=_billing_headers(request))
    if resp.status_code == 402:
        detail = (resp.json() if resp.content else {}).get("detail", "算力不足")
        raise HTTPException(status_code=402, detail=f"算力不足，预计需预扣 {expected_credits} 算力。{detail}")
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录后再生成")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"HiFly 计费预扣失败 HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    data = resp.json() if resp.content else {}
    try:
        charged_value = float(data.get("credits_charged"))
    except (TypeError, ValueError):
        charged_value = float(expected_credits)
    return {"credits_pre_deducted": charged_value, "estimated_seconds": estimated_seconds, "expected_credits": expected_credits, "raw": data}


async def _hifly_refund_capability(request: Request, capability_id: str, credits: float) -> None:
    if credits <= 0:
        return
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.post(f"{_billing_base()}/capabilities/refund", json={"capability_id": capability_id, "credits": credits}, headers=_billing_headers(request))
    except Exception:
        logger.exception("[hifly-billing] refund failed capability=%s credits=%s", capability_id, credits)


async def _hifly_refund_tts(request: Request, credits: float) -> None:
    await _hifly_refund_capability(request, _HIFLY_TTS_CAPABILITY_ID, credits)


async def _hifly_record_video_billing(request: Request, row: UserHiflyVideoAsset, result: Dict[str, Any]) -> Dict[str, Any]:
    meta = dict(row.meta or {})
    billing = dict(meta.get("billing") or {})
    capability_id = str(billing.get("capability_id") or _HIFLY_TTS_CAPABILITY_ID).strip() or _HIFLY_TTS_CAPABILITY_ID
    credits_pre = float(billing.get("credits_pre_deducted") or 0)
    actual_seconds = _duration_seconds(result.get("duration"))
    credits_final = float(actual_seconds * _HIFLY_TTS_UNIT_CREDITS)
    body = {
        "capability_id": capability_id,
        "success": True,
        "source": "hifly_video_task",
        "request_payload": {"task_id": row.hifly_task_id, "request_id": result.get("request_id") or billing.get("request_id") or "", "estimated_seconds": billing.get("estimated_seconds")},
        "response_payload": {"duration": result.get("duration"), "video_url": result.get("video_url") or ""},
        "credits_charged": credits_final,
        "pre_deduct_applied": credits_pre > 0,
        "credits_pre_deducted": credits_pre,
        "credits_final": credits_final,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{_billing_base()}/capabilities/record-call", json=body, headers=_billing_headers(request))
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"HiFly 计费结算失败 HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    billing.update({"billing_status": "settled", "credits_final": credits_final, "actual_seconds": actual_seconds, "settled_at": datetime.utcnow().isoformat() + "Z"})
    return billing


def _headers(token: Optional[str]) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_resolved_token(token)}",
        "Accept": "application/json",
    }


def _url(path: str) -> str:
    return f"{_HIFLY_API_BASE}{path}"


def _safe_title(value: str, default: str, max_len: int = 20) -> str:
    text = str(value or "").strip() or default
    return text[:max_len] or default


def _voice_param_text(value: Any, default: str, min_value: float, max_value: float, label: str) -> str:
    raw = str(value if value is not None else default).strip() or default
    try:
        number = float(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{label}必须是数字")
    if number < min_value or number > max_value:
        raise HTTPException(status_code=400, detail=f"{label}必须在 {min_value:g} 到 {max_value:g} 之间")
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return text or default


def _status_text(status: int) -> str:
    return {
        1: "等待中",
        2: "处理中",
        3: "已完成",
        4: "失败",
    }.get(status, "未知")


def _local_status(status: int) -> str:
    return {
        1: "waiting",
        2: "processing",
        3: "success",
        4: "failed",
    }.get(status, "processing")


def _raise_for_hifly_business_error(payload: Dict[str, Any]) -> None:
    code = payload.get("code", 0)
    if code in (None, 0):
        return
    message = str(payload.get("message") or payload.get("msg") or "HiFly business error")
    raise HTTPException(status_code=502, detail=f"HiFly error {code}: {message}")


async def _get(path: str, token: Optional[str], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        resp = await client.get(_url(path), headers=_headers(token), params=params or {})
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="HiFly token invalid or expired")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"HiFly HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    try:
        payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="HiFly returned non-JSON response") from exc
    if isinstance(payload, dict):
        _raise_for_hifly_business_error(payload)
        return payload
    raise HTTPException(status_code=502, detail="HiFly returned invalid payload")


async def _post(path: str, token: Optional[str], body: Dict[str, Any]) -> Dict[str, Any]:
    headers = _headers(token)
    headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        resp = await client.post(_url(path), headers=headers, json=body)
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="HiFly token invalid or expired")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"HiFly HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    try:
        payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="HiFly returned non-JSON response") from exc
    if isinstance(payload, dict):
        _raise_for_hifly_business_error(payload)
        return payload
    raise HTTPException(status_code=502, detail="HiFly returned invalid payload")


async def _put_bytes_to_url(upload_url: str, data: bytes, content_type: str) -> None:
    headers = {"Content-Type": content_type or "application/octet-stream"}
    async with httpx.AsyncClient(timeout=300.0, trust_env=False) as client:
        resp = await client.put(upload_url, headers=headers, content=data)
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"HiFly upload failed HTTP {resp.status_code}: {(resp.text or '')[:240]}",
        )


def _normalized_extension(upload: UploadFile, allowed_exts: Dict[str, str], fallback: str) -> str:
    filename_ext = Path(upload.filename or "").suffix.lower().lstrip(".")
    if filename_ext in allowed_exts:
        return filename_ext
    guessed = (mimetypes.guess_extension(upload.content_type or "") or "").lower().lstrip(".")
    if guessed == "jpe":
        guessed = "jpeg"
    if guessed in allowed_exts:
        return guessed
    return fallback


async def _upload_file_to_hifly(
    token: Optional[str],
    upload: UploadFile,
    *,
    allowed_exts: Dict[str, str],
    max_bytes: int,
    fallback_ext: str,
) -> Dict[str, Any]:
    raw = await upload.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(raw) > max_bytes:
        raise HTTPException(status_code=400, detail=f"上传文件不能超过 {max_bytes // (1024 * 1024)}MB")

    ext = _normalized_extension(upload, allowed_exts, fallback_ext)
    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail=f"仅支持 {', '.join(sorted(allowed_exts))} 格式")

    upload_meta = await _post("/api/v2/hifly/tool/create_upload_url", token, {"file_extension": ext})
    upload_url = str(upload_meta.get("upload_url") or "").strip()
    file_id = str(upload_meta.get("file_id") or "").strip()
    if not upload_url or not file_id:
        raise HTTPException(status_code=502, detail="HiFly 未返回 upload_url 或 file_id")

    content_type = (
        str(upload_meta.get("content_type") or "").strip()
        or allowed_exts.get(ext)
        or upload.content_type
        or "application/octet-stream"
    )
    await _put_bytes_to_url(upload_url, raw, content_type)
    return {
        "file_id": file_id,
        "content_type": content_type,
        "filename": upload.filename or f"upload.{ext}",
        "size": len(raw),
        "extension": ext,
        "raw_bytes": raw,
    }


def _upload_meta_for_store(uploaded: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "file_id": uploaded.get("file_id"),
        "content_type": uploaded.get("content_type"),
        "filename": uploaded.get("filename"),
        "size": uploaded.get("size"),
        "extension": uploaded.get("extension"),
        "source_asset_id": uploaded.get("source_asset_id"),
        "source_url": uploaded.get("source_url"),
    }


def _persist_input_asset(db: Session, user_id: int, uploaded: Dict[str, Any], media_type: str) -> Optional[Asset]:
    raw = uploaded.get("raw_bytes")
    if not raw:
        return None
    ext = "." + str(uploaded.get("extension") or "bin").lstrip(".")
    content_type = str(uploaded.get("content_type") or "").strip()
    asset_id, filename_or_key, file_size, source_url = _save_bytes_or_tos(raw, ext, content_type)
    asset = Asset(
        asset_id=asset_id,
        user_id=user_id,
        filename=filename_or_key,
        media_type=media_type,
        file_size=file_size,
        source_url=source_url,
    )
    db.add(asset)
    db.flush()
    return asset


def _payload_and_nested(payload: Dict[str, Any]):
    """HiFly 有的接口把业务字段放在顶层，有的塞进 data。两处都要扫一遍。"""
    nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return payload, nested or {}


def _pick_field(payload: Dict[str, Any], *keys: str) -> Any:
    """按优先级从 payload 顶层或 payload.data 中取第一个非空字段。"""
    top, nested = _payload_and_nested(payload)
    for key in keys:
        for src in (top, nested):
            value = src.get(key)
            if value not in (None, ""):
                return value
    return None


def _pick_cover(payload: Dict[str, Any]) -> str:
    top, nested = _payload_and_nested(payload)
    for key in ("image_url", "cover_url", "avatar_url", "pic_url", "thumbnail_url", "poster_url"):
        for src in (top, nested):
            value = str(src.get(key) or "").strip()
            if value:
                return value
    return ""


def _pick_demo(payload: Dict[str, Any]) -> str:
    top, nested = _payload_and_nested(payload)
    for key in ("demo_url", "audio_url", "preview_url"):
        for src in (top, nested):
            value = str(src.get(key) or "").strip()
            if value:
                return value
    return ""


def _voice_preview_token(row_id: int, expiry_ts: int) -> str:
    raw = f"hifly_voice_preview:{int(row_id)}:{int(expiry_ts)}"
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _build_voice_preview_proxy_url(request: Optional[Request], row_id: int) -> str:
    if request is None:
        return ""
    expiry_ts = int(time.time()) + _VOICE_PREVIEW_EXPIRY_SEC
    token = _voice_preview_token(row_id, expiry_ts)
    base = _resolve_asset_public_base(request)
    return f"{base}/api/hifly/my/voice/{int(row_id)}/preview?token={token}&expiry={expiry_ts}"


def _avatar_cover_token(url: str, expiry: int) -> str:
    msg = f"avatar-cover:{int(expiry)}:{url}".encode("utf-8")
    return hmac.new(_HIFLY_SHARE_SECRET, msg, hashlib.sha256).hexdigest()


def _build_avatar_cover_proxy_url(request: Optional[Request], url: str) -> str:
    value = str(url or "").strip()
    if request is None or not value.startswith(("http://", "https://")):
        return value
    expiry_ts = int(time.time()) + _AVATAR_COVER_EXPIRY_SEC
    token = _avatar_cover_token(value, expiry_ts)
    base = _resolve_asset_public_base(request).rstrip("/")
    return f"{base}/api/hifly/avatar/cover?url={quote(value, safe='')}&token={token}&expiry={expiry_ts}"


def _static_hifly_avatar_cover_url(request: Optional[Request], url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    decoded = unquote(value)
    proxy_match = re.search(r"[?&]url=([^&]+)", value)
    if proxy_match:
        decoded = unquote(proxy_match.group(1))
    if "/client/miniprogram/hifly_avatars/" in decoded:
        filename = decoded.rsplit("/client/miniprogram/hifly_avatars/", 1)[-1].split("?", 1)[0].split("#", 1)[0]
    elif "/static/hifly_avatars/" in decoded:
        filename = decoded.rsplit("/static/hifly_avatars/", 1)[-1].split("?", 1)[0].split("#", 1)[0]
    elif "hfcdn.lingverse.co/" in decoded:
        filename = decoded.split("?", 1)[0].split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        if filename:
            filename = f"{filename}.jpg"
    else:
        return ""
    if not filename or "/" in filename or "\\" in filename:
        return ""
    base = _static_public_base(request).rstrip("/")
    path = f"/client/miniprogram/hifly_avatars/{filename}"
    return f"{base}{path}" if base else path


def _resolve_voice_preview_source(row: UserHiflyVoiceAsset, request: Optional[Request] = None) -> str:
    demo_url = str(row.demo_url or "").strip()
    if demo_url:
        return demo_url
    if isinstance(row.meta, dict):
        task_raw = row.meta.get("task_raw") if isinstance(row.meta.get("task_raw"), dict) else {}
        create_raw = row.meta.get("create_raw") if isinstance(row.meta.get("create_raw"), dict) else {}
        demo_url = _pick_demo(task_raw) or _pick_demo(create_raw) or ""
        if demo_url:
            return demo_url
        upload_meta = row.meta.get("upload_meta") if isinstance(row.meta.get("upload_meta"), dict) else {}
        upload_source_url = str(upload_meta.get("source_url") or "").strip()
        if upload_source_url:
            return upload_source_url
        upload_asset_id = str(upload_meta.get("source_asset_id") or "").strip()
        if request is not None and upload_asset_id:
            return build_asset_file_url(request, upload_asset_id) or ""
    return ""


def _refresh_voice_asset_from_hifly(row: UserHiflyVoiceAsset) -> bool:
    """对已成功但缺少 demo_url/voice_id 的旧记录做一次懒刷新。"""
    task_id = str(row.hifly_task_id or "").strip()
    if not task_id:
        return False
    try:
        headers = {"Authorization": f"Bearer {_resolved_token(None)}"}
        with httpx.Client(timeout=30.0, trust_env=False) as client:
            resp = client.get(_url("/api/v2/hifly/voice/task"), headers=headers, params={"task_id": task_id})
        if resp.status_code == 401:
            logger.warning("[hifly_assets] refresh voice task unauthorized task_id=%s", task_id)
            return False
        if resp.status_code >= 400:
            logger.warning("[hifly_assets] refresh voice task http=%s task_id=%s", resp.status_code, task_id)
            return False
        payload = resp.json()
        if not isinstance(payload, dict):
            return False
        _raise_for_hifly_business_error(payload)
    except Exception as exc:
        logger.warning("[hifly_assets] refresh voice task failed task_id=%s err=%s", task_id, exc)
        return False

    status_num = int(_pick_field(payload, "status") or 0)
    next_status = _local_status(status_num)
    next_title = _safe_title(str(_pick_field(payload, "title") or row.title or "未命名声音"), "未命名声音", 128)
    next_voice_id = str(_pick_field(payload, "voice", "voice_id") or row.hifly_voice_id or "").strip() or None
    next_demo_url = _pick_demo(payload) or row.demo_url
    next_cover_url = _pick_cover(payload) or row.cover_url
    next_error = str(_pick_field(payload, "message") or "").strip() or None
    next_meta = dict(row.meta or {})
    next_meta["task_raw"] = payload

    changed = False
    if row.status != next_status:
        row.status = next_status
        changed = True
    if row.title != next_title:
        row.title = next_title
        changed = True
    if row.hifly_voice_id != next_voice_id:
        row.hifly_voice_id = next_voice_id
        changed = True
    if row.demo_url != next_demo_url:
        row.demo_url = next_demo_url
        changed = True
    if row.cover_url != next_cover_url:
        row.cover_url = next_cover_url
        changed = True
    if row.error_message != next_error:
        row.error_message = next_error
        changed = True
    if row.meta != next_meta:
        row.meta = next_meta
        changed = True
    return changed


def _normalize_avatar_asset(row: UserHiflyAvatarAsset, request: Optional[Request] = None) -> Dict[str, Any]:
    meta = dict(row.meta or {})
    detail_asset_id = str(meta.get("source_asset_id") or "").strip()
    detail_url = build_asset_file_url(request, detail_asset_id) if request and detail_asset_id else ""
    cover_url = row.cover_url or ""
    return {
        "id": row.id,
        "task_id": row.hifly_task_id,
        "avatar": row.hifly_avatar_id or "",
        "title": row.title,
        "image_url": cover_url,
        "cover_url": cover_url,
        "source_type": row.source_type,
        "detail_asset_id": detail_asset_id,
        "detail_url": detail_url,
        "status": row.status,
        "status_text": {
            "waiting": "等待中",
            "processing": "处理中",
            "success": "已完成",
            "failed": "失败",
        }.get(row.status, "处理中"),
        "model": row.model,
        "aigc_flag": row.aigc_flag,
        "message": row.error_message or "",
        "section_label": "我的数字人",
        "is_mine": True,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _normalize_voice_asset(row: UserHiflyVoiceAsset, request: Optional[Request] = None) -> Dict[str, Any]:
    voice_id = row.hifly_voice_id or ""
    demo_url = _resolve_voice_preview_source(row, request)
    cover_url = row.cover_url or ""
    voice_params = {}
    if isinstance(row.meta, dict) and isinstance(row.meta.get("voice_params"), dict):
        voice_params = dict(row.meta.get("voice_params") or {})
    # 旧记录可能因为没做 data 嵌套 fallback 而 demo_url 为空，这里再从 meta.task_raw 兜底一次
    if (not demo_url or not voice_id) and isinstance(row.meta, dict):
        task_raw = row.meta.get("task_raw") if isinstance(row.meta.get("task_raw"), dict) else {}
        create_raw = row.meta.get("create_raw") if isinstance(row.meta.get("create_raw"), dict) else {}
        if not demo_url:
            demo_url = _pick_demo(task_raw) or _pick_demo(create_raw) or ""
        if not voice_id:
            vid = _pick_field(task_raw, "voice", "voice_id") or _pick_field(create_raw, "voice", "voice_id")
            if vid:
                voice_id = str(vid).strip()
        if not cover_url:
            cover_url = _pick_cover(task_raw) or _pick_cover(create_raw) or ""
    if not demo_url and isinstance(row.meta, dict):
        upload_meta = row.meta.get("upload_meta") if isinstance(row.meta.get("upload_meta"), dict) else {}
        upload_asset_id = str(upload_meta.get("source_asset_id") or "").strip()
        upload_source_url = str(upload_meta.get("source_url") or "").strip()
        demo_url = upload_source_url or (build_asset_file_url(request, upload_asset_id) if request and upload_asset_id else "")
    public_demo_url = _build_voice_preview_proxy_url(request, row.id) if request and demo_url else demo_url
    styles = []
    if voice_id:
        styles.append(
            {
                "voice": voice_id,
                "label": "默认风格",
                "demo_url": public_demo_url,
                "title": row.title,
            }
        )
    return {
        "id": row.id,
        "task_id": row.hifly_task_id,
        "voice": voice_id,
        "title": row.title,
        "image_url": cover_url,
        "cover_url": cover_url,
        "demo_url": public_demo_url,
        "demo_origin_url": demo_url,
        "status": row.status,
        "status_text": {
            "waiting": "等待中",
            "processing": "处理中",
            "success": "已完成",
            "failed": "失败",
        }.get(row.status, "处理中"),
        "languages": row.languages or "zh",
        "voice_type": row.voice_type,
        "message": row.error_message or "",
        "section_label": "我的声音",
        "is_mine": True,
        "voice_params": {
            "rate": str(voice_params.get("rate") or "1.0"),
            "volume": str(voice_params.get("volume") or "1.0"),
            "pitch": str(voice_params.get("pitch") or "1.0"),
        },
        "style_count": len(styles),
        "styles": styles,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _base_name(title: str) -> str:
    text = "".join(str(title or "").split())
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"-视频素材\d+$", "", text, flags=re.IGNORECASE)
    for suffix in ("-直播", "-分享", "-近景", "-中近景", "-远中景"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def _read_json_file(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        raw = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.warning("[hifly_assets] failed to read json %s", path, exc_info=True)
        return {}


def _has_hifly_token(token: Optional[str]) -> bool:
    return bool((token or "").strip() or (settings.hifly_default_token or "").strip())


def _static_public_base(request: Optional[Request]) -> str:
    if request is not None:
        return _resolve_asset_public_base(request).rstrip("/")
    pub = (getattr(settings, "public_base_url", None) or "").strip().rstrip("/")
    return pub


def _absolute_public_url(url: str, request: Optional[Request]) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("/"):
        base = _static_public_base(request)
        return f"{base}{value}" if base else value
    return value


def _load_seed_public_avatar_rows() -> List[Dict[str, Any]]:
    data = _read_json_file(_HIFLY_PUBLIC_AVATARS_PATH)
    rows = data.get("data") or []
    return rows if isinstance(rows, list) else []


def _load_seed_public_voice_rows() -> List[Dict[str, Any]]:
    data = _read_json_file(_HIFLY_PUBLIC_VOICES_SEED_PATH)
    nested = data.get("data")
    if isinstance(nested, dict):
        rows = nested.get("list") or []
    else:
        rows = data.get("list") or data.get("data") or []
    return rows if isinstance(rows, list) else []


def _load_public_avatar_cover_maps() -> tuple[Dict[str, str], Dict[str, str]]:
    data = _read_json_file(_HIFLY_PUBLIC_AVATAR_COVERS_PATH)
    by_avatar_raw = data.get("by_avatar") or {}
    by_title_raw = data.get("by_title") or {}
    by_avatar = {
        str(key or "").strip(): str(value or "").strip()
        for key, value in by_avatar_raw.items()
        if str(key or "").strip() and str(value or "").strip()
    } if isinstance(by_avatar_raw, dict) else {}
    by_title = {
        str(key or "").strip().lower(): str(value or "").strip()
        for key, value in by_title_raw.items()
        if str(key or "").strip() and str(value or "").strip()
    } if isinstance(by_title_raw, dict) else {}
    return by_avatar, by_title


def _public_avatar_cover_override(
    avatar_id: str,
    title: str,
    cover_maps: Optional[tuple[Dict[str, str], Dict[str, str]]] = None,
) -> str:
    by_avatar, by_title = cover_maps or _load_public_avatar_cover_maps()
    avatar_hit = by_avatar.get(str(avatar_id or "").strip())
    if avatar_hit:
        return avatar_hit
    raw_title = str(title or "").strip()
    for key in (raw_title.lower(), _base_name(raw_title).lower()):
        hit = by_title.get(key)
        if hit:
            return hit
    return ""


def _pick_public_cover(item: Dict[str, Any]) -> str:
    for key in ("cover_url", "cover", "image_url", "avatar_url", "poster_url", "thumbnail_url", "preview_url"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _apply_public_avatar_material_counts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for row in rows:
        key = _base_name(str(row.get("title") or row.get("avatar") or ""))
        counts[key] = counts.get(key, 0) + 1
    for row in rows:
        key = _base_name(str(row.get("title") or row.get("avatar") or ""))
        row["material_count"] = counts.get(key, 1)
    return rows


def _sort_public_avatar_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            int(row.get("cover_rank", 9)),
            _base_name(str(row.get("title") or "")),
            str(row.get("title") or ""),
        ),
    )


def _enrich_public_avatar_rows(rows: List[Dict[str, Any]], request: Optional[Request] = None) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: set[str] = set()
    cover_maps = _load_public_avatar_cover_maps()
    for item in rows or []:
        avatar_id = str(item.get("avatar") or "").strip()
        if not avatar_id or avatar_id in seen:
            continue
        title = str(item.get("title") or avatar_id).strip() or avatar_id
        origin_cover_url = _pick_public_cover(item) or _public_avatar_cover_override(avatar_id, title, cover_maps)
        if not origin_cover_url:
            continue
        cover_url = _static_hifly_avatar_cover_url(request, origin_cover_url) or _build_avatar_cover_proxy_url(request, origin_cover_url)
        seen.add(avatar_id)
        result.append(
            {
                "avatar": avatar_id,
                "title": title,
                "kind": item.get("kind"),
                "section": "public",
                "section_label": "公共数字人",
                "image_url": cover_url,
                "cover_url": cover_url,
                "detail_url": cover_url,
                "origin_cover_url": origin_cover_url,
                "cover_guessed": False,
                "cover_rank": 1,
                "material_count": None,
                "tags": ["公共数字人"],
            }
        )
    return _sort_public_avatar_rows(_apply_public_avatar_material_counts(result))


def _voice_title_parts(title: str) -> tuple[str, str]:
    text = " ".join(str(title or "").split())
    if not text or " " not in text:
        return text or "未命名声音", ""
    base, style = text.split(" ", 1)
    return base.strip() or text, style.strip()


def _enrich_public_voice_rows(rows: List[Dict[str, Any]], request: Optional[Request] = None) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for item in rows or []:
        voice_id = str(item.get("voice") or item.get("voice_id") or "").strip()
        if not voice_id:
            continue
        title = str(item.get("title") or voice_id).strip() or voice_id
        base_title, style_label = _voice_title_parts(title)
        group_key = base_title if base_title and base_title != "默认风格" else voice_id
        group = groups.setdefault(
            group_key,
            {
                "voice": voice_id,
                "title": base_title or title,
                "kind": item.get("kind"),
                "section": "public",
                "section_label": "公共声音",
                "demo_url": "",
                "cover_url": "",
                "cover_rank": 9,
                "styles": [],
                "tags": ["公共声音"],
                "search_text": "",
            },
        )
        demo_url = _absolute_public_url(
            str(item.get("demo_url") or item.get("audio_url") or item.get("preview_url") or "").strip(),
            request,
        )
        style = {
            "voice": voice_id,
            "title": title,
            "label": style_label or "默认风格",
            "demo_url": demo_url,
            "rate": str(item.get("rate") or "").strip(),
            "pitch": str(item.get("pitch") or "").strip(),
            "volume": str(item.get("volume") or "").strip(),
            "language": str(item.get("languages") or item.get("language") or "").strip(),
        }
        group["styles"].append(style)
        if demo_url and not group.get("demo_url"):
            group["demo_url"] = demo_url
            group["voice"] = voice_id
    result: List[Dict[str, Any]] = []
    for group in groups.values():
        styles = group.get("styles") or []
        if not styles:
            continue
        group["style_count"] = len(styles)
        group["search_text"] = " ".join([str(group.get("title") or "")] + [str(s.get("label") or "") for s in styles])
        result.append(group)
    return sorted(result, key=lambda row: (_base_name(row.get("title") or ""), row.get("title") or ""))


def _load_preview_manifest_voice_rows(request: Optional[Request]) -> List[Dict[str, Any]]:
    data = _read_json_file(_HIFLY_PREVIEWS_MANIFEST_PATH)
    groups = data.get("groups") or []
    if not isinstance(groups, list):
        return []
    result: List[Dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        title = str(group.get("title") or "").strip()
        cover_url = _absolute_public_url(str(group.get("cover_url") or "").strip(), request)
        members = group.get("members") or []
        if not isinstance(members, list):
            continue
        styles: List[Dict[str, Any]] = []
        for member in members:
            if not isinstance(member, dict):
                continue
            member_id = member.get("id")
            if member_id in (None, ""):
                continue
            preview_url = _absolute_public_url(str(member.get("preview_url") or "").strip(), request)
            if not preview_url:
                continue
            member_title = str(member.get("title") or "").strip() or title
            voice_value = f"consumer_{member_id}"
            styles.append(
                {
                    "voice": voice_value,
                    "title": member_title or title,
                    "label": member_title if member_title and member_title != title else "默认风格",
                    "demo_url": preview_url,
                    "rate": "",
                    "pitch": "",
                    "volume": "",
                    "language": "",
                    "preview_text": str(member.get("preview_text") or "").strip(),
                    "tts_level": member.get("tts_level"),
                }
            )
        if not styles:
            continue
        primary = styles[0]
        result.append(
            {
                "voice": str(primary.get("voice") or ""),
                "title": title or str(primary.get("title") or ""),
                "kind": "consumer_public",
                "section": "public",
                "section_label": "公共声音",
                "demo_url": str(primary.get("demo_url") or ""),
                "cover_url": cover_url,
                "cover_rank": 1 if cover_url else 9,
                "style_count": len(styles),
                "styles": styles,
                "tags": ["公共声音", "可试听"],
                "search_text": " ".join(
                    [title or str(primary.get("title") or "")]
                    + [str(s.get("label") or "") for s in styles]
                ).strip(),
            }
        )
    return result


def _merge_public_voice_rows(*row_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    merged: List[Dict[str, Any]] = []
    for rows in row_groups:
        for row in rows or []:
            key = str(row.get("voice") or row.get("title") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(row)
    return merged


async def _fetch_all_hifly_pages(path: str, token: Optional[str], kind: int, page_size: int, max_pages: int = 12) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        payload = await _get(path, token, {"page": page, "size": page_size, "kind": kind})
        page_rows = payload.get("data") or []
        if not isinstance(page_rows, list) or not page_rows:
            break
        rows.extend(page_rows)
        if len(page_rows) < page_size:
            break
    return rows


@router.post("/api/hifly/avatar/library")
async def hifly_avatar_library(body: HiflyAvatarLibraryBody, request: Request):
    public_seed_rows = _load_seed_public_avatar_rows()
    public_api_rows: List[Dict[str, Any]] = []
    source = "local_seed" if public_seed_rows else "empty"
    if _has_hifly_token(body.token):
        try:
            public_api_rows = await _fetch_all_hifly_pages(
                "/api/v2/hifly/avatar/list",
                body.token,
                2,
                _MAX_AVATAR_PAGE_SIZE,
            )
            source = "local_seed+hifly" if public_seed_rows else "hifly"
        except HTTPException:
            if not public_seed_rows:
                raise
            logger.warning("[hifly_assets] avatar/library HiFly fetch failed; using local seed", exc_info=True)
    public = _enrich_public_avatar_rows(public_seed_rows + public_api_rows, request)
    return {
        "ok": True,
        "mine": [],
        "public": public,
        "mine_supported": False,
        "mine_message": "我的数字人请走 /api/hifly/my/avatar/list",
        "public_total": len(public),
        "public_page": 1,
        "public_size": len(public),
        "public_has_more": False,
        "mine_total": 0,
        "using_default_token": bool((settings.hifly_default_token or "").strip() and not (body.token or "").strip()),
        "source": source,
    }


@router.post("/api/hifly/voice/library")
async def hifly_voice_library(body: HiflyTokenBody, request: Request):
    public_seed = _enrich_public_voice_rows(_load_seed_public_voice_rows(), request)
    public_manifest = _load_preview_manifest_voice_rows(request)
    public_api: List[Dict[str, Any]] = []
    source = "local_seed"
    if _has_hifly_token(body.token):
        try:
            public_rows = await _fetch_all_hifly_pages(
                "/api/v2/hifly/voice/list",
                body.token,
                2,
                _MAX_VOICE_PAGE_SIZE,
            )
            public_api = _enrich_public_voice_rows(public_rows, request)
            source = "hifly"
        except HTTPException:
            if not (public_seed or public_manifest):
                raise
            logger.warning("[hifly_assets] voice/library HiFly fetch failed; using local seed", exc_info=True)
    public = _merge_public_voice_rows(public_manifest, public_api, public_seed)
    return {
        "ok": True,
        "mine": [],
        "public": public,
        "mine_supported": False,
        "mine_message": "我的声音请走 /api/hifly/my/voice/list",
        "public_total": len(public),
        "manifest_count": len(public_manifest),
        "using_default_token": bool((settings.hifly_default_token or "").strip() and not (body.token or "").strip()),
        "source": source,
    }


@router.post("/api/hifly/my/avatar/create-by-image-upload")
async def create_my_avatar_by_image_upload(
    request: Request,
    token: Optional[str] = Form(None),
    title: str = Form("未命名数字人"),
    model: int = Form(2),
    aigc_flag: int = Form(0),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    uploaded = await _upload_file_to_hifly(
        token,
        file,
        allowed_exts=_IMAGE_EXTS,
        max_bytes=_IMAGE_MAX_BYTES,
        fallback_ext="png",
    )
    payload = {
        "title": _safe_title(title, "未命名数字人"),
        "file_id": uploaded["file_id"],
        "model": 1 if int(model or 0) == 1 else 2,
        "aigc_flag": int(aigc_flag or 0),
    }
    created = await _post("/api/v2/hifly/avatar/create_by_image", token, payload)
    task_id = str(_pick_field(created, "task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=502, detail="HiFly 未返回 task_id")

    source_asset = _persist_input_asset(db, current_user.id, uploaded, "image")
    row = UserHiflyAvatarAsset(
        user_id=current_user.id,
        title=payload["title"],
        source_type="image",
        status="processing",
        hifly_task_id=task_id,
        file_id=uploaded["file_id"],
        aigc_flag=payload["aigc_flag"],
        model=payload["model"],
        meta={
            "create_raw": created,
            "upload_meta": _upload_meta_for_store(uploaded),
            "source_asset_id": source_asset.asset_id if source_asset else "",
        },
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _normalize_avatar_asset(row, request)}


@router.post("/api/hifly/my/avatar/create-by-video-upload")
async def create_my_avatar_by_video_upload(
    request: Request,
    token: Optional[str] = Form(None),
    title: str = Form("未命名数字人"),
    aigc_flag: int = Form(0),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    uploaded = await _upload_file_to_hifly(
        token,
        file,
        allowed_exts=_VIDEO_EXTS,
        max_bytes=_VIDEO_MAX_BYTES,
        fallback_ext="mp4",
    )
    payload = {
        "title": _safe_title(title, "未命名数字人"),
        "file_id": uploaded["file_id"],
        "aigc_flag": int(aigc_flag or 0),
    }
    created = await _post("/api/v2/hifly/avatar/create_by_video", token, payload)
    task_id = str(_pick_field(created, "task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=502, detail="HiFly 未返回 task_id")

    source_asset = _persist_input_asset(db, current_user.id, uploaded, "video")
    row = UserHiflyAvatarAsset(
        user_id=current_user.id,
        title=payload["title"],
        source_type="video",
        status="processing",
        hifly_task_id=task_id,
        file_id=uploaded["file_id"],
        aigc_flag=payload["aigc_flag"],
        meta={
            "create_raw": created,
            "upload_meta": _upload_meta_for_store(uploaded),
            "source_asset_id": source_asset.asset_id if source_asset else "",
        },
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _normalize_avatar_asset(row, request)}


@router.post("/api/hifly/my/voice/create-upload")
async def create_my_voice_upload(
    request: Request,
    token: Optional[str] = Form(None),
    title: str = Form(...),
    voice_type: int = Form(8),
    languages: str = Form("zh"),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    uploaded = await _upload_file_to_hifly(
        token,
        file,
        allowed_exts=_AUDIO_EXTS,
        max_bytes=_AUDIO_MAX_BYTES,
        fallback_ext="mp3",
    )
    source_asset = _persist_input_asset(db, current_user.id, uploaded, "audio")
    if source_asset:
        uploaded["source_asset_id"] = source_asset.asset_id
        uploaded["source_url"] = source_asset.source_url or ""
    payload = {
        "title": _safe_title(title, "未命名声音"),
        "voice_type": int(voice_type or 8),
        "file_id": uploaded["file_id"],
        "languages": (languages or "zh").strip() or "zh",
    }
    created = await _post("/api/v2/hifly/voice/create", token, payload)
    task_id = str(_pick_field(created, "task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=502, detail="HiFly 未返回 task_id")

    row = UserHiflyVoiceAsset(
        user_id=current_user.id,
        title=payload["title"],
        status="processing",
        hifly_task_id=task_id,
        file_id=uploaded["file_id"],
        voice_type=payload["voice_type"],
        languages=payload["languages"],
        meta={"create_raw": created, "upload_meta": _upload_meta_for_store(uploaded)},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _normalize_voice_asset(row, request)}


@router.post("/api/hifly/my/avatar/task")
async def poll_my_avatar_task(
    request: Request,
    body: HiflyTaskBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(UserHiflyAvatarAsset)
        .filter(
            UserHiflyAvatarAsset.user_id == current_user.id,
            UserHiflyAvatarAsset.hifly_task_id == body.task_id.strip(),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="未找到该数字人任务")

    payload = await _get("/api/v2/hifly/avatar/task", body.token, {"task_id": row.hifly_task_id})
    status_num = int(_pick_field(payload, "status") or 0)
    row.status = _local_status(status_num)
    row.title = _safe_title(str(_pick_field(payload, "title") or row.title or "未命名数字人"), "未命名数字人", 128)
    row.hifly_avatar_id = str(_pick_field(payload, "avatar") or row.hifly_avatar_id or "").strip() or None
    row.cover_url = _pick_cover(payload) or row.cover_url
    row.error_message = str(_pick_field(payload, "message") or "").strip() or None
    row.meta = dict(row.meta or {}, task_raw=payload)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": status_num != 4,
        "task_id": row.hifly_task_id,
        "status": status_num,
        "status_text": _status_text(status_num),
        "item": _normalize_avatar_asset(row, request),
        "raw": payload,
    }


@router.get("/api/hifly/my/voice/{voice_asset_id}/preview")
async def preview_my_voice_audio(
    voice_asset_id: int,
    token: str = Query(...),
    expiry: int = Query(...),
    db: Session = Depends(get_db),
):
    expected = _voice_preview_token(voice_asset_id, expiry)
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="无效的试听签名")
    if int(time.time()) > int(expiry):
        raise HTTPException(status_code=403, detail="试听链接已过期")

    row = db.query(UserHiflyVoiceAsset).filter(UserHiflyVoiceAsset.id == int(voice_asset_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail="试听记录不存在")

    target_url = _resolve_voice_preview_source(row, None)
    if not target_url:
        raise HTTPException(status_code=404, detail="当前声音暂无可用试听音频")

    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False, follow_redirects=True) as client:
            resp = await client.get(target_url, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as exc:
        logger.warning("[hifly_assets] preview proxy request failed row_id=%s err=%s", voice_asset_id, exc)
        raise HTTPException(status_code=503, detail="试听音频拉取失败") from exc

    if resp.status_code >= 400:
        raise HTTPException(status_code=503, detail=f"试听音频源站不可用 HTTP {resp.status_code}")

    media_type = (resp.headers.get("content-type") or "audio/mpeg").split(";")[0].strip() or "audio/mpeg"
    return Response(
        content=resp.content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/api/hifly/my/voice/task")
async def poll_my_voice_task(
    request: Request,
    body: HiflyTaskBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(UserHiflyVoiceAsset)
        .filter(
            UserHiflyVoiceAsset.user_id == current_user.id,
            UserHiflyVoiceAsset.hifly_task_id == body.task_id.strip(),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="未找到该声音任务")

    payload = await _get("/api/v2/hifly/voice/task", body.token, {"task_id": row.hifly_task_id})
    status_num = int(_pick_field(payload, "status") or 0)
    row.status = _local_status(status_num)
    row.title = _safe_title(str(_pick_field(payload, "title") or row.title or "未命名声音"), "未命名声音", 128)
    row.hifly_voice_id = str(_pick_field(payload, "voice", "voice_id") or row.hifly_voice_id or "").strip() or None
    row.demo_url = _pick_demo(payload) or row.demo_url
    row.cover_url = _pick_cover(payload) or row.cover_url
    row.error_message = str(_pick_field(payload, "message") or "").strip() or None
    row.meta = dict(row.meta or {}, task_raw=payload)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": status_num != 4,
        "task_id": row.hifly_task_id,
        "status": status_num,
        "status_text": _status_text(status_num),
        "item": _normalize_voice_asset(row, request),
        "raw": payload,
    }


@router.post("/api/hifly/my/voice/edit")
async def edit_my_voice_params(
    request: Request,
    body: HiflyVoiceEditBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    voice_id = (body.voice or "").strip()
    row = (
        db.query(UserHiflyVoiceAsset)
        .filter(
            UserHiflyVoiceAsset.user_id == current_user.id,
            UserHiflyVoiceAsset.hifly_voice_id == voice_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="声音不存在或不属于当前账号")
    rate = _voice_param_text(body.rate, "1.0", 0.5, 2.0, "语速")
    volume = _voice_param_text(body.volume, "1.0", 0.1, 2.0, "音量")
    pitch = _voice_param_text(body.pitch, "1.0", 0.1, 2.0, "语调")
    payload = {"voice": voice_id, "rate": rate, "volume": volume, "pitch": pitch}
    meta = dict(row.meta or {})
    meta["voice_params"] = {"rate": rate, "volume": volume, "pitch": pitch}
    meta["voice_edit_at"] = datetime.utcnow().isoformat() + "Z"
    result: Dict[str, Any] = {}
    synced = False
    sync_error = ""
    if (body.token or "").strip() or (getattr(settings, "hifly_default_token", None) or "").strip():
        try:
            result = await _post("/api/v2/hifly/voice/edit", body.token, payload)
            synced = True
            meta["voice_edit_raw"] = result
            meta["voice_edit_synced_at"] = datetime.utcnow().isoformat() + "Z"
        except HTTPException as exc:
            sync_error = str(exc.detail or "HiFly 同步失败")
            meta["voice_edit_sync_error"] = sync_error
    else:
        sync_error = "本地服务端未配置 HIFLY_DEFAULT_TOKEN，已仅保存到本地，生成口播时会随任务参数提交。"
        meta["voice_edit_sync_error"] = sync_error
    row.meta = meta
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": True,
        "synced": synced,
        "sync_error": sync_error,
        "request_id": str(result.get("request_id") or ""),
        "message": str(result.get("message") or ""),
        "params": meta["voice_params"],
        "item": _normalize_voice_asset(row, request),
        "raw": result,
    }


@router.get("/api/hifly/my/avatar/list")
def list_my_avatars(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    q: str = Query("", alias="keyword"),
    status: str = Query(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(UserHiflyAvatarAsset).filter(
        UserHiflyAvatarAsset.user_id == current_user.id,
        UserHiflyAvatarAsset.status != "deleted",
    )
    keyword = (q or "").strip()
    if keyword:
        query = query.filter(UserHiflyAvatarAsset.title.contains(keyword))
    status_value = (status or "").strip()
    if status_value:
        query = query.filter(UserHiflyAvatarAsset.status == status_value)

    total = query.count()
    rows = (
        query.order_by(UserHiflyAvatarAsset.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {
        "ok": True,
        "items": [_normalize_avatar_asset(row, request) for row in rows],
        "page": page,
        "size": size,
        "total": total,
    }


@router.delete("/api/hifly/my/avatar/{avatar_asset_id}")
def delete_my_avatar(
    avatar_asset_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(UserHiflyAvatarAsset)
        .filter(
            UserHiflyAvatarAsset.id == avatar_asset_id,
            UserHiflyAvatarAsset.user_id == current_user.id,
            UserHiflyAvatarAsset.status != "deleted",
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="数字人不存在或已删除")
    meta = dict(row.meta or {})
    meta["deleted"] = True
    meta["deleted_at"] = datetime.utcnow().isoformat()
    row.meta = meta
    row.status = "deleted"
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "deleted": avatar_asset_id}


# ── 口播视频任务持久化 ──────────────────────────────────────────────

class HiflyVideoCreateBody(BaseModel):
    title: str = Field("数字人口播", max_length=128)
    avatar: str = Field(..., min_length=1, max_length=128)
    voice: str = Field(..., min_length=1, max_length=128)
    text: str = Field(..., min_length=1, max_length=10000)
    st_show: int = 0
    aigc_flag: int = 0
    rate: Optional[str] = None
    volume: Optional[str] = None
    pitch: Optional[str] = None
    avatar_title: Optional[str] = None
    avatar_image_url: Optional[str] = None
    voice_title: Optional[str] = None
    token: Optional[str] = None


def _pick_nested(payload: Dict[str, Any], key: str) -> Any:
    """HiFly 部分接口把业务字段包在 data 里，这里做统一 fallback。"""
    if payload.get(key) not in (None, ""):
        return payload.get(key)
    nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return nested.get(key)


def _video_item_url(row: UserHiflyVideoAsset, request: Optional[Request]) -> str:
    """优先返回永久 URL，其次 /api/assets/file 签名链，最后退回 HiFly 临时链。"""
    if row.asset_video_url:
        return row.asset_video_url
    if row.asset_id and request is not None:
        signed = build_asset_file_url(request, row.asset_id)
        if signed:
            return signed
    return row.source_video_url or ""


def _normalize_video_asset(row: UserHiflyVideoAsset, request: Optional[Request] = None) -> Dict[str, Any]:
    video_url = _video_item_url(row, request)
    meta = dict(row.meta or {})
    source_mode = str(meta.get("source_mode") or ("tts" if row.text else "audio")).strip() or "tts"
    return {
        "id": row.id,
        "task_id": row.hifly_task_id,
        "title": row.title,
        "avatar": row.avatar_id or "",
        "voice": row.voice_id or "",
        "source_mode": source_mode,
        "status": row.status,
        "status_text": {
            "waiting": "等待中",
            "processing": "处理中",
            "success": "已完成",
            "failed": "失败",
        }.get(row.status, "处理中"),
        "video_url": video_url,
        "cover_url": str(meta.get("avatar_image_url") or "").strip(),
        "avatar_title": str(meta.get("avatar_title") or "").strip(),
        "voice_title": str(meta.get("voice_title") or "").strip(),
        "asset_video_url": row.asset_video_url or "",
        "asset_id": row.asset_id or "",
        "source_video_url": row.source_video_url or "",
        "text": row.text or "",
        "duration": row.duration,
        "aigc_flag": row.aigc_flag,
        "st_show": row.st_show,
        "message": row.error_message or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/api/hifly/avatar/cover")
async def proxy_public_avatar_cover(
    url: str = Query(..., min_length=1),
    token: str = Query(..., min_length=1),
    expiry: int = Query(...),
):
    target_url = unquote(str(url or "").strip())
    if not target_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="封面地址无效")
    expected = _avatar_cover_token(target_url, expiry)
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="无效的封面签名")
    if int(time.time()) > int(expiry):
        raise HTTPException(status_code=403, detail="封面链接已过期")
    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False, follow_redirects=True) as client:
            resp = await client.get(
                target_url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                },
            )
    except Exception as exc:
        logger.warning("[hifly_assets] avatar cover proxy failed url=%s err=%s", target_url[:180], exc)
        raise HTTPException(status_code=503, detail="数字人封面拉取失败") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=503, detail=f"数字人封面源站不可用 HTTP {resp.status_code}")
    media_type = (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip() or "image/jpeg"
    if not media_type.startswith("image/"):
        media_type = "image/jpeg"
    return Response(
        content=resp.content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _video_share_token(video_id: int) -> str:
    raw = str(int(video_id))
    sig = hmac.new(_HIFLY_SHARE_SECRET, raw.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
    return f"{raw}.{sig}"


def _video_id_from_share_token(token: str) -> int:
    raw = (token or "").strip()
    if "." not in raw:
        raise HTTPException(status_code=400, detail="分享链接无效")
    video_id_text, sig = raw.split(".", 1)
    if not video_id_text.isdigit():
        raise HTTPException(status_code=400, detail="分享链接无效")
    expected = _video_share_token(int(video_id_text)).split(".", 1)[1]
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=400, detail="分享链接无效")
    return int(video_id_text)


async def _download_bytes(url: str) -> tuple[bytes, str]:
    """拉取 HiFly 临时视频返回 (data, content_type)。"""
    async with httpx.AsyncClient(timeout=600.0, trust_env=False, follow_redirects=True) as client:
        resp = await client.get(url)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"下载 HiFly 视频失败 HTTP {resp.status_code}")
    return resp.content, (resp.headers.get("content-type") or "video/mp4").split(";")[0].strip() or "video/mp4"


async def _persist_video_result(
    row: UserHiflyVideoAsset,
    request: Optional[Request],
    source_url: str,
    db: Session,
) -> None:
    """HiFly 状态=3 时把临时视频拉回来，转存到 TOS/Asset，更新永久 URL。失败时保留 source_video_url。"""
    if not source_url:
        return
    if row.asset_video_url:
        return  # 已经转存过
    try:
        data, content_type = await _download_bytes(source_url)
        ext = ".mp4"
        if "webm" in content_type:
            ext = ".webm"
        elif "quicktime" in content_type or "mov" in content_type:
            ext = ".mov"
        asset_id, filename_or_key, file_size, public_url = _save_bytes_or_tos(data, ext, content_type)
        asset = Asset(
            asset_id=asset_id,
            user_id=row.user_id,
            filename=filename_or_key,
            media_type="video",
            file_size=file_size,
            source_url=public_url,
            tags="hifly,video_tts",
            meta={"hifly_task_id": row.hifly_task_id, "title": row.title},
        )
        db.add(asset)
        db.flush()
        row.asset_id = asset_id
        # 有 TOS 时用公网直链；无 TOS 时在返回前端时再用 build_asset_file_url 生成签名链
        row.asset_video_url = public_url or ""
        logger.info(
            "[hifly] video task %s 已转存 asset_id=%s tos=%s",
            row.hifly_task_id,
            asset_id,
            "yes" if public_url else "no",
        )
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("[hifly] 转存口播视频失败 task_id=%s", row.hifly_task_id)


@router.post("/api/hifly/my/video/create-by-tts")
async def create_my_video_by_tts(
    request: Request,
    body: HiflyVideoCreateBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    title = _safe_title(body.title, "数字人口播", 128)
    voice_value = body.voice.strip()
    if voice_value.startswith("consumer_"):
        raise HTTPException(
            status_code=400,
            detail="该公共声音目前仅支持试听，暂不支持用于生成口播视频。请改选「我的声音」或带 voice id 的公共声音。",
        )
    payload = {
        "title": title[:20] or "数字人口播",
        "avatar": body.avatar.strip(),
        "voice": voice_value,
        "text": body.text.strip(),
        "st_show": 1 if int(body.st_show or 0) == 1 else 0,
        "aigc_flag": int(body.aigc_flag or 0),
    }
    voice_params: Dict[str, str] = {}
    if body.rate is not None:
        voice_params["rate"] = _voice_param_text(body.rate, "1.0", 0.5, 2.0, "语速")
    if body.volume is not None:
        voice_params["volume"] = _voice_param_text(body.volume, "1.0", 0.1, 2.0, "音量")
    if body.pitch is not None:
        voice_params["pitch"] = _voice_param_text(body.pitch, "1.0", 0.1, 2.0, "语调")
    if not voice_params:
        voice_row = (
            db.query(UserHiflyVoiceAsset)
            .filter(UserHiflyVoiceAsset.user_id == current_user.id, UserHiflyVoiceAsset.hifly_voice_id == voice_value)
            .first()
        )
        if voice_row and isinstance(voice_row.meta, dict) and isinstance(voice_row.meta.get("voice_params"), dict):
            stored_params = voice_row.meta.get("voice_params") or {}
            for key in ("rate", "volume", "pitch"):
                value = str(stored_params.get(key) or "").strip()
                if value:
                    voice_params[key] = value
    payload.update(voice_params)
    billing = await _hifly_pre_deduct_tts(request, payload)
    try:
        created = await _post("/api/v2/hifly/video/create_by_tts", body.token, payload)
        task_id = str(_pick_nested(created, "task_id") or "").strip()
        if not task_id:
            await _hifly_refund_tts(request, float(billing.get("credits_pre_deducted") or 0))
            raise HTTPException(status_code=502, detail="HiFly 未返回 task_id")
    except HTTPException:
        if "task_id" not in locals():
            await _hifly_refund_tts(request, float(billing.get("credits_pre_deducted") or 0))
        raise
    request_id = str(_pick_nested(created, "request_id") or "").strip()

    row = UserHiflyVideoAsset(
        user_id=current_user.id,
        title=title,
        status="processing",
        hifly_task_id=task_id,
        avatar_id=body.avatar.strip() or None,
        voice_id=voice_value or None,
        text=body.text.strip(),
        aigc_flag=payload["aigc_flag"],
        st_show=payload["st_show"],
        meta={
            "create_raw": created,
            "create_payload": payload,
            "avatar_title": (body.avatar_title or "").strip(),
            "avatar_image_url": (body.avatar_image_url or "").strip(),
            "voice_title": (body.voice_title or "").strip(),
            "billing": {
                "capability_id": _HIFLY_TTS_CAPABILITY_ID,
                "billing_status": "pending",
                "credits_pre_deducted": billing.get("credits_pre_deducted"),
                "estimated_seconds": billing.get("estimated_seconds"),
                "expected_credits": billing.get("expected_credits"),
                "request_id": request_id,
                "created_at": datetime.utcnow().isoformat() + "Z",
            },
        },
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "task_id": task_id, "request_id": request_id, "billing": (row.meta or {}).get("billing") or {}, "item": _normalize_video_asset(row)}


@router.post("/api/hifly/my/video/create-by-audio-upload")
async def create_my_video_by_audio_upload(
    request: Request,
    avatar: str = Form(...),
    title: str = Form("数字人口播"),
    aigc_flag: int = Form(0),
    audio_duration: float = Form(0),
    token: Optional[str] = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    avatar_value = str(avatar or "").strip()
    if not avatar_value:
        raise HTTPException(status_code=400, detail="请先选择数字人")

    uploaded = await _upload_file_to_hifly(
        token,
        file,
        allowed_exts=_AUDIO_EXTS,
        max_bytes=_AUDIO_DRIVE_MAX_BYTES,
        fallback_ext="mp3",
    )
    title_value = _safe_title(title, "数字人口播", 128)
    payload = {
        "title": title_value[:20] or "数字人口播",
        "avatar": avatar_value,
        "file_id": uploaded["file_id"],
        "aigc_flag": int(aigc_flag or 0),
    }
    billing = await _hifly_pre_deduct_audio(request, {"audio_size": uploaded.get("size") or 0}, _estimate_audio_seconds(audio_duration))
    try:
        created = await _post("/api/v2/hifly/video/create_by_audio", token, payload)
        task_id = str(_pick_nested(created, "task_id") or "").strip()
        if not task_id:
            await _hifly_refund_capability(request, _HIFLY_AUDIO_CAPABILITY_ID, float(billing.get("credits_pre_deducted") or 0))
            raise HTTPException(status_code=502, detail="HiFly 未返回 task_id")
    except HTTPException:
        if "task_id" not in locals():
            await _hifly_refund_capability(request, _HIFLY_AUDIO_CAPABILITY_ID, float(billing.get("credits_pre_deducted") or 0))
        raise
    request_id = str(_pick_nested(created, "request_id") or "").strip()

    source_asset = _persist_input_asset(db, current_user.id, uploaded, "audio")
    if source_asset:
        uploaded["source_asset_id"] = source_asset.asset_id
        uploaded["source_url"] = source_asset.source_url

    row = UserHiflyVideoAsset(
        user_id=current_user.id,
        title=title_value,
        status="processing",
        hifly_task_id=task_id,
        avatar_id=avatar_value or None,
        aigc_flag=payload["aigc_flag"],
        st_show=0,
        meta={
            "create_raw": created,
            "create_payload": payload,
            "upload_meta": _upload_meta_for_store(uploaded),
            "source_asset_id": source_asset.asset_id if source_asset else "",
            "source_mode": "audio",
            "billing": {
                "capability_id": _HIFLY_AUDIO_CAPABILITY_ID,
                "billing_status": "pending",
                "credits_pre_deducted": billing.get("credits_pre_deducted"),
                "estimated_seconds": billing.get("estimated_seconds"),
                "expected_credits": billing.get("expected_credits"),
                "request_id": request_id,
                "created_at": datetime.utcnow().isoformat() + "Z",
            },
        },
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "task_id": task_id, "request_id": request_id, "billing": (row.meta or {}).get("billing") or {}, "item": _normalize_video_asset(row)}


@router.post("/api/hifly/my/video/task")
async def poll_my_video_task(
    request: Request,
    body: HiflyTaskBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(UserHiflyVideoAsset)
        .filter(
            UserHiflyVideoAsset.user_id == current_user.id,
            UserHiflyVideoAsset.hifly_task_id == body.task_id.strip(),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="未找到该口播视频任务")

    payload = await _get("/api/v2/hifly/video/task", body.token, {"task_id": row.hifly_task_id})
    status_num = int(_pick_nested(payload, "status") or 0)
    video_url = str(
        _pick_nested(payload, "video_Url")
        or _pick_nested(payload, "video_url")
        or _pick_nested(payload, "videoUrl")
        or ""
    ).strip()
    duration_raw = _pick_nested(payload, "duration")
    row.status = _local_status(status_num)
    if video_url:
        row.source_video_url = video_url
    if duration_raw not in (None, ""):
        try:
            row.duration = int(duration_raw)
        except (TypeError, ValueError):
            pass
    row.error_message = str(_pick_nested(payload, "message") or "").strip() or None
    meta = dict(row.meta or {}, task_raw=payload)
    billing = dict(meta.get("billing") or {})
    if billing and billing.get("billing_status") not in ("settled", "refunded"):
        if status_num == 3:
            billing_result = {
                "duration": duration_raw,
                "video_url": video_url,
                "request_id": str(_pick_nested(payload, "request_id") or billing.get("request_id") or ""),
            }
            try:
                billing = await _hifly_record_video_billing(request, row, billing_result)
            except HTTPException as exc:
                logger.exception("[hifly-billing] settle failed task_id=%s", row.hifly_task_id)
                billing.update({"billing_status": "settle_failed", "billing_error": str(exc.detail)[:500], "updated_at": datetime.utcnow().isoformat() + "Z"})
        elif status_num == 4:
            credits_pre = float(billing.get("credits_pre_deducted") or 0)
            capability_id = str(billing.get("capability_id") or _HIFLY_TTS_CAPABILITY_ID).strip() or _HIFLY_TTS_CAPABILITY_ID
            await _hifly_refund_capability(request, capability_id, credits_pre)
            billing.update({"billing_status": "refunded", "credits_refunded": credits_pre, "refunded_at": datetime.utcnow().isoformat() + "Z"})
    meta["billing"] = billing
    row.meta = meta

    # 状态=3 且尚未转存过：把 HiFly 临时视频下载后写入 TOS/Asset，记录永久 URL
    if status_num == 3 and video_url and not row.asset_video_url:
        await _persist_video_result(row, request, video_url, db)

    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "ok": status_num != 4,
        "task_id": row.hifly_task_id,
        "status": status_num,
        "status_text": _status_text(status_num),
        "video_url": _video_item_url(row, request),
        "duration": row.duration,
        "message": row.error_message or "",
        "billing": ((row.meta or {}).get("billing") or {}),
        "item": _normalize_video_asset(row, request),
        "raw": payload,
    }


@router.get("/api/hifly/my/video/list")
def list_my_videos(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    q: str = Query("", alias="keyword"),
    status: str = Query(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(UserHiflyVideoAsset).filter(UserHiflyVideoAsset.user_id == current_user.id)
    keyword = (q or "").strip()
    if keyword:
        query = query.filter(UserHiflyVideoAsset.title.contains(keyword))
    status_value = (status or "").strip()
    if status_value:
        query = query.filter(UserHiflyVideoAsset.status == status_value)

    total = query.count()
    rows = (
        query.order_by(UserHiflyVideoAsset.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {
        "ok": True,
        "items": [_normalize_video_asset(row, request) for row in rows],
        "page": page,
        "size": size,
        "total": total,
    }


@router.post("/api/hifly/my/video/{video_id}/share")
def create_my_video_share(
    video_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(UserHiflyVideoAsset)
        .filter(UserHiflyVideoAsset.id == video_id, UserHiflyVideoAsset.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="作品不存在")
    if row.status != "success":
        raise HTTPException(status_code=400, detail="作品生成完成后才能分享")
    return {"ok": True, "share_token": _video_share_token(row.id), "item": _normalize_video_asset(row, request)}


@router.get("/api/hifly/video/share/{share_token}")
def get_shared_video(
    share_token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    video_id = _video_id_from_share_token(share_token)
    row = db.query(UserHiflyVideoAsset).filter(UserHiflyVideoAsset.id == video_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="分享作品不存在")
    return {"ok": True, "item": _normalize_video_asset(row, request)}


@router.delete("/api/hifly/my/video/{video_id}")
def delete_my_video(
    video_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(UserHiflyVideoAsset)
        .filter(UserHiflyVideoAsset.id == video_id, UserHiflyVideoAsset.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/api/hifly/my/voice/list")
def list_my_voices(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    q: str = Query("", alias="keyword"),
    status: str = Query(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(UserHiflyVoiceAsset).filter(UserHiflyVoiceAsset.user_id == current_user.id)
    keyword = (q or "").strip()
    if keyword:
        query = query.filter(UserHiflyVoiceAsset.title.contains(keyword))
    status_value = (status or "").strip()
    if status_value:
        query = query.filter(UserHiflyVoiceAsset.status == status_value)

    total = query.count()
    rows = (
        query.order_by(UserHiflyVoiceAsset.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    changed = False
    for row in rows:
        missing_preview = not str(row.demo_url or "").strip()
        missing_voice_id = not str(row.hifly_voice_id or "").strip()
        if row.status == "success" and (missing_preview or missing_voice_id):
            if _refresh_voice_asset_from_hifly(row):
                db.add(row)
                changed = True
    if changed:
        db.commit()
        for row in rows:
            db.refresh(row)
    return {
        "ok": True,
        "items": [_normalize_voice_asset(row, request) for row in rows],
        "page": page,
        "size": size,
        "total": total,
    }

