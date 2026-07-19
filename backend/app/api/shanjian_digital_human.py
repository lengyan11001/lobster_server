from __future__ import annotations

import base64
import logging
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from ..models import Asset, ShanjianDigitalHumanProfile, ShanjianDigitalHumanVideoTask, User
from .assets import _get_tos_config, _save_bytes_or_tos, get_asset_public_url
from .auth import get_current_user
from .shanjian_smart_clip import _data, _get, _post

logger = logging.getLogger(__name__)
router = APIRouter()

_SHANJIAN_BILLING_CAPABILITY_ID = "hifly.video.create_by_tts"
_SHANJIAN_UNIT_CREDITS_PER_SECOND = 10
_SHANJIAN_TEMPLATE_UNIT_CREDITS_PER_SECOND = 15
_SHANJIAN_TTS_CHARS_PER_SECOND = 4


class _TokenBody(BaseModel):
    token: Optional[str] = None


class ProfileTrainBody(_TokenBody):
    title: str = "未命名数字人"
    mode: str = "image"
    image_url: Optional[str] = None
    image_asset_id: Optional[str] = None
    video_url: Optional[str] = None
    video_asset_id: Optional[str] = None
    auth_video_url: Optional[str] = None
    auth_video_asset_id: Optional[str] = None
    auth_text: str = Field(..., min_length=2, max_length=500)
    callback_url: str = ""
    make_default: bool = True


class ProfileTaskBody(_TokenBody):
    task_id: Optional[str] = None
    profile_id: Optional[int] = None


class SetDefaultBody(BaseModel):
    profile_id: int = Field(..., gt=0)


class CreateVideoBody(_TokenBody):
    profile_id: Optional[int] = None
    virtualman_id: Optional[str] = None
    title: str = "数字人口播"
    text: Optional[str] = None
    speaker_id: Optional[str] = None
    audio_url: Optional[str] = None
    audio_asset_id: Optional[str] = None
    language: str = "zh-CN"
    speed_ratio: float = 1.0
    callback_url: str = ""
    template_scene: str = ""
    style_id: str = ""
    materials: List[Dict[str, Any]] = Field(default_factory=list)
    material_sound_switch: bool = False
    introduce_name: str = ""
    introduce_description: str = ""
    header_switch: bool = True
    material_switch: bool = True
    subtitle_switch: bool = True
    keyword_switch: bool = True
    watermark_show: bool = False
    material_match_way: str = "fuzzyMatch"
    resource_preprocess_method: str = "roughCut"
    material_composition: str = "random"
    video_duration: int = 30


class VideoTaskBody(_TokenBody):
    task_id: Optional[str] = None
    record_id: Optional[int] = None


def _clean_text(value: Optional[str]) -> str:
    return str(value or "").strip()


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
        raise HTTPException(status_code=503, detail="AUTH_SERVER_BASE is not configured; billing is unavailable")
    return base


def _billing_headers(request: Request) -> Dict[str, str]:
    token = _bearer_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Please sign in before generating a digital human video")
    headers: Dict[str, str] = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    installation_id = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    billing_key = (
        (getattr(settings, "lobster_mcp_billing_internal_key", None) or "").strip()
        or (os.environ.get("LOBSTER_MCP_BILLING_INTERNAL_KEY") or "").strip()
    )
    if billing_key:
        headers["X-Lobster-Mcp-Billing"] = billing_key
    return headers


def _estimate_text_seconds(text: str) -> int:
    clean = "".join(str(text or "").split())
    return max(1, int(math.ceil(len(clean) / _SHANJIAN_TTS_CHARS_PER_SECOND)))


def _duration_seconds(value: Any, fallback: int = 1) -> int:
    if value in (None, ""):
        return max(1, int(fallback or 1))
    if isinstance(value, (int, float)):
        return max(1, int(math.ceil(float(value))))
    raw = str(value).strip()
    try:
        return max(1, int(math.ceil(float(raw))))
    except Exception:
        pass
    parts = raw.split(":")
    if len(parts) in {2, 3}:
        try:
            nums = [float(p) for p in parts]
            seconds = nums[-1] + nums[-2] * 60
            if len(nums) == 3:
                seconds += nums[0] * 3600
            return max(1, int(math.ceil(seconds)))
        except Exception:
            pass
    return max(1, int(fallback or 1))


def _billing_unit_credits(template_meta: Optional[Dict[str, Any]]) -> int:
    return _SHANJIAN_TEMPLATE_UNIT_CREDITS_PER_SECOND if template_meta and _clean_text(template_meta.get("style_id")) else _SHANJIAN_UNIT_CREDITS_PER_SECOND


def _estimate_billing_seconds(body: "CreateVideoBody", template_meta: Optional[Dict[str, Any]], text: str) -> int:
    if template_meta:
        return max(1, min(int(getattr(body, "video_duration", None) or template_meta.get("video_duration") or 30), 300))
    if text:
        return _estimate_text_seconds(text)
    return max(1, min(int(getattr(body, "video_duration", None) or 30), 300))


async def _shanjian_pre_deduct(
    *,
    request: Request,
    template_meta: Optional[Dict[str, Any]],
    estimated_seconds: int,
    title: str,
) -> Dict[str, Any]:
    unit = _billing_unit_credits(template_meta)
    expected_credits = int(max(1, estimated_seconds)) * unit
    body = {
        "capability_id": _SHANJIAN_BILLING_CAPABILITY_ID,
        "model": "shanjian-digital-human-template" if unit == _SHANJIAN_TEMPLATE_UNIT_CREDITS_PER_SECOND else "shanjian-digital-human",
        "force_credits": expected_credits,
        "params": {
            "provider": "shanjian",
            "title": _clean_text(title)[:80],
            "estimated_seconds": int(max(1, estimated_seconds)),
            "unit_credits": unit,
            "expected_credits": expected_credits,
            "template_enabled": bool(unit == _SHANJIAN_TEMPLATE_UNIT_CREDITS_PER_SECOND),
            "style_id": _clean_text((template_meta or {}).get("style_id")),
        },
    }
    async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
        resp = await client.post(f"{_billing_base()}/capabilities/pre-deduct", json=body, headers=_billing_headers(request))
    if resp.status_code == 402:
        try:
            detail = (resp.json() if resp.content else {}).get("detail", "balance insufficient")
        except Exception:
            detail = "balance insufficient"
        raise HTTPException(status_code=402, detail=f"算力不足，预计需预扣 {expected_credits} 算力。{detail}")
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录后再生成")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Shanjian billing pre-deduct failed HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    data = resp.json() if resp.content else {}
    try:
        charged = float(data.get("credits_charged"))
    except Exception:
        charged = float(expected_credits)
    return {
        "billing_status": "pre_deducted",
        "capability_id": _SHANJIAN_BILLING_CAPABILITY_ID,
        "provider": "shanjian",
        "unit_credits": unit,
        "estimated_seconds": int(max(1, estimated_seconds)),
        "expected_credits": expected_credits,
        "credits_pre_deducted": charged,
        "template_enabled": bool(unit == _SHANJIAN_TEMPLATE_UNIT_CREDITS_PER_SECOND),
        "raw": data,
    }


async def _shanjian_refund_billing(request: Request, billing: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
    if not isinstance(billing, dict) or billing.get("billing_status") in {"settled", "refunded"}:
        return billing if isinstance(billing, dict) else {}
    credits = float(billing.get("credits_pre_deducted") or 0)
    if credits <= 0:
        billing["billing_status"] = "refunded"
        billing["refund_reason"] = reason
        billing["credits_refunded"] = 0
        return billing
    body = {"capability_id": billing.get("capability_id") or _SHANJIAN_BILLING_CAPABILITY_ID, "credits": credits}
    try:
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
            resp = await client.post(f"{_billing_base()}/capabilities/refund", json=body, headers=_billing_headers(request))
        if resp.status_code >= 400:
            logger.warning("[shanjian-billing] refund failed http=%s body=%s", resp.status_code, (resp.text or "")[:300])
            billing["billing_status"] = "refund_failed"
            billing["billing_error"] = (resp.text or "")[:300]
            return billing
        billing["billing_status"] = "refunded"
        billing["refund_reason"] = reason
        billing["credits_refunded"] = credits
        billing["refunded_at"] = datetime.utcnow().isoformat() + "Z"
    except Exception as exc:
        logger.exception("[shanjian-billing] refund exception")
        billing["billing_status"] = "refund_failed"
        billing["billing_error"] = str(exc)[:300]
    return billing


async def _shanjian_settle_billing(
    *,
    request: Request,
    row: ShanjianDigitalHumanVideoTask,
    billing: Dict[str, Any],
    duration_seconds: int,
    video_url: str,
    stage: str,
) -> Dict[str, Any]:
    if not isinstance(billing, dict) or billing.get("billing_status") in {"settled", "refunded"}:
        return billing if isinstance(billing, dict) else {}
    unit = int(billing.get("unit_credits") or _SHANJIAN_UNIT_CREDITS_PER_SECOND)
    actual_seconds = max(1, int(duration_seconds or billing.get("estimated_seconds") or 1))
    final_credits = actual_seconds * unit
    body = {
        "capability_id": billing.get("capability_id") or _SHANJIAN_BILLING_CAPABILITY_ID,
        "success": True,
        "source": "shanjian_digital_human_video_task",
        "request_payload": {
            "task_id": row.task_id,
            "request_id": row.request_id or "",
            "stage": stage,
            "estimated_seconds": billing.get("estimated_seconds"),
            "unit_credits": unit,
            "template_enabled": bool(billing.get("template_enabled")),
        },
        "response_payload": {"duration": actual_seconds, "video_url": video_url or ""},
        "credits_charged": final_credits,
        "pre_deduct_applied": True,
        "credits_pre_deducted": float(billing.get("credits_pre_deducted") or 0),
        "credits_final": final_credits,
    }
    async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
        resp = await client.post(f"{_billing_base()}/capabilities/record-call", json=body, headers=_billing_headers(request))
    if resp.status_code >= 400:
        logger.warning("[shanjian-billing] settle failed http=%s body=%s", resp.status_code, (resp.text or "")[:300])
        billing["billing_status"] = "settle_failed"
        billing["billing_error"] = (resp.text or "")[:300]
        return billing
    billing["billing_status"] = "settled"
    billing["actual_seconds"] = actual_seconds
    billing["credits_final"] = final_credits
    billing["settled_stage"] = stage
    billing["settled_at"] = datetime.utcnow().isoformat() + "Z"
    billing["record_call_raw"] = resp.json() if resp.content else {}
    return billing


async def _finalize_row_billing(
    *,
    request: Request,
    row: ShanjianDigitalHumanVideoTask,
    status: str,
    duration_value: Any,
    video_url: str,
    stage: str,
    error_message: str = "",
) -> None:
    submit_payload = row.submit_payload if isinstance(row.submit_payload, dict) else {}
    billing = dict(submit_payload.get("billing") or {})
    if not billing or billing.get("billing_status") in {"settled", "refunded"}:
        return
    if status == "succeed":
        fallback = int(billing.get("estimated_seconds") or row.duration or 1)
        billing = await _shanjian_settle_billing(
            request=request,
            row=row,
            billing=billing,
            duration_seconds=_duration_seconds(duration_value, fallback=fallback),
            video_url=video_url,
            stage=stage,
        )
    elif status == "failed":
        billing = await _shanjian_refund_billing(request, billing, reason=error_message or f"{stage}_failed")
    submit_payload["billing"] = billing
    row.submit_payload = submit_payload


def _url_hint(value: str) -> str:
    raw = _clean_text(value)
    if raw.startswith("data:"):
        return "data-url"
    try:
        parsed = urlparse(raw)
        path = (parsed.path or "")[:80]
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    except Exception:
        return raw[:120]


def _audio_ext_from_content(content_type: str, url: str = "") -> str:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    suffix = Path((url or "").split("?", 1)[0].split("#", 1)[0]).suffix.lower()
    if suffix in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}:
        return suffix
    if "wav" in ct:
        return ".wav"
    if "mp4" in ct or "m4a" in ct:
        return ".m4a"
    if "aac" in ct:
        return ".aac"
    if "ogg" in ct:
        return ".ogg"
    if "flac" in ct:
        return ".flac"
    return ".mp3"


async def _download_audio_bytes(audio_url: str) -> tuple[bytes, str]:
    raw = _clean_text(audio_url)
    if raw.startswith("data:"):
        header, _, payload = raw.partition(",")
        if not payload:
            raise HTTPException(status_code=400, detail="audio_url data URL 格式无效")
        content_type = header[5:].split(";", 1)[0].strip() or "audio/mpeg"
        try:
            data = base64.b64decode(payload)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="audio_url data URL 解码失败") from exc
        return data, content_type
    if not raw.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="audio_url 必须是 http(s) 或 data: 音频地址")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "audio/*,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True, trust_env=False) as client:
            resp = await client.get(raw, headers=headers)
            resp.raise_for_status()
            return resp.content, resp.headers.get("content-type", "") or "audio/mpeg"
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"下载音频失败: HTTP {exc.response.status_code}",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"下载音频失败: {type(exc).__name__}: {exc}") from exc


async def _persist_audio_for_shanjian(
    *,
    audio_url: str,
    db: Session,
    current_user: User,
    title: str,
) -> tuple[str, str]:
    raw = _clean_text(audio_url)
    data, content_type = await _download_audio_bytes(raw)
    if not data:
        raise HTTPException(status_code=400, detail="音频内容为空，无法提交数字人任务")
    media_type = (content_type or "audio/mpeg").split(";", 1)[0].strip() or "audio/mpeg"
    ext = _audio_ext_from_content(media_type, raw)
    asset_id, filename_or_key, file_size, public_url = _save_bytes_or_tos(data, ext, media_type)
    if not public_url:
        raise HTTPException(status_code=503, detail="数字人口播音频转存 TOS 失败，无法提交闪剪")
    asset = Asset(
        asset_id=asset_id,
        user_id=int(current_user.id),
        filename=filename_or_key,
        media_type="audio",
        file_size=file_size,
        source_url=public_url,
        prompt=_clean_text(title)[:200],
        model="shanjian-digital-human-tts-audio",
        tags="shanjian,digital-human,audio",
        meta={
            "source": "shanjian_digital_human_audio_transfer",
            "original_url_hint": _url_hint(raw),
            "content_type": media_type,
        },
    )
    db.add(asset)
    db.flush()
    logger.info(
        "[shanjian-dh] audio rehosted user_id=%s asset_id=%s size=%s from=%s to=%s",
        getattr(current_user, "id", ""),
        asset_id,
        file_size,
        _url_hint(raw),
        _url_hint(public_url),
    )
    return public_url, asset_id


def _normalize_mode(value: str) -> str:
    raw = _clean_text(value).lower()
    aliases = {
        "image": "image",
        "image_train": "image",
        "photo": "image",
        "video": "video",
        "pro": "video",
        "professional": "video",
        "fast_video": "fast_video",
        "fast": "fast_video",
    }
    mode = aliases.get(raw)
    if not mode:
        raise HTTPException(status_code=400, detail="mode 仅支持 image / video / fast_video")
    return mode


def _pick_result_value(result: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in result and result.get(key) not in (None, ""):
            return result.get(key)
    return None


def _task_status_text(status: str) -> str:
    mapping = {
        "processing": "处理中",
        "succeed": "已完成",
        "failed": "失败",
    }
    return mapping.get(_clean_text(status), _clean_text(status) or "处理中")


def _resolve_asset_or_url(
    *,
    request: Request,
    db: Session,
    current_user: User,
    url: Optional[str],
    asset_id: Optional[str],
    label: str,
) -> str:
    raw_url = _clean_text(url)
    if raw_url:
        if raw_url.startswith("http://") or raw_url.startswith("https://"):
            return raw_url
        raise HTTPException(status_code=400, detail=f"{label} URL 必须是 http(s) 地址")
    aid = _clean_text(asset_id)
    if not aid:
        raise HTTPException(status_code=400, detail=f"请提供 {label} URL 或 asset_id")
    public_url = get_asset_public_url(aid, int(current_user.id), request, db)
    if not public_url:
        raise HTTPException(status_code=400, detail=f"{label} 素材还没有可用公网地址，请先确认素材已上传成功")
    return public_url




def _media_ext_from_content(content_type: str, url: str = "", fallback: str = ".bin") -> str:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    try:
        suffix = Path(urlparse(url or "").path or "").suffix.lower()
    except Exception:
        suffix = ""
    if suffix in {".mp4", ".mov", ".m4v", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}:
        return suffix
    if "quicktime" in ct:
        return ".mov"
    if "mp4" in ct:
        return ".mp4"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    if "mpeg" in ct or "mp3" in ct:
        return ".mp3"
    if "wav" in ct:
        return ".wav"
    if "m4a" in ct or "aac" in ct:
        return ".m4a"
    return fallback


async def _download_media_bytes(media_url: str, *, accept: str = "*/*") -> tuple[bytes, str]:
    raw = _clean_text(media_url)
    if raw.startswith("data:"):
        header, _, payload = raw.partition(",")
        if not payload:
            raise HTTPException(status_code=400, detail="media data URL 格式无效")
        content_type = header[5:].split(";", 1)[0].strip() or "application/octet-stream"
        try:
            return base64.b64decode(payload), content_type
        except Exception as exc:
            raise HTTPException(status_code=400, detail="media data URL 解码失败") from exc
    if not raw.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="media URL 必须是 http(s) 或 data: 地址")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": accept,
    }
    try:
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True, trust_env=False) as client:
            resp = await client.get(raw, headers=headers)
            resp.raise_for_status()
            return resp.content, resp.headers.get("content-type", "") or "application/octet-stream"
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=f"下载媒体失败: HTTP {exc.response.status_code}") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"下载媒体失败: {type(exc).__name__}: {exc}") from exc


async def _persist_media_for_shanjian(
    *,
    media_url: str,
    db: Session,
    current_user: User,
    title: str,
    media_type: str,
    label: str,
) -> tuple[str, str]:
    raw = _clean_text(media_url)
    accept = "video/*,*/*;q=0.8" if media_type == "video" else ("image/*,*/*;q=0.8" if media_type == "image" else "*/*")
    data, content_type = await _download_media_bytes(raw, accept=accept)
    if not data:
        raise HTTPException(status_code=400, detail=f"{label}内容为空，无法继续模板剪辑")
    media_type_header = (content_type or "application/octet-stream").split(";", 1)[0].strip() or "application/octet-stream"
    fallback = ".mp4" if media_type == "video" else (".jpg" if media_type == "image" else ".bin")
    ext = _media_ext_from_content(media_type_header, raw, fallback=fallback)
    asset_id, filename_or_key, file_size, public_url = _save_bytes_or_tos(data, ext, media_type_header)
    if not public_url:
        raise HTTPException(status_code=503, detail=f"{label}转存 TOS 失败，无法继续模板剪辑")
    asset = Asset(
        asset_id=asset_id,
        user_id=int(current_user.id),
        filename=filename_or_key,
        media_type=media_type,
        file_size=file_size,
        source_url=public_url,
        prompt=_clean_text(title)[:200],
        model="shanjian-digital-human-template-media",
        tags="shanjian,digital-human,template-media",
        meta={
            "source": "shanjian_digital_human_template_transfer",
            "label": label,
            "original_url_hint": _url_hint(raw),
            "content_type": media_type_header,
        },
    )
    db.add(asset)
    db.flush()
    logger.info(
        "[shanjian-dh] media rehosted user_id=%s label=%s media_type=%s asset_id=%s size=%s from=%s to=%s",
        getattr(current_user, "id", ""),
        label,
        media_type,
        asset_id,
        file_size,
        _url_hint(raw),
        _url_hint(public_url),
    )
    return public_url, asset_id


def _template_meta_from_body(body: CreateVideoBody) -> Optional[Dict[str, Any]]:
    style_id = _clean_text(body.style_id)
    template_scene = _clean_text(body.template_scene)
    materials: List[Dict[str, Any]] = []
    for item in (body.materials or [])[:20]:
        if not isinstance(item, dict):
            continue
        kind = _clean_text(item.get("type") or item.get("media_type")).lower()
        if kind not in {"image", "video"}:
            continue
        file_url = _clean_text(
            item.get("fileUrl")
            or item.get("file_url")
            or item.get("url")
            or item.get("source_url")
            or item.get("open_url")
            or item.get("preview_url")
        )
        asset_id = _clean_text(item.get("asset_id") or item.get("assetId"))
        if not (file_url or asset_id):
            continue
        row: Dict[str, Any] = {"type": kind}
        if file_url:
            row["fileUrl"] = file_url
        if asset_id:
            row["asset_id"] = asset_id
        materials.append(row)
    if not (style_id or template_scene or materials or _clean_text(body.introduce_name) or _clean_text(body.introduce_description)):
        return None
    return {
        "template_scene": template_scene or "realMan",
        "style_id": style_id,
        "materials": materials,
        "material_sound_switch": bool(body.material_sound_switch),
        "introduce_name": _clean_text(body.introduce_name),
        "introduce_description": _clean_text(body.introduce_description),
        "header_switch": bool(body.header_switch),
        "material_switch": bool(body.material_switch),
        "subtitle_switch": bool(body.subtitle_switch),
        "keyword_switch": bool(body.keyword_switch),
        "watermark_show": bool(body.watermark_show),
        "material_match_way": _clean_text(body.material_match_way) or "fuzzyMatch",
        "resource_preprocess_method": _clean_text(body.resource_preprocess_method) or "roughCut",
        "material_composition": _clean_text(body.material_composition) or "random",
        "video_duration": int(body.video_duration or 30),
    }


def _template_meta_from_submit_payload(submit_payload: Optional[dict]) -> Optional[Dict[str, Any]]:
    if not isinstance(submit_payload, dict):
        return None
    template = submit_payload.get("template")
    if isinstance(template, dict):
        return template
    legacy = submit_payload.get("templateClip")
    if isinstance(legacy, dict):
        pack_rules = legacy.get("packRules") if isinstance(legacy.get("packRules"), dict) else {}
        process_rules = legacy.get("processRules") if isinstance(legacy.get("processRules"), dict) else {}
        return {
            "template_scene": _clean_text(legacy.get("scene")) or "realMan",
            "style_id": _clean_text(legacy.get("styleId") or legacy.get("style_id")),
            "materials": legacy.get("materials") if isinstance(legacy.get("materials"), list) else [],
            "material_sound_switch": bool(legacy.get("materialSoundSwitch")),
            "introduce_name": _clean_text((legacy.get("introduceCard") or {}).get("name") if isinstance(legacy.get("introduceCard"), dict) else ""),
            "introduce_description": _clean_text((legacy.get("introduceCard") or {}).get("description") if isinstance(legacy.get("introduceCard"), dict) else ""),
            "header_switch": bool(pack_rules.get("headerSwitch", True)),
            "material_switch": bool(pack_rules.get("materialSwitch", True)),
            "subtitle_switch": bool(pack_rules.get("subtitleSwitch", True)),
            "keyword_switch": bool(pack_rules.get("keywordSwitch", True)),
            "watermark_show": bool(process_rules.get("watermarkShow", False)),
            "material_match_way": _clean_text(process_rules.get("materialMatchWay")) or "fuzzyMatch",
            "resource_preprocess_method": _clean_text(process_rules.get("resourcePreprocessMethod")) or "roughCut",
            "material_composition": _clean_text(process_rules.get("materialComposition")) or "random",
            "video_duration": int(process_rules.get("videoDuration") or legacy.get("videoDuration") or 30),
            "clip_task_id": _clean_text(legacy.get("clipTaskId") or legacy.get("clip_task_id")),
            "clip_request_id": _clean_text(legacy.get("clipRequestId") or legacy.get("clip_request_id")),
        }
    style_id = _clean_text(submit_payload.get("styleId") or submit_payload.get("style_id"))
    if style_id:
        return {
            "template_scene": "realMan",
            "style_id": style_id,
            "materials": submit_payload.get("materials") if isinstance(submit_payload.get("materials"), list) else [],
        }
    return None


async def _prepare_template_media_urls(
    *,
    db: Session,
    current_user: User,
    template_meta: Dict[str, Any],
    title: str,
) -> Dict[str, Any]:
    prepared = dict(template_meta or {})
    materials: List[Dict[str, Any]] = []
    for index, item in enumerate((template_meta.get("materials") or [])[:20]):
        if not isinstance(item, dict):
            continue
        kind = _clean_text(item.get("type") or item.get("media_type")).lower()
        if kind not in {"image", "video"}:
            continue
        asset_id = _clean_text(item.get("asset_id") or item.get("assetId"))
        file_url = _clean_text(
            item.get("fileUrl")
            or item.get("file_url")
            or item.get("url")
            or item.get("source_url")
            or item.get("open_url")
            or item.get("preview_url")
        )
        if asset_id:
            row = db.query(Asset).filter(Asset.asset_id == asset_id, Asset.user_id == int(current_user.id)).first()
            if not row:
                raise HTTPException(status_code=404, detail=f"???????{index + 1}")
            row_url = _clean_text(getattr(row, "source_url", None))
            if _is_reusable_shanjian_media_url(row_url):
                logger.info("[shanjian-dh] reuse template material user_id=%s asset_id=%s type=%s url=%s", getattr(current_user, "id", ""), asset_id, kind, _url_hint(row_url))
                materials.append({"type": kind, "fileUrl": row_url})
                continue
            file_url = row_url or file_url
        if not file_url:
            continue
        if _is_reusable_shanjian_media_url(file_url):
            logger.info("[shanjian-dh] reuse template material url user_id=%s type=%s url=%s", getattr(current_user, "id", ""), kind, _url_hint(file_url))
            materials.append({"type": kind, "fileUrl": file_url})
            continue
        uploaded_url, _ = await _persist_media_for_shanjian(
            media_url=file_url,
            db=db,
            current_user=current_user,
            title=title,
            media_type=kind,
            label=f"????{index + 1}",
        )
        materials.append({"type": kind, "fileUrl": uploaded_url})
    prepared["materials"] = materials
    return prepared


def _build_realman_clip_payload(*, title: str, template_meta: Dict[str, Any], video_url: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "styleId": _clean_text(template_meta.get("style_id")),
        "title": _clean_text(title)[:80] or "数字人口播",
        "videoUrl": _clean_text(video_url),
        "materialSoundSwitch": bool(template_meta.get("material_sound_switch")),
        "packRules": {
            "headerSwitch": bool(template_meta.get("header_switch", True)),
            "materialSwitch": bool(template_meta.get("material_switch", True)),
            "subtitleSwitch": bool(template_meta.get("subtitle_switch", True)),
            "keywordSwitch": bool(template_meta.get("keyword_switch", True)),
        },
        "processRules": {
            "watermarkShow": bool(template_meta.get("watermark_show", False)),
            "materialMatchWay": template_meta.get("material_match_way") if template_meta.get("material_match_way") in {"fuzzyMatch", "preciseMatch"} else "fuzzyMatch",
            "resourcePreprocessMethod": template_meta.get("resource_preprocess_method") if template_meta.get("resource_preprocess_method") in {"roughCut", "sliceMerge"} else "roughCut",
        },
    }
    material_composition = _clean_text(template_meta.get("material_composition"))
    if material_composition in {"random", "sequential"}:
        payload["processRules"]["materialComposition"] = material_composition
    try:
        payload["processRules"]["videoDuration"] = max(5, min(int(template_meta.get("video_duration") or 30), 300))
    except Exception:
        payload["processRules"]["videoDuration"] = 30
    if template_meta.get("materials"):
        payload["materials"] = [
            {"type": str(item.get("type") or "").strip(), "fileUrl": _clean_text(item.get("fileUrl") or item.get("file_url"))}
            for item in template_meta.get("materials")[:20]
            if isinstance(item, dict)
            and _clean_text(item.get("fileUrl") or item.get("file_url"))
            and str(item.get("type") or "").strip() in {"image", "video"}
        ]
    intro_name = _clean_text(template_meta.get("introduce_name"))
    intro_desc = _clean_text(template_meta.get("introduce_description"))
    if intro_name or intro_desc:
        payload["introduceCard"] = {"name": intro_name, "description": intro_desc}
    return payload


async def _submit_realman_clip_task(
    *,
    body: VideoTaskBody,
    db: Session,
    current_user: User,
    row: ShanjianDigitalHumanVideoTask,
    template_meta: Dict[str, Any],
    base_result_payload: Dict[str, Any],
) -> Dict[str, Any]:
    result = base_result_payload.get("result") if isinstance(base_result_payload, dict) else {}
    base_video_url = _clean_text(_pick_result_value(result if isinstance(result, dict) else {}, "videoUrl")) or _clean_text(row.video_url)
    if not base_video_url:
        raise HTTPException(status_code=502, detail="基础视频未返回可用 videoUrl，无法继续模板剪辑")
    base_video_url, base_asset_id = await _persist_media_for_shanjian(
        media_url=base_video_url,
        db=db,
        current_user=current_user,
        title=row.title or "数字人口播",
        media_type="video",
        label="基础数字人视频",
    )
    prepared_template = await _prepare_template_media_urls(
        db=db,
        current_user=current_user,
        template_meta=template_meta,
        title=row.title or "数字人口播",
    )
    clip_payload = _build_realman_clip_payload(title=row.title or "数字人口播", template_meta=prepared_template, video_url=base_video_url)
    logger.info(
        "[shanjian-dh] submit realman clip user_id=%s base_task=%s style_id=%s materials=%s payload=%s",
        getattr(current_user, "id", ""),
        row.task_id,
        clip_payload.get("styleId"),
        len(clip_payload.get("materials") or []),
        str(clip_payload)[:2000],
    )
    clip_upstream = await _post("/v1/clip/video/realman_broadcast", body.token, clip_payload)
    clip_data = _data(clip_upstream)
    clip_task_id = _clean_text(clip_data.get("taskId"))
    if not clip_task_id:
        logger.warning("[shanjian-dh] realman clip missing taskId user_id=%s response=%s", getattr(current_user, "id", ""), str(clip_upstream)[:1000])
        raise HTTPException(status_code=502, detail="闪剪模板剪辑未返回 taskId")

    submit_payload = dict(row.submit_payload or {})
    template_state = dict(prepared_template or {})
    template_state["clip_task_id"] = clip_task_id
    template_state["clip_request_id"] = _clean_text(clip_upstream.get("requestId"))
    template_state["base_video_url"] = base_video_url
    template_state["base_asset_id"] = base_asset_id
    submit_payload["template"] = template_state
    submit_payload["stage"] = "clip"
    submit_payload["base_result"] = base_result_payload
    row.submit_payload = submit_payload
    row.status = "processing"
    row.result_payload = {"base": base_result_payload, "clip_submit": clip_upstream}
    row.error_message = None
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": True,
        "stage": "clip",
        "clip_task_id": clip_task_id,
        "request_id": _clean_text(clip_upstream.get("requestId")),
        "clip_payload": clip_payload,
        "raw": clip_upstream,
    }


def _is_reusable_shanjian_media_url(url: str) -> bool:
    raw = _clean_text(url)
    if not raw.startswith(("http://", "https://")):
        return False
    if "token=" in raw or "?token" in raw:
        return False
    try:
        parsed = urlparse(raw)
        hostname = (parsed.hostname or "").lower()
    except Exception:
        return False
    if not hostname or hostname in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return False
    if hostname.endswith(".local") or hostname.startswith("192.168.") or hostname.startswith("10."):
        return False
    try:
        cfg = _get_tos_config() or {}
        public_domain = str(cfg.get("public_domain", "") or "").strip().rstrip("/")
    except Exception:
        public_domain = ""
    if public_domain and raw.startswith(public_domain + "/"):
        return True
    return ("tos" in hostname and "volces.com" in hostname) or ".tos-" in hostname or hostname.startswith("lobster-online-assets")

def _clear_default_profiles(db: Session, user_id: int) -> None:
    db.query(ShanjianDigitalHumanProfile).filter(
        ShanjianDigitalHumanProfile.user_id == int(user_id),
        ShanjianDigitalHumanProfile.is_default.is_(True),
    ).update({"is_default": False}, synchronize_session=False)


def _profile_to_dict(row: ShanjianDigitalHumanProfile) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "title": row.title,
        "train_mode": row.train_mode,
        "status": row.status,
        "status_text": _task_status_text(row.status),
        "is_default": bool(row.is_default),
        "task_id": row.task_id or "",
        "request_id": row.request_id or "",
        "virtualman_id": row.virtualman_id or "",
        "source_asset_id": row.source_asset_id or "",
        "source_url": row.source_url or "",
        "auth_video_asset_id": row.auth_video_asset_id or "",
        "auth_video_url": row.auth_video_url or "",
        "auth_text": row.auth_text or "",
        "cover_url": row.cover_url or "",
        "error_message": row.error_message or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _video_task_to_dict(row: ShanjianDigitalHumanVideoTask) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "profile_id": row.profile_id,
        "title": row.title,
        "status": row.status,
        "status_text": _task_status_text(row.status),
        "task_id": row.task_id,
        "request_id": row.request_id or "",
        "virtualman_id": row.virtualman_id or "",
        "audio_asset_id": row.audio_asset_id or "",
        "audio_url": row.audio_url or "",
        "speaker_id": row.speaker_id or "",
        "text": row.text or "",
        "video_url": row.video_url or "",
        "cover_url": row.cover_url or "",
        "duration": row.duration,
        "error_message": row.error_message or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _resolve_profile_for_video(
    db: Session,
    current_user: User,
    profile_id: Optional[int],
    virtualman_id: Optional[str],
) -> tuple[Optional[ShanjianDigitalHumanProfile], str]:
    vmid = _clean_text(virtualman_id)
    if profile_id:
        row = db.query(ShanjianDigitalHumanProfile).filter(
            ShanjianDigitalHumanProfile.id == int(profile_id),
            ShanjianDigitalHumanProfile.user_id == int(current_user.id),
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="未找到对应的闪剪数字人档案")
        if row.status != "succeed" or not _clean_text(row.virtualman_id):
            raise HTTPException(status_code=400, detail="该闪剪数字人还未训练完成，暂时不能用于出片")
        return row, _clean_text(row.virtualman_id)
    if vmid:
        row = db.query(ShanjianDigitalHumanProfile).filter(
            ShanjianDigitalHumanProfile.user_id == int(current_user.id),
            ShanjianDigitalHumanProfile.virtualman_id == vmid,
        ).first()
        return row, vmid
    row = db.query(ShanjianDigitalHumanProfile).filter(
        ShanjianDigitalHumanProfile.user_id == int(current_user.id),
        ShanjianDigitalHumanProfile.is_default.is_(True),
        ShanjianDigitalHumanProfile.status == "succeed",
    ).order_by(ShanjianDigitalHumanProfile.updated_at.desc()).first()
    if row and _clean_text(row.virtualman_id):
        return row, _clean_text(row.virtualman_id)
    raise HTTPException(status_code=400, detail="请先创建并训练一个自己的闪剪数字人，或显式传入 virtualman_id")


def _profile_endpoint_and_payload(
    body: ProfileTrainBody,
    *,
    request: Request,
    db: Session,
    current_user: User,
) -> tuple[str, Dict[str, Any], str, Optional[str], Optional[str]]:
    mode = _normalize_mode(body.mode)
    source_asset_id = _clean_text(body.image_asset_id if mode == "image" else body.video_asset_id) or None
    auth_asset_id = _clean_text(body.auth_video_asset_id) or None
    auth_video_url = _resolve_asset_or_url(
        request=request,
        db=db,
        current_user=current_user,
        url=body.auth_video_url,
        asset_id=body.auth_video_asset_id,
        label="授权视频",
    )
    payload: Dict[str, Any] = {
        "title": _clean_text(body.title)[:80] or "未命名数字人",
        "authVideoUrl": auth_video_url,
        "authText": _clean_text(body.auth_text),
    }
    if _clean_text(body.callback_url):
        payload["callbackUrl"] = _clean_text(body.callback_url)
    if mode == "image":
        source_url = _resolve_asset_or_url(
            request=request,
            db=db,
            current_user=current_user,
            url=body.image_url,
            asset_id=body.image_asset_id,
            label="训练图片",
        )
        payload["imageUrl"] = source_url
        return "/v1/virtualman/image/train", payload, source_url, source_asset_id, auth_asset_id
    source_url = _resolve_asset_or_url(
        request=request,
        db=db,
        current_user=current_user,
        url=body.video_url,
        asset_id=body.video_asset_id,
        label="训练视频",
    )
    payload["videoUrl"] = source_url
    endpoint = "/v1/virtualman/fast/train" if mode == "fast_video" else "/v1/virtualman/train"
    return endpoint, payload, source_url, source_asset_id, auth_asset_id


@router.get("/api/shanjian-digital-human/profiles")
async def list_profiles(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(ShanjianDigitalHumanProfile).filter(
        ShanjianDigitalHumanProfile.user_id == int(current_user.id)
    ).order_by(
        ShanjianDigitalHumanProfile.is_default.desc(),
        ShanjianDigitalHumanProfile.updated_at.desc(),
        ShanjianDigitalHumanProfile.id.desc(),
    ).all()
    return {"ok": True, "items": [_profile_to_dict(row) for row in rows]}


@router.get("/api/shanjian-digital-human/videos")
async def list_video_tasks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(ShanjianDigitalHumanVideoTask).filter(
        ShanjianDigitalHumanVideoTask.user_id == int(current_user.id)
    ).order_by(
        ShanjianDigitalHumanVideoTask.updated_at.desc(),
        ShanjianDigitalHumanVideoTask.id.desc(),
    ).limit(100).all()
    return {"ok": True, "items": [_video_task_to_dict(row) for row in rows]}


@router.post("/api/shanjian-digital-human/profile/train")
async def create_profile(
    body: ProfileTrainBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    endpoint, payload, source_url, source_asset_id, auth_asset_id = _profile_endpoint_and_payload(
        body,
        request=request,
        db=db,
        current_user=current_user,
    )
    upstream = await _post(endpoint, body.token, payload)
    data = _data(upstream)
    task_id = _clean_text(data.get("taskId"))
    if not task_id:
        raise HTTPException(status_code=502, detail="闪剪未返回 taskId")
    if body.make_default:
        _clear_default_profiles(db, int(current_user.id))
    row = ShanjianDigitalHumanProfile(
        user_id=int(current_user.id),
        title=_clean_text(body.title)[:80] or "未命名数字人",
        train_mode=_normalize_mode(body.mode),
        status="processing",
        is_default=bool(body.make_default),
        task_id=task_id,
        request_id=_clean_text(upstream.get("requestId")),
        source_asset_id=source_asset_id,
        source_url=source_url,
        auth_video_asset_id=auth_asset_id,
        auth_video_url=_clean_text(payload.get("authVideoUrl")),
        auth_text=_clean_text(body.auth_text),
        train_payload=payload,
        train_result=upstream,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": True,
        "profile": _profile_to_dict(row),
        "task_id": task_id,
        "request_id": row.request_id or "",
        "raw": upstream,
    }


@router.post("/api/shanjian-digital-human/profile/task")
async def query_profile_task(
    body: ProfileTaskBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = None
    if body.profile_id:
        row = db.query(ShanjianDigitalHumanProfile).filter(
            ShanjianDigitalHumanProfile.id == int(body.profile_id),
            ShanjianDigitalHumanProfile.user_id == int(current_user.id),
        ).first()
    elif _clean_text(body.task_id):
        row = db.query(ShanjianDigitalHumanProfile).filter(
            ShanjianDigitalHumanProfile.task_id == _clean_text(body.task_id),
            ShanjianDigitalHumanProfile.user_id == int(current_user.id),
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到对应的闪剪数字人任务")

    payload = await _get("/v1/task/info", body.token, {"taskId": row.task_id})
    data = _data(payload)
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    status = _clean_text(data.get("status")) or "processing"
    virtualman_id = _clean_text(_pick_result_value(result, "virtualmanId", "virtualManId", "id"))
    cover_url = _clean_text(_pick_result_value(result, "coverUrl", "imageUrl", "posterUrl"))
    error_message = _clean_text(data.get("errorMessage") or payload.get("message"))

    row.status = status
    row.request_id = _clean_text(payload.get("requestId")) or row.request_id
    row.virtualman_id = virtualman_id or row.virtualman_id
    row.cover_url = cover_url or row.cover_url
    row.train_result = payload
    row.error_message = error_message or None
    row.updated_at = datetime.utcnow()

    if status == "succeed" and row.is_default:
        _clear_default_profiles(db, int(current_user.id))
        row.is_default = True

    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": status != "failed",
        "status": status,
        "status_text": _task_status_text(status),
        "virtualman_id": row.virtualman_id or "",
        "profile": _profile_to_dict(row),
        "message": error_message,
        "raw": payload,
    }


@router.post("/api/shanjian-digital-human/profile/default")
async def set_default_profile(
    body: SetDefaultBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(ShanjianDigitalHumanProfile).filter(
        ShanjianDigitalHumanProfile.id == int(body.profile_id),
        ShanjianDigitalHumanProfile.user_id == int(current_user.id),
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到对应的闪剪数字人档案")
    _clear_default_profiles(db, int(current_user.id))
    row.is_default = True
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "profile": _profile_to_dict(row)}


@router.post("/api/shanjian-digital-human/video/create")
async def create_video(
    body: CreateVideoBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile, virtualman_id = _resolve_profile_for_video(db, current_user, body.profile_id, body.virtualman_id)
    text = _clean_text(body.text)
    speaker_id = _clean_text(body.speaker_id)
    audio_url = _clean_text(body.audio_url)
    audio_asset_id = _clean_text(body.audio_asset_id) or None
    title = _clean_text(body.title)[:80] or "Digital human video"

    if not audio_url and audio_asset_id:
        audio_url = _resolve_asset_or_url(
            request=request,
            db=db,
            current_user=current_user,
            url=None,
            asset_id=audio_asset_id,
            label="audio",
        )
    if not audio_url and (not text or not speaker_id):
        raise HTTPException(status_code=400, detail="Please provide audio_url / audio_asset_id, or text + speaker_id")

    if audio_url:
        audio_url, persisted_audio_asset_id = await _persist_audio_for_shanjian(
            audio_url=audio_url,
            db=db,
            current_user=current_user,
            title=title,
        )
        audio_asset_id = audio_asset_id or persisted_audio_asset_id

    payload: Dict[str, Any] = {"title": title, "virtualmanId": virtualman_id}
    if _clean_text(body.callback_url):
        payload["callbackUrl"] = _clean_text(body.callback_url)
    if audio_url:
        payload["audioUrl"] = audio_url
    else:
        payload["text"] = text
        payload["speakerId"] = speaker_id
        payload["speakerExtra"] = {
            "speedRatio": max(0.5, min(float(body.speed_ratio or 1.0), 2.0)),
            "language": _clean_text(body.language) or "zh-CN",
        }

    template_meta = _template_meta_from_body(body)
    submit_payload: Dict[str, Any] = {"base": payload, "stage": "base"}
    if template_meta:
        submit_payload["template"] = template_meta
    billing = await _shanjian_pre_deduct(
        request=request,
        template_meta=template_meta,
        estimated_seconds=_estimate_billing_seconds(body, template_meta, text),
        title=title,
    )
    submit_payload["billing"] = billing

    logger.info(
        "[shanjian-dh] submit base video user_id=%s profile_id=%s virtualman=%s audio=%s text_len=%s template=%s style_id=%s materials=%s",
        getattr(current_user, "id", ""),
        getattr(profile, "id", None),
        virtualman_id,
        _url_hint(payload.get("audioUrl", "")) if payload.get("audioUrl") else "",
        len(text or ""),
        bool(template_meta),
        (template_meta or {}).get("style_id"),
        len((template_meta or {}).get("materials") or []),
    )
    try:
        upstream = await _post("/v1/virtualman/video", body.token, payload)
    except HTTPException as exc:
        await _shanjian_refund_billing(request, billing, reason="base_submit_failed")
        logger.warning(
            "[shanjian-dh] submit base video failed user_id=%s virtualman=%s audio=%s detail=%s",
            getattr(current_user, "id", ""),
            virtualman_id,
            _url_hint(payload.get("audioUrl", "")) if payload.get("audioUrl") else "",
            getattr(exc, "detail", exc),
        )
        raise
    data = _data(upstream)
    task_id = _clean_text(data.get("taskId"))
    if not task_id:
        await _shanjian_refund_billing(request, billing, reason="base_missing_task_id")
        logger.warning("[shanjian-dh] submit base video missing taskId user_id=%s response=%s", getattr(current_user, "id", ""), str(upstream)[:1000])
        raise HTTPException(status_code=502, detail="Shanjian did not return taskId")

    row = ShanjianDigitalHumanVideoTask(
        user_id=int(current_user.id),
        profile_id=getattr(profile, "id", None),
        title=title,
        status="processing",
        task_id=task_id,
        request_id=_clean_text(upstream.get("requestId")),
        virtualman_id=virtualman_id,
        audio_asset_id=audio_asset_id,
        audio_url=audio_url or None,
        speaker_id=speaker_id or None,
        text=text or None,
        submit_payload=submit_payload,
        result_payload={"base_submit": upstream},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "task_id": task_id, "record": _video_task_to_dict(row), "raw": upstream}


@router.post("/api/shanjian-digital-human/video/task")
async def query_video_task(
    body: VideoTaskBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = None
    if body.record_id:
        row = db.query(ShanjianDigitalHumanVideoTask).filter(
            ShanjianDigitalHumanVideoTask.id == int(body.record_id),
            ShanjianDigitalHumanVideoTask.user_id == int(current_user.id),
        ).first()
    elif _clean_text(body.task_id):
        row = db.query(ShanjianDigitalHumanVideoTask).filter(
            ShanjianDigitalHumanVideoTask.task_id == _clean_text(body.task_id),
            ShanjianDigitalHumanVideoTask.user_id == int(current_user.id),
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到对应的闪剪视频任务")

    submit_payload = row.submit_payload if isinstance(row.submit_payload, dict) else {}
    template_meta = _template_meta_from_submit_payload(submit_payload)
    clip_task_id = _clean_text((template_meta or {}).get("clip_task_id"))

    if clip_task_id:
        payload = await _get("/v1/task/info", body.token, {"taskId": clip_task_id})
        data = _data(payload)
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        status = _clean_text(data.get("status")) or "processing"
        error_message = _clean_text(data.get("errorMessage") or payload.get("message"))

        row.status = status
        row.request_id = _clean_text(payload.get("requestId")) or row.request_id
        row.video_url = _clean_text(_pick_result_value(result, "videoUrl")) or row.video_url
        row.cover_url = _clean_text(_pick_result_value(result, "coverUrl")) or row.cover_url
        duration_value = _pick_result_value(result, "duration")
        try:
            row.duration = int(duration_value) if duration_value not in (None, "") else row.duration
        except Exception:
            pass
        row.result_payload = {"base": submit_payload.get("base_result"), "clip": payload}
        row.error_message = error_message or None
        row.updated_at = datetime.utcnow()
        await _finalize_row_billing(
            request=request,
            row=row,
            status=status,
            duration_value=duration_value,
            video_url=row.video_url or "",
            stage="clip",
            error_message=error_message,
        )

        db.add(row)
        db.commit()
        db.refresh(row)
        return {
            "ok": status != "failed",
            "status": status,
            "status_text": _task_status_text(status),
            "task_id": row.task_id,
            "clip_task_id": clip_task_id,
            "video_url": row.video_url or "",
            "cover_url": row.cover_url or "",
            "duration": row.duration,
            "record": _video_task_to_dict(row),
            "message": error_message,
            "raw": payload,
        }

    payload = await _get("/v1/task/info", body.token, {"taskId": row.task_id})
    data = _data(payload)
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    status = _clean_text(data.get("status")) or "processing"
    error_message = _clean_text(data.get("errorMessage") or payload.get("message"))

    row.status = status
    row.request_id = _clean_text(payload.get("requestId")) or row.request_id
    row.video_url = _clean_text(_pick_result_value(result, "videoUrl")) or row.video_url
    row.cover_url = _clean_text(_pick_result_value(result, "coverUrl")) or row.cover_url
    duration_value = _pick_result_value(result, "duration")
    try:
        row.duration = int(duration_value) if duration_value not in (None, "") else row.duration
    except Exception:
        pass

    if status == "succeed" and template_meta and _clean_text(template_meta.get("style_id")):
        try:
            clip_submit = await _submit_realman_clip_task(
                body=body,
                db=db,
                current_user=current_user,
                row=row,
                template_meta=template_meta,
                base_result_payload=payload,
            )
        except HTTPException as exc:
            row.status = "failed"
            row.result_payload = {"base": payload, "clip_error": getattr(exc, "detail", str(exc))}
            row.error_message = f"模板剪辑提交失败：{getattr(exc, 'detail', str(exc))}"
            row.updated_at = datetime.utcnow()
            await _finalize_row_billing(
                request=request,
                row=row,
                status="failed",
                duration_value=row.duration,
                video_url=row.video_url or "",
                stage="clip_submit",
                error_message=row.error_message or "",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            logger.warning(
                "[shanjian-dh] realman clip submit failed user_id=%s base_task=%s detail=%s",
                getattr(current_user, "id", ""),
                row.task_id,
                getattr(exc, "detail", exc),
            )
            return {
                "ok": False,
                "status": "failed",
                "status_text": _task_status_text("failed"),
                "task_id": row.task_id,
                "video_url": row.video_url or "",
                "cover_url": row.cover_url or "",
                "duration": row.duration,
                "record": _video_task_to_dict(row),
                "message": row.error_message,
                "raw": row.result_payload,
            }
        return {
            "ok": True,
            "status": "processing",
            "status_text": "处理中",
            "task_id": row.task_id,
            "clip_task_id": clip_submit.get("clip_task_id"),
            "video_url": row.video_url or "",
            "cover_url": row.cover_url or "",
            "duration": row.duration,
            "record": _video_task_to_dict(row),
            "message": "基础视频已完成，模板剪辑任务已提交。",
            "raw": {"base": payload, "clip_submit": clip_submit},
        }

    row.result_payload = payload
    row.error_message = error_message or None
    row.updated_at = datetime.utcnow()
    await _finalize_row_billing(
        request=request,
        row=row,
        status=status,
        duration_value=duration_value,
        video_url=row.video_url or "",
        stage="base",
        error_message=error_message,
    )

    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": status != "failed",
        "status": status,
        "status_text": _task_status_text(status),
        "task_id": row.task_id,
        "video_url": row.video_url or "",
        "cover_url": row.cover_url or "",
        "duration": row.duration,
        "record": _video_task_to_dict(row),
        "message": error_message,
        "raw": payload,
    }
