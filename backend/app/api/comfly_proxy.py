"""Comfly 透明 Proxy：让用户客户端 (lobster_online) 内的爆款TVC pipeline 走云端 Comfly Token + 龙虾积分计费。

为什么需要：
- 爆款TVC pipeline (skills/comfly_veo3_daihuo_video) 内部会调 Comfly 的 4 个端点：
  POST /v1/chat/completions       (分镜规划，按 token usage 计费)
  POST /v1/images/generations     (分镜图，按 per_call 计费)
  POST /v2/videos/generations     (Veo 视频提交，按 per_call 计费)
  GET  /v2/videos/generations/{id}(Veo 任务轮询，不计费)
- 之前每个用户必须自己在「技能商店」配 Comfly API Key，按 Comfly 账户余额扣费。
- 现在改成统一走云端 server token (env: COMFLY_API_KEY[_<GROUP>])，按 comfly_pricing.json 扣龙虾积分。

设计：
- 透明转发：proxy 不重新组装 body，直接把客户端构造好的 body POST 给 Comfly，只替换 Authorization。
- 计费：① 调用前按估算预扣 → ② 调 Comfly → ③ chat 按 usage 结算差额；image/video 按 per_call 实扣（估算==实际）；失败全额退款。
- 鉴权：用户 JWT。
- token_group：按 model 在 comfly_pricing.json 配置的 token_group 选用对应 env 的 Key。
"""
from __future__ import annotations

import asyncio
import ast
import copy
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import sys
import tempfile
import time
import uuid
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import SessionLocal, get_db
from ..models import Asset, User
from ..services.credit_ledger import append_credit_ledger
from ..services.brand_context import explicit_request_brand_mark
from ..services.credits_amount import quantize_credits, credits_json_float, user_balance_decimal
from ..services.model_usage_monitor import log_model_usage_event
from ..services.runtime_cache import cache_delete, cache_get, cache_set, cache_set_if_absent
from ..services.user_feature_flags import OPENAI_OFFICIAL_IMAGE_CHANNEL_FEATURE_ID, user_has_feature
from ..services.workload_guard import WorkloadQueueFull, background_heavy_slot, spawn_tracked_task
from .assets import _run_asset_upload_io, _save_bytes_or_tos
from .auth import ALGORITHM, get_current_user, validate_token_brand
from .mobile_identity import online_user_for_mobile_user

# 让本模块能 import mcp/ 下的 comfly_upstream
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mcp.comfly_upstream import (  # noqa: E402
    estimate_comfly_credits,
    get_comfly_config,
    lookup_comfly_model,
)
from mcp.sutui_tokens import next_sutui_server_token_with_pool  # noqa: E402

logger = logging.getLogger(__name__)
router = APIRouter()

_PROXY_AUDIT_LOGGER = logging.getLogger("comfly_proxy_audit")

# Comfly 上游超时（与 pipeline 默认 poll 间隔对齐，video submit 通常很快返回 task_id）
_TIMEOUT_CHAT = 120.0
_TIMEOUT_IMAGE = 300.0
try:
    _TIMEOUT_OPENMIND_IMAGE_READ = max(
        30.0,
        min(180.0, float(os.environ.get("OPENMIND_IMAGE_TIMEOUT_SECONDS") or "120")),
    )
except (TypeError, ValueError):
    _TIMEOUT_OPENMIND_IMAGE_READ = 120.0
_TIMEOUT_FILE_UPLOAD = 120.0
_TIMEOUT_VIDEO_SUBMIT = 60.0
_TIMEOUT_OPENMIND_VIDEO_SUBMIT = 60.0
_TIMEOUT_XAI_VIDEO_SUBMIT = 60.0
_TIMEOUT_VIDEO_POLL = 30.0
_MAX_PROXY_VIDEO_TASK_TRACK = 5000
_MAX_GROK_REFERENCE_BYTES = 30 * 1024 * 1024
_MAX_PROXY_FILE_BYTES = 512 * 1024 * 1024
_MAX_IMAGE_EDIT_TOTAL_BYTES = 120 * 1024 * 1024
_MAX_CHAT_IMAGE_PREPARE_BYTES = 20 * 1024 * 1024
_MAX_GENERATED_IMAGE_PERSIST_BYTES = 40 * 1024 * 1024
_IMAGE_PROXY_JOB_TTL_SECONDS = 24 * 60 * 60
_proxy_video_task_meta: "OrderedDict[str, Tuple[str, str]]" = OrderedDict()
_openmind_tos_url_cache: "OrderedDict[str, str]" = OrderedDict()
_MAX_OPENMIND_TOS_URL_CACHE = 1000
_MAX_OPENMIND_VIDEO_BYTES = 512 * 1024 * 1024
_MAX_VIDEO_IMAGE_RETRY_CONTEXTS = 5000
_VIDEO_IMAGE_RETRY_TTL_SECONDS = 2 * 60 * 60
_video_image_retry_contexts: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_video_image_retry_roots: "OrderedDict[str, str]" = OrderedDict()
_video_image_retry_lock = asyncio.Lock()


def _seekable_upload_size(file_obj) -> int:
    file_obj.seek(0, os.SEEK_END)
    size = int(file_obj.tell())
    file_obj.seek(0)
    return size


def _video_image_retry_context_key(root_task_id: str) -> str:
    return f"comfly:video-image-retry:context:{root_task_id}"


def _video_image_retry_root_key(task_id: str) -> str:
    return f"comfly:video-image-retry:root:{task_id}"


def _video_image_retry_claim_key(root_task_id: str) -> str:
    return f"comfly:video-image-retry:claim:{root_task_id}"


def _store_video_image_retry_context(root_task_id: str, context: Dict[str, Any]) -> None:
    _video_image_retry_contexts[root_task_id] = context
    _video_image_retry_contexts.move_to_end(root_task_id)
    cache_set(
        _video_image_retry_context_key(root_task_id),
        json.dumps(context, ensure_ascii=False, default=str),
        _VIDEO_IMAGE_RETRY_TTL_SECONDS,
    )


def _store_video_image_retry_root(task_id: str, root_task_id: str) -> None:
    _video_image_retry_roots[task_id] = root_task_id
    _video_image_retry_roots.move_to_end(task_id)
    cache_set(
        _video_image_retry_root_key(task_id),
        root_task_id,
        _VIDEO_IMAGE_RETRY_TTL_SECONDS,
    )


def _load_video_image_retry_context(root_task_id: str) -> Optional[Dict[str, Any]]:
    cached = cache_get(_video_image_retry_context_key(root_task_id))
    if cached:
        try:
            parsed = json.loads(cached)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            _video_image_retry_contexts[root_task_id] = parsed
            _video_image_retry_contexts.move_to_end(root_task_id)
            return parsed
    return _video_image_retry_contexts.get(root_task_id)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_int(name: str, default: int, *, min_value: int = 1, max_value: int = 5) -> int:
    try:
        value = int(str(os.environ.get(name) or "").strip() or default)
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def _remember_video_image_retry_context(
    task_id: str,
    *,
    provider: str,
    body: Dict[str, Any],
    model: str,
    request_user_id: int,
) -> None:
    root_task_id = str(task_id or "").strip()
    if not root_task_id:
        return
    context = {
        "provider": str(provider or "").strip().lower(),
        "body": dict(body or {}),
        "model": str(model or "").strip(),
        "request_user_id": int(request_user_id),
        "active_task_id": root_task_id,
        "resubmit_count": 0,
        "resubmit_state": "ready",
    }
    _store_video_image_retry_context(root_task_id, context)
    _store_video_image_retry_root(root_task_id, root_task_id)
    while len(_video_image_retry_contexts) > _MAX_VIDEO_IMAGE_RETRY_CONTEXTS:
        expired_root, _expired = _video_image_retry_contexts.popitem(last=False)
        stale_task_ids = [
            tracked_task_id
            for tracked_task_id, tracked_root in _video_image_retry_roots.items()
            if tracked_root == expired_root
        ]
        for stale_task_id in stale_task_ids:
            _video_image_retry_roots.pop(stale_task_id, None)
    while len(_video_image_retry_roots) > _MAX_VIDEO_IMAGE_RETRY_CONTEXTS * 2:
        _video_image_retry_roots.popitem(last=False)


def _video_image_retry_poll_target(
    task_id: str,
    *,
    provider: str,
    request_user_id: int,
) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    requested_task_id = str(task_id or "").strip()
    root_task_id = (
        cache_get(_video_image_retry_root_key(requested_task_id))
        or _video_image_retry_roots.get(requested_task_id)
        or requested_task_id
    )
    context = _load_video_image_retry_context(root_task_id)
    if not context:
        return requested_task_id, requested_task_id, None
    if context.get("provider") != str(provider or "").strip().lower():
        return requested_task_id, requested_task_id, None
    if int(context.get("request_user_id") or 0) != int(request_user_id):
        return requested_task_id, requested_task_id, None
    _video_image_retry_contexts.move_to_end(root_task_id)
    active_task_id = str(context.get("active_task_id") or root_task_id).strip()
    return root_task_id, active_task_id, context


def _is_image_download_interrupted_payload(payload: Any) -> bool:
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str).lower()
    except Exception:
        text = str(payload or "").lower()
    return (
        "image_download_interrupted" in text
        or "timed out while downloading image" in text
        or "timed out while downloading the image" in text
        or "while downloading image from ip:port" in text
    ) or (
        "failed to download the provided image" in text
        and "connection dropped while downloading the image" in text
    )


def _image_generation_channel_cache_key(provider: str, model: str) -> str:
    normalized_provider = str(provider or "comfly").strip().lower()
    if normalized_provider == "openmindapi":
        normalized_provider = "openmind"
    normalized_model = str(model or "unknown").strip().lower()
    if normalized_model == "gpt-image-2-openmindapi":
        normalized_model = "gpt-image-2"
    safe_provider = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in normalized_provider)[:64]
    safe_model = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in normalized_model)[:96]
    return f"comfly:image-provider-disabled:{safe_provider}:{safe_model}"


def _image_generation_provider_label(entry: Dict[str, Any], model: str) -> str:
    token_group = str((entry or {}).get("token_group") or "").strip().lower()
    if token_group:
        return token_group
    return "comfly"


def _image_generation_channel_available(provider: str, model: str) -> bool:
    return cache_get(_image_generation_channel_cache_key(provider, model)) is None


def _image_generation_channel_disable_ttl_seconds(provider: str, error: str) -> int:
    provider = str(provider or "").strip().lower()
    msg = str(error or "").lower()
    if any(token in msg for token in ("insufficient_quota", "no credits remaining", "quota exceeded", "余额不足", "欠费")):
        return _env_int("COMFLY_IMAGE_PROVIDER_QUOTA_DISABLE_SECONDS", 1800, min_value=60, max_value=7200)
    if "no available compatible accounts" in msg or "无可用账号" in msg or "no available account" in msg:
        return _env_int("COMFLY_IMAGE_PROVIDER_ACCOUNT_DISABLE_SECONDS", 600, min_value=60, max_value=3600)
    if "invalid api key" in msg or "invalid_api_key" in msg or "unauthorized" in msg or "forbidden" in msg:
        return _env_int("COMFLY_IMAGE_PROVIDER_AUTH_DISABLE_SECONDS", 1800, min_value=60, max_value=7200)
    if "http 429" in msg and provider in {"openai_official", "gaisc", "comfyui_official", "openmindapi", "openmind", "yunwu", "sutui"}:
        return _env_int("COMFLY_IMAGE_PROVIDER_RATE_LIMIT_DISABLE_SECONDS", 120, min_value=30, max_value=900)
    return 0


def _mark_image_generation_channel_failure(provider: str, model: str, error: str) -> bool:
    ttl = _image_generation_channel_disable_ttl_seconds(provider, error)
    if ttl <= 0:
        return False
    cache_set(_image_generation_channel_cache_key(provider, model), str(error or "")[:500], ttl_seconds=ttl)
    logger.warning(
        "[image_generate] provider temporarily disabled provider=%s model=%s ttl=%ss error=%s",
        provider,
        model,
        ttl,
        str(error or "")[:300],
    )
    return True


def _image_proxy_job_key(job_id: str) -> str:
    safe = "".join(ch for ch in str(job_id or "") if ch.isalnum() or ch in {"-", "_"})[:96]
    return f"comfly:image-proxy-job:{safe}" if safe else ""


def _new_image_proxy_job_id() -> str:
    return f"img_{uuid.uuid4().hex}"


def _store_image_proxy_job(job_id: str, payload: Dict[str, Any]) -> None:
    key = _image_proxy_job_key(job_id)
    if not key:
        return
    compact = dict(payload or {})
    compact.setdefault("job_id", job_id)
    compact["updated_at_ts"] = int(time.time())
    cache_set(key, json.dumps(compact, ensure_ascii=False, default=str), ttl_seconds=_IMAGE_PROXY_JOB_TTL_SECONDS)


def _load_image_proxy_job(job_id: str) -> Optional[Dict[str, Any]]:
    raw = cache_get(_image_proxy_job_key(job_id))
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _image_proxy_job_public_payload(job: Dict[str, Any]) -> Dict[str, Any]:
    status = str(job.get("status") or "").strip() or "unknown"
    out: Dict[str, Any] = {
        "ok": status != "failed",
        "async": True,
        "job_id": job.get("job_id"),
        "status": status,
        "stage": job.get("stage") or status,
        "created_at_ts": job.get("created_at_ts"),
        "updated_at_ts": job.get("updated_at_ts"),
    }
    if status == "completed":
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        out["result"] = result
        if isinstance(result, dict):
            out.update(result)
    elif status == "failed":
        out["error"] = str(job.get("error") or _public_image_failure_detail())
        if job.get("errors"):
            out["errors"] = job.get("errors")
    return out


def _compact_image_proxy_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    urls = [url for url in _extract_image_result_urls(result) if str(url).startswith(("http://", "https://"))]
    compact: Dict[str, Any] = {"data": [{"url": url} for url in urls]}
    for key in ("_lobster_fallback", "fallback_used", "fallback_provider", "_provider", "_requested_model"):
        value = result.get(key)
        if value not in (None, "", [], {}):
            compact[key] = value
    return compact


def _start_image_proxy_job(
    *,
    kind: str,
    request_user_id: int,
    billing_user_id: int,
    requested_model: str,
) -> str:
    job_id = _new_image_proxy_job_id()
    now = int(time.time())
    _store_image_proxy_job(
        job_id,
        {
            "job_id": job_id,
            "kind": kind,
            "status": "queued",
            "stage": "queued",
            "request_user_id": int(request_user_id),
            "billing_user_id": int(billing_user_id),
            "requested_model": requested_model,
            "created_at_ts": now,
            "updated_at_ts": now,
        },
    )
    return job_id


def _update_image_proxy_job(job_id: str, **updates: Any) -> None:
    job = _load_image_proxy_job(job_id) or {"job_id": job_id, "created_at_ts": int(time.time())}
    job.update(updates)
    _store_image_proxy_job(job_id, job)


def _job_error_detail(exc: BaseException) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail or "")
    return str(exc or "")


def _should_deduct_credits() -> bool:
    """与 capabilities.py / sutui_chat_proxy.py 一致：在线版独立认证才扣积分。"""
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    return edition == "online" and getattr(settings, "lobster_independent_auth", True)


def _bearer_token_from_request(request: Request) -> str:
    auth_header = str(request.headers.get("Authorization") or "").strip()
    if not auth_header:
        raise HTTPException(
            status_code=401,
            detail="Authorization Bearer missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return parts[1].strip()


def _resolve_proxy_user_ids_from_request(
    request: Request,
    *,
    map_to_online_user: bool = False,
) -> Tuple[int, int]:
    token = _bearer_token_from_request(request)
    credentials_exception = HTTPException(
        status_code=401,
        detail="无法验证凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        request_user_id = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise credentials_exception

    db = SessionLocal()
    try:
        request_user = db.query(User).filter(User.id == request_user_id).first()
        if request_user is None:
            raise credentials_exception
        validate_token_brand(
            payload,
            user=request_user,
            explicit_brand=explicit_request_brand_mark(request),
        )
        billing_user = online_user_for_mobile_user(db, request_user) if map_to_online_user else request_user
        return int(request_user.id), int(billing_user.id)
    finally:
        db.close()


def _do_pre_deduct_by_user_id(
    user_id: int,
    credits: int,
    *,
    capability_id: str,
    model: str,
    endpoint: str,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> Decimal:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if user is None:
            raise HTTPException(status_code=401, detail="用户不存在")
        return _do_pre_deduct(
            db,
            user,
            credits,
            capability_id=capability_id,
            model=model,
            endpoint=endpoint,
            extra_meta=extra_meta,
        )
    finally:
        db.close()


def _do_full_refund_by_user_id(
    user_id: int,
    *,
    pre: Decimal,
    capability_id: str,
    model: str,
    endpoint: str,
    error: str = "",
) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if user is None:
            raise HTTPException(status_code=401, detail="用户不存在")
        _do_full_refund(
            db,
            user,
            pre=pre,
            capability_id=capability_id,
            model=model,
            endpoint=endpoint,
            error=error,
        )
    finally:
        db.close()


def _do_settle_by_user_id(
    user_id: int,
    *,
    pre: Decimal,
    actual: int,
    capability_id: str,
    model: str,
    endpoint: str,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if user is None:
            raise HTTPException(status_code=401, detail="用户不存在")
        _do_settle(
            db,
            user,
            pre=pre,
            actual=actual,
            capability_id=capability_id,
            model=model,
            endpoint=endpoint,
            extra_meta=extra_meta,
        )
    finally:
        db.close()


async def _save_generated_images_best_effort_by_user_id(
    user_id: int,
    *,
    response_payload: Dict[str, Any],
    prompt: str,
    model: str,
    limit: int,
    exclude_urls: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        return await _save_generated_images_best_effort(
            db,
            user_id=user_id,
            response_payload=response_payload,
            prompt=prompt,
            model=model,
            limit=limit,
            exclude_urls=exclude_urls,
        )
    finally:
        db.close()


async def _persist_generated_images_in_background(
    user_id: int,
    *,
    response_payload: Dict[str, Any],
    prompt: str,
    model: str,
    limit: int,
    exclude_urls: Optional[List[str]] = None,
) -> None:
    """Persist generated images after the upstream response has been returned."""
    try:
        async with background_heavy_slot("image_asset_persistence"):
            saved_assets = await _save_generated_images_best_effort_by_user_id(
                user_id,
                response_payload=response_payload,
                prompt=prompt,
                model=model,
                limit=limit,
                exclude_urls=exclude_urls,
            )
            logger.info(
                "[image_generate] background asset persistence finished user_id=%s model=%s saved_assets=%s",
                user_id,
                model,
                len(saved_assets),
            )
    except WorkloadQueueFull:
        logger.warning(
            "[image_generate] background asset persistence skipped because queue is full user_id=%s model=%s",
            user_id,
            model,
        )
    except Exception:
        logger.exception(
            "[image_generate] background asset persistence failed user_id=%s model=%s",
            user_id,
            model,
        )


def _queue_generated_image_asset_persistence(
    user_id: int,
    *,
    response_payload: Dict[str, Any],
    prompt: str,
    model: str,
    limit: int,
    exclude_urls: Optional[List[str]] = None,
) -> bool:
    """Queue asset persistence without making the image response wait for TOS."""
    result_urls = _extract_image_result_urls(response_payload)
    if not result_urls:
        return False
    # Keep only the result URLs so a large upstream response is not retained by a task.
    compact_payload = {"data": [{"url": url} for url in result_urls]}
    task_coro = _persist_generated_images_in_background(
        user_id,
        response_payload=compact_payload,
        prompt=prompt,
        model=model,
        limit=limit,
        exclude_urls=exclude_urls,
    )
    try:
        spawn_tracked_task(
            task_coro,
            name=f"image-asset-persist-{user_id}",
        )
    except Exception:
        task_coro.close()
        logger.exception(
            "[image_generate] failed to queue background asset persistence user_id=%s model=%s",
            user_id,
            model,
        )
        return False
    return True


def _model_token_group(model_id: str) -> str:
    entry = lookup_comfly_model(model_id) or {}
    return (entry.get("token_group") or "").strip()


def _normalized_model_id(model_id: str) -> str:
    return (model_id or "").strip().lower().replace("_", "-")


_GPT_IMAGE_2_REQUEST_ALIASES = {
    "gpt-image-2",
    "gpt-image2",
    "gpt-image",
    "openai/gpt-image-2",
    "openai/gpt-image2",
    "openai/gpt-image",
}


def _is_gpt_image_2_request_model(model_id: str) -> bool:
    return _normalized_model_id(model_id) in _GPT_IMAGE_2_REQUEST_ALIASES


def _collect_image_ref_values(value: Any, *, max_depth: int = 4) -> List[str]:
    refs: List[str] = []

    def add(item: Any) -> None:
        text = str(item or "").strip()
        if text and text not in refs:
            refs.append(text)

    def visit(item: Any, depth: int = 0) -> None:
        if item is None or depth > max_depth:
            return
        if isinstance(item, str):
            text = item.strip()
            if not text:
                return
            if text.startswith(("[", "{")):
                try:
                    visit(json.loads(text), depth + 1)
                    return
                except Exception:
                    try:
                        visit(ast.literal_eval(text), depth + 1)
                        return
                    except Exception:
                        pass
            add(text)
            return
        if isinstance(item, (list, tuple, set)):
            for sub in item:
                visit(sub, depth + 1)
            return
        if isinstance(item, dict):
            for key in ("url", "image_url", "image", "source_url", "public_url", "file_url"):
                if key in item:
                    visit(item.get(key), depth + 1)
            return
        add(item)

    visit(value)
    return refs


def _normalized_image_refs_from_payload(payload: Dict[str, Any]) -> Tuple[str, List[str]]:
    refs: List[str] = []
    for key in ("image", "image_url", "image_urls", "images"):
        for value in _collect_image_ref_values(payload.get(key)):
            if value not in refs:
                refs.append(value)
    primary = refs[0] if refs else ""
    return primary, refs


def _image_generation_model_attempts(model: str) -> List[str]:
    """Return billing model ids to try for one image generation request."""
    if _is_gpt_image_2_request_model(model):
        return [
            "gpt-image-2-gaisc",
            "gpt-image-2",
            "gpt-image-2-comfyui-official",
            "gpt-image-2-sutui",
            "gpt-image-2-openmindapi",
            "nano-banana-2",
        ]
    return [model]


def _image_generation_model_attempts_for_user(model: str, *, openai_official_first: bool) -> List[str]:
    if not _is_gpt_image_2_request_model(model):
        return [model]
    if openai_official_first:
        return [
            "gpt-image-2-openai-official",
            "gpt-image-2-gaisc",
            "gpt-image-2",
            "gpt-image-2-comfyui-official",
            "gpt-image-2-sutui",
            "gpt-image-2-openmindapi",
            "nano-banana-2",
        ]
    return _image_generation_model_attempts(model)


def _image_edit_model_attempts_for_user(model: str, *, openai_official_first: bool) -> List[str]:
    return _image_generation_model_attempts_for_user(model, openai_official_first=openai_official_first)


def _openai_official_image_first_for_user(user_id: int) -> bool:
    db_flags = None
    try:
        db_flags = SessionLocal()
        return bool(user_has_feature(db_flags, int(user_id), OPENAI_OFFICIAL_IMAGE_CHANNEL_FEATURE_ID))
    except Exception:
        logger.warning("[image_generate] failed to read openai official channel flag user_id=%s", user_id, exc_info=True)
        return False
    finally:
        if db_flags is not None:
            try:
                db_flags.close()
            except Exception:
                pass


def _audit(event: str, **kw: Any) -> None:
    """JSONL 审计日志（与 sutui_audit 同 logger 风格）。"""
    try:
        payload = {"event": event, **kw}
        _PROXY_AUDIT_LOGGER.info("[comfly_proxy_audit] %s", json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        pass


def _check_request_authorized_for_billing(request: Request) -> None:
    """与 /capabilities/pre-deduct 同口径：非本机回环且无 X-Lobster-Mcp-Billing 时拒绝，避免被外部直接打。

    爆款TVC proxy 是用户客户端发来的，只要带有效 JWT 即可，不强制 billing key（与 sutui_chat_proxy 一致）。
    本函数预留扩展点：如未来要求强制 billing key，把判断打开即可。
    """
    return None


def _is_trusted_internal_video_fallback(request: Request) -> bool:
    """Only the MCP may reuse an existing video reservation for fallback."""
    marker = (
        request.headers.get("X-Lobster-Video-Fallback")
        or request.headers.get("x-lobster-video-fallback")
        or ""
    ).strip().lower()
    if marker not in {"1", "true", "yes", "on"}:
        return False
    expected = (
        getattr(settings, "lobster_mcp_billing_internal_key", None)
        or os.environ.get("LOBSTER_MCP_BILLING_INTERNAL_KEY")
        or ""
    ).strip()
    provided = (
        request.headers.get("X-Lobster-Mcp-Billing")
        or request.headers.get("x-lobster-mcp-billing")
        or ""
    ).strip()
    return bool(expected and provided and hmac.compare_digest(expected, provided))


def _do_pre_deduct(
    db: Session, user: User, credits: int, *,
    capability_id: str, model: str, endpoint: str, extra_meta: Optional[Dict[str, Any]] = None,
) -> Decimal:
    """直接扣账（与 capabilities.py force_credits 路径一致）。返回实际扣的 Decimal。"""
    if not _should_deduct_credits() or credits <= 0:
        return Decimal("0")
    fc = quantize_credits(credits)
    db.refresh(user)
    if user_balance_decimal(user) < fc:
        raise HTTPException(
            status_code=402,
            detail=f"积分不足：本次预扣 {float(fc)}，当前余额 {float(user_balance_decimal(user))}。",
        )
    user.credits = user_balance_decimal(user) - fc
    bal = quantize_credits(user.credits)
    append_credit_ledger(
        db, user.id, -fc, "pre_deduct", bal,
        description=f"Comfly proxy 预扣 ({endpoint})",
        ref_type="comfly_proxy",
        meta={
            "capability_id": capability_id, "model": model, "endpoint": endpoint,
            "pre_estimated": credits_json_float(fc), "upstream": "comfly",
            **(extra_meta or {}),
        },
    )
    db.commit()
    return fc


def _do_settle(
    db: Session, user: User, *, pre: Decimal, actual: int,
    capability_id: str, model: str, endpoint: str, extra_meta: Optional[Dict[str, Any]] = None,
) -> None:
    """实际 vs 预扣的差额结算。actual<pre 退差额，actual>pre 再扣差额。"""
    if not _should_deduct_credits():
        return
    actual_dec = quantize_credits(max(0, int(actual)))
    delta = actual_dec - pre  # >0 需补扣，<0 需退款
    if delta == 0:
        return
    db.refresh(user)
    if delta > 0:
        # 补扣：余额不足时不阻断（已经走完上游），只能记账让管理员对账
        cur_bal = user_balance_decimal(user)
        deduct_now = min(cur_bal, delta) if cur_bal > 0 else Decimal("0")
        user.credits = cur_bal - deduct_now
        bal = quantize_credits(user.credits)
        append_credit_ledger(
            db, user.id, -deduct_now, "settle", bal,
            description=f"Comfly proxy 结算补扣 ({endpoint}) actual={actual} pre={float(pre)}",
            ref_type="comfly_proxy",
            meta={
                "capability_id": capability_id, "model": model, "endpoint": endpoint,
                "pre_estimated": credits_json_float(pre), "actual": credits_json_float(actual_dec),
                "delta": credits_json_float(delta), "upstream": "comfly",
                **(extra_meta or {}),
            },
        )
        if deduct_now < delta:
            logger.warning(
                "[comfly_proxy] 用户 %s 结算补扣不足额：需 %s，仅扣 %s（余额耗尽）",
                user.id, float(delta), float(deduct_now),
            )
    else:
        # 退款
        refund_amt = -delta
        user.credits = user_balance_decimal(user) + refund_amt
        bal = quantize_credits(user.credits)
        append_credit_ledger(
            db, user.id, refund_amt, "refund", bal,
            description=f"Comfly proxy 结算退款 ({endpoint}) actual={actual} pre={float(pre)}",
            ref_type="comfly_proxy",
            meta={
                "capability_id": capability_id, "model": model, "endpoint": endpoint,
                "pre_estimated": credits_json_float(pre), "actual": credits_json_float(actual_dec),
                "delta": credits_json_float(delta), "upstream": "comfly",
                **(extra_meta or {}),
            },
        )
    db.commit()


def _do_full_refund(
    db: Session, user: User, *, pre: Decimal,
    capability_id: str, model: str, endpoint: str, error: str = "",
) -> None:
    if not _should_deduct_credits() or pre <= 0:
        return
    db.refresh(user)
    user.credits = user_balance_decimal(user) + pre
    bal = quantize_credits(user.credits)
    append_credit_ledger(
        db, user.id, pre, "refund", bal,
        description=f"Comfly proxy 调用失败全额退款 ({endpoint})",
        ref_type="comfly_proxy",
        meta={
            "capability_id": capability_id, "model": model, "endpoint": endpoint,
            "refunded": credits_json_float(pre), "upstream": "comfly",
            "error": (error or "")[:500],
        },
    )
    db.commit()


async def _comfly_request(
    method: str, url: str, body: Optional[Dict[str, Any]], headers: Dict[str, str], timeout: float,
) -> Dict[str, Any]:
    """统一封装 httpx 调用 Comfly。失败抛 RuntimeError，含状态码与文本片段。"""
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        if method.upper() == "GET":
            r = await client.get(url, headers=headers)
        else:
            r = await client.post(url, headers=headers, json=body or {})
    if r.status_code >= 400:
        raise RuntimeError(f"Comfly HTTP {r.status_code}: {(r.text or '')[:500]}")
    try:
        return r.json() if r.content else {}
    except Exception:
        return {"_raw_text": r.text}


async def _yunwu_request(
    method: str, url: str, body: Optional[Dict[str, Any]], headers: Dict[str, str], timeout: float,
) -> Dict[str, Any]:
    """Yunwu HTTP wrapper. Keep the provider name out of Comfly error text."""
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        if method.upper() == "GET":
            r = await client.get(url, headers=headers)
        else:
            r = await client.post(url, headers=headers, json=body or {})
    if r.status_code >= 400:
        raise RuntimeError(f"Yunwu HTTP {r.status_code}: {(r.text or '')[:500]}")
    try:
        return r.json() if r.content else {}
    except Exception:
        return {"_raw_text": r.text}


async def _comfly_multipart_request(
    url: str,
    data: Dict[str, str],
    files: List[Tuple[str, Tuple[Any, ...]]],
    headers: Dict[str, str],
    timeout: float,
) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        r = await client.post(url, headers=headers, data=data, files=files)
    if r.status_code >= 400:
        raise RuntimeError(f"Comfly HTTP {r.status_code}: {(r.text or '')[:500]}")
    try:
        return r.json() if r.content else {}
    except Exception:
        return {"_raw_text": r.text}


async def _yunwu_multipart_request(
    url: str,
    data: Dict[str, str],
    files: List[Tuple[str, Tuple[Any, ...]]],
    headers: Dict[str, str],
    timeout: float,
) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        r = await client.post(url, headers=headers, data=data, files=files)
    if r.status_code >= 400:
        raise RuntimeError(f"Yunwu HTTP {r.status_code}: {(r.text or '')[:500]}")
    try:
        return r.json() if r.content else {}
    except Exception:
        return {"_raw_text": r.text}


async def _openai_official_multipart_request(
    url: str,
    data: Dict[str, str],
    files: List[Tuple[str, Tuple[Any, ...]]],
    headers: Dict[str, str],
    timeout: float,
) -> Dict[str, Any]:
    multipart_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        r = await client.post(url, headers=multipart_headers, data=data, files=files)
    if r.status_code >= 400:
        raise RuntimeError(f"OpenAI official HTTP {r.status_code}: {(r.text or '')[:500]}")
    try:
        return r.json() if r.content else {}
    except Exception:
        return {"_raw_text": r.text}


def _comfly_url(path: str, model: str = "") -> str:
    base, _ = get_comfly_config(_model_token_group(model))
    if not base:
        raise HTTPException(503, "服务端未配置 Comfly：缺少环境变量 COMFLY_API_BASE")
    return base.rstrip("/") + path


def _comfly_headers(model: str = "") -> Dict[str, str]:
    headers = _comfly_auth_headers(model)
    headers["Content-Type"] = "application/json"
    return headers


def _comfly_auth_headers(model: str = "") -> Dict[str, str]:
    _, key = get_comfly_config(_model_token_group(model))
    if not key:
        raise HTTPException(503, "服务端未配置 Comfly Key：缺少环境变量 COMFLY_API_KEY")
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


def _yunwu_base_url() -> str:
    base = (os.environ.get("YUNWU_API_BASE") or "https://yunwu.ai").strip().rstrip("/")
    return base or "https://yunwu.ai"


def _yunwu_api_key() -> str:
    key = (os.environ.get("YUNWU_API_KEY") or os.environ.get("COMFLY_API_KEY_YUNWU") or "").strip()
    if not key:
        raise HTTPException(503, "Server missing YUNWU_API_KEY")
    return key


def _yunwu_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_yunwu_api_key()}", "Accept": "application/json", "Content-Type": "application/json"}


def _yunwu_auth_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_yunwu_api_key()}", "Accept": "application/json"}


def _provider_route_label(token_group: str) -> str:
    group = str(token_group or "").strip().lower()
    return group if group else "comfly"


def _sutui_image_base_url() -> str:
    base = (
        os.environ.get("SUTUI_IMAGE_API_BASE")
        or os.environ.get("SUTUI_API_BASE")
        or getattr(settings, "sutui_api_base", None)
        or "https://api.xskill.ai"
    )
    return str(base or "https://api.xskill.ai").strip().rstrip("/")


async def _sutui_image_headers(*, multipart: bool = False) -> Dict[str, str]:
    token, pool_key = await next_sutui_server_token_with_pool()
    if not token:
        raise RuntimeError(f"Sutui shared token pool is not configured (pool={pool_key or 'none'})")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if not multipart:
        headers["Content-Type"] = "application/json"
    return headers


async def _sutui_image_request(source_body: Dict[str, Any]) -> Dict[str, Any]:
    body = dict(source_body or {})
    if not str(body.get("model") or "").strip():
        body["model"] = "openai/gpt-image-2"
    if body.get("size") and not body.get("image_size"):
        body["image_size"] = body.get("size")
    async with httpx.AsyncClient(timeout=_TIMEOUT_IMAGE, trust_env=False) as client:
        resp = await client.post(
            f"{_sutui_image_base_url()}/v1/images/generations",
            headers=await _sutui_image_headers(),
            json=body,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Sutui HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    try:
        payload = resp.json() if resp.content else {}
    except Exception:
        payload = {"_raw_text": resp.text}
    if isinstance(payload, dict):
        payload.setdefault("fallback_used", True)
        payload.setdefault("fallback_provider", "sutui")
        payload.setdefault("_provider", "sutui")
        payload.setdefault("_requested_model", body.get("model"))
    return payload


async def _sutui_multipart_request(
    path: str,
    data: Dict[str, str],
    files: List[Tuple[str, Tuple[Any, ...]]],
    timeout: float,
) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.post(
            f"{_sutui_image_base_url()}{path}",
            headers=await _sutui_image_headers(multipart=True),
            data=data,
            files=files,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Sutui HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    try:
        payload = resp.json() if resp.content else {}
    except Exception:
        payload = {"_raw_text": resp.text}
    if isinstance(payload, dict):
        payload.setdefault("fallback_used", True)
        payload.setdefault("fallback_provider", "sutui")
        payload.setdefault("_provider", "sutui")
        payload.setdefault("_requested_model", data.get("model"))
    return payload


def _is_retryable_image_error(exc: BaseException) -> bool:
    msg = str(exc or "").lower()
    if (
        "comfly http 400" in msg
        or "comfly http 401" in msg
        or "comfly http 403" in msg
        or "comfly http 404" in msg
        or "sutui http 400" in msg
        or "sutui http 401" in msg
        or "sutui http 403" in msg
        or "sutui http 404" in msg
    ):
        return False
    retry_tokens = (
        "comfly http 408",
        "comfly http 409",
        "comfly http 425",
        "comfly http 429",
        "comfly http 5",
        "sutui http 408",
        "sutui http 409",
        "sutui http 425",
        "sutui http 429",
        "sutui http 5",
        "timeout",
        "connect",
        "connection",
        "read",
        "network",
        "new_api_error",
        "unknown_error",
        "upstream",
        "上游",
        "未接收到上游响应内容",
    )
    return any(token in msg for token in retry_tokens)


def _extract_upstream_trace_id(error: Any) -> str:
    text = str(error or "")
    match = re.search(r"(?:traceid|trace_id|trace-id)\s*[:：]\s*([A-Za-z0-9_-]{8,96})", text, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _image_edit_failure_detail(error: Any) -> str:
    text = str(error or "")
    trace_id = _extract_upstream_trace_id(text)
    suffix = f"（traceid: {trace_id}）" if trace_id else ""
    lower = text.lower()
    if "系统繁忙" in text or "busy" in lower or "http 500" in lower or "http 503" in lower or "http 504" in lower:
        return f"图片生成上游服务繁忙，已自动重试但仍未成功，已自动退款，请稍后重试或切换模型。{suffix}"
    if "content_policy" in lower or "safety" in lower or "防护限制" in text or "违反" in text:
        return "图片生成失败：内容可能触发上游安全限制，已自动退款。请调整提示词或参考图后重试。"
    return f"图片生成失败，已自动重试但仍未成功，已自动退款，请稍后重试或切换模型。{suffix}"


def _public_image_failure_detail() -> str:
    return "图片生成失败，已自动重试但仍未成功，请稍后重试或切换模型。"


def _openmind_image_fallback_enabled() -> bool:
    return _env_bool("OPENMIND_IMAGE_FALLBACK_ENABLED", False) and bool((os.environ.get("OPENMIND_API_KEY") or "").strip())


def _openmind_image_url() -> str:
    base = (os.environ.get("OPENMIND_API_BASE") or "https://www.openmindapi.com").strip().rstrip("/")
    return (base or "https://www.openmindapi.com") + "/v1/images/generations"


def _openmind_image_headers() -> Dict[str, str]:
    key = (os.environ.get("OPENMIND_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENMIND_API_KEY is not configured")
    return {
        "User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _openai_official_image_url(path: str) -> str:
    base = (os.environ.get("OPENAI_IMAGE_API_BASE") or os.environ.get("OPENAI_API_BASE") or "https://api.openai.com/v1").strip().rstrip("/")
    return f"{base}{path}"


def _openai_official_image_api_key() -> str:
    key = (os.environ.get("OPENAI_IMAGE_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_IMAGE_API_KEY is not configured")
    return key


def _openai_official_image_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_openai_official_image_api_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _openai_official_image_model() -> str:
    return (os.environ.get("OPENAI_OFFICIAL_IMAGE_MODEL") or "gpt-image-1").strip() or "gpt-image-1"


def _is_openai_official_image_model(model: str) -> bool:
    value = str(model or "").strip().lower().replace("_", "-")
    return value in {"gpt-image-1", "gpt-image-1.5", "gpt-image-1-mini", "gpt-image-2", "gpt-image2", "gpt-image"}


def _coerce_openai_official_image_size(raw: Any) -> str:
    value = str(raw or "").strip().lower().replace(" ", "")
    if not value:
        return "1024x1024"
    if value == "auto":
        return "auto"

    known_ratio = _coerce_comfly_image_ratio_size(value)
    if known_ratio == "1:1":
        return "1024x1024"
    if known_ratio in {"3:4", "2:3", "9:16"}:
        return "1024x1536"
    if known_ratio in {"4:3", "3:2", "16:9"}:
        return "1536x1024"

    if "x" in value:
        parts = value.split("x", 1)
        try:
            width = int(parts[0])
            height = int(parts[1])
        except (TypeError, ValueError):
            width = 0
            height = 0
        if width > 0 and height > 0:
            if width == height:
                return "1024x1024"
            return "1024x1536" if height > width else "1536x1024"

    return "1024x1024"


def _openmind_image_body(source_body: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(source_body.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("OpenMind image fallback missing prompt")
    body: Dict[str, Any] = {
        "model": (os.environ.get("OPENMIND_IMAGE_MODEL") or "gpt-image-2").strip() or "gpt-image-2",
        "prompt": prompt,
        "size": _coerce_openmind_image_size(
            source_body.get("size")
            or source_body.get("image_size")
            or source_body.get("aspect_ratio")
            or source_body.get("ratio")
            or "1024x1024"
        ),
        "n": int(source_body.get("n") or 1),
        "response_format": str(source_body.get("response_format") or "url").strip() or "url",
    }
    image_url, image_urls = _normalized_image_refs_from_payload(source_body)
    if image_url:
        body["image_url"] = image_url
        body["image"] = image_url
    if image_urls:
        body["image_urls"] = image_urls
    return body


def _openai_official_image_body(source_body: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(source_body.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("OpenAI official image request missing prompt")
    body: Dict[str, Any] = {
        "model": _openai_official_image_model(),
        "prompt": prompt,
        "size": _coerce_openai_official_image_size(
            source_body.get("size")
            or source_body.get("image_size")
            or source_body.get("aspect_ratio")
            or source_body.get("ratio")
            or "1024x1024"
        ),
        "n": int(source_body.get("n") or source_body.get("num_images") or 1),
    }
    image_url, image_urls = _normalized_image_refs_from_payload(source_body)
    if image_url:
        body["image"] = image_url
        body["image_url"] = image_url
    if image_urls:
        body["image_urls"] = image_urls
    return body


def _extract_image_result_urls(payload: Any) -> List[str]:
    result: List[str] = []

    def add(value: Any) -> None:
        url = str(value or "").strip()
        if not url:
            return
        if url.startswith("data:image/") or url.startswith(("http://", "https://")):
            if url not in result:
                result.append(url)

    def visit(value: Any, depth: int = 0) -> None:
        if value is None or depth > 6:
            return
        if isinstance(value, str):
            add(value)
            if value.strip().startswith(("{", "[")):
                try:
                    visit(json.loads(value), depth + 1)
                except Exception:
                    pass
            return
        if isinstance(value, list):
            for item in value:
                visit(item, depth + 1)
            return
        if not isinstance(value, dict):
            return
        for key in ("url", "image_url", "source_url", "public_url", "file_url", "b64_json"):
            if key in value:
                val = value.get(key)
                if key == "b64_json" and val:
                    add(f"data:image/png;base64,{val}")
                else:
                    add(val)
        for item in value.values():
            visit(item, depth + 1)

    visit(payload)
    return result


def _image_edit_idempotency_key(user_id: int, raw: Any) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    safe = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_", ":"})[:96]
    if not safe:
        return ""
    return f"comfly:image_edit:result:{int(user_id)}:{safe}"


def _image_edit_fingerprint_key(user_id: int, data: Dict[str, str], files: List[Tuple[str, Tuple[Any, ...]]]) -> str:
    hasher = hashlib.sha256()
    for key in sorted(data.keys()):
        if key in {"response_format"}:
            continue
        hasher.update(str(key).encode("utf-8", "ignore"))
        hasher.update(b"=")
        hasher.update(str(data.get(key) or "").encode("utf-8", "ignore")[:2048])
        hasher.update(b"\n")
    for field_name, file_tuple in files[:12]:
        filename = str(file_tuple[0] if len(file_tuple) > 0 else "")
        file_obj = file_tuple[1] if len(file_tuple) > 1 else None
        content_type = str(file_tuple[2] if len(file_tuple) > 2 else "")
        try:
            current_pos = file_obj.tell()
            file_obj.seek(0, os.SEEK_END)
            size = int(file_obj.tell())
            file_obj.seek(0)
            head = file_obj.read(min(size, 65536)) or b""
            tail = b""
            if size > 65536:
                file_obj.seek(max(0, size - 65536))
                tail = file_obj.read(65536) or b""
            file_obj.seek(current_pos)
        except Exception:
            size = -1
            head = b""
            tail = b""
        hasher.update(str(field_name).encode("utf-8", "ignore"))
        hasher.update(filename.encode("utf-8", "ignore"))
        hasher.update(content_type.encode("utf-8", "ignore"))
        hasher.update(str(size).encode("ascii", "ignore"))
        hasher.update(hashlib.sha256(head).digest())
        hasher.update(hashlib.sha256(tail).digest())
    return f"comfly:image_edit:result:{int(user_id)}:fp:{hasher.hexdigest()[:32]}"


def _image_edit_buffer_fingerprint_key(
    user_id: int,
    data: Dict[str, str],
    buffered_files: List[Tuple[str, str, bytes, str]],
) -> str:
    hasher = hashlib.sha256()
    for key in sorted(data.keys()):
        if key in {"response_format"}:
            continue
        hasher.update(str(key).encode("utf-8", "ignore"))
        hasher.update(b"=")
        hasher.update(str(data.get(key) or "").encode("utf-8", "ignore")[:2048])
        hasher.update(b"\n")
    for field_name, filename, raw, content_type in buffered_files[:12]:
        raw_bytes = bytes(raw or b"")
        hasher.update(str(field_name).encode("utf-8", "ignore"))
        hasher.update(str(filename).encode("utf-8", "ignore"))
        hasher.update(str(content_type).encode("utf-8", "ignore"))
        hasher.update(str(len(raw_bytes)).encode("ascii", "ignore"))
        hasher.update(hashlib.sha256(raw_bytes[:65536]).digest())
        hasher.update(hashlib.sha256(raw_bytes[-65536:] if raw_bytes else b"").digest())
    return f"comfly:image_edit:result:{int(user_id)}:fp:{hasher.hexdigest()[:32]}"


async def _buffer_image_edit_uploads(
    files: List[Tuple[str, Tuple[Any, ...]]],
) -> List[Tuple[str, str, bytes, str]]:
    buffered: List[Tuple[str, str, bytes, str]] = []
    total = 0
    for field_name, file_tuple in files:
        filename = str(file_tuple[0] if len(file_tuple) > 0 else "image.png") or "image.png"
        file_obj = file_tuple[1] if len(file_tuple) > 1 else None
        content_type = str(file_tuple[2] if len(file_tuple) > 2 else "application/octet-stream") or "application/octet-stream"
        if file_obj is None:
            continue
        try:
            file_obj.seek(0)
        except Exception:
            pass
        raw = await asyncio.to_thread(file_obj.read)
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            continue
        raw_bytes = bytes(raw)
        if len(raw_bytes) > _MAX_GROK_REFERENCE_BYTES:
            raise HTTPException(status_code=413, detail="单张参考图不能超过 30MB")
        total += len(raw_bytes)
        if total > _MAX_IMAGE_EDIT_TOTAL_BYTES:
            raise HTTPException(status_code=413, detail="参考图总量不能超过 120MB")
        buffered.append((str(field_name), filename, raw_bytes, content_type))
    return buffered


def _image_edit_files_from_buffer(
    buffered: List[Tuple[str, str, bytes, str]],
) -> List[Tuple[str, Tuple[str, bytes, str]]]:
    return [(field_name, (filename, raw, content_type)) for field_name, filename, raw, content_type in buffered]


def _image_edit_data_for_attempt(
    source_data: Dict[str, str],
    *,
    requested_model: str,
    attempt_model: str,
    entry: Dict[str, Any],
) -> Dict[str, str]:
    token_group = str(entry.get("token_group") or "").strip().lower()
    if attempt_model == requested_model:
        data = dict(source_data)
    else:
        forwarded = _body_for_upstream_model(dict(source_data), attempt_model, entry)
        data = {}
        for key, value in forwarded.items():
            if value is None:
                continue
            if isinstance(value, (dict, list, tuple)):
                data[key] = json.dumps(value, ensure_ascii=False, default=str)
            else:
                data[key] = str(value)

    data["model"] = _upstream_model(attempt_model, entry)
    data.setdefault("prompt", str(source_data.get("prompt") or ""))
    data.setdefault("response_format", str(source_data.get("response_format") or "url") or "url")

    if token_group == "openai_official":
        try:
            num_images = max(1, int(data.get("n") or data.get("num_images") or source_data.get("n") or 1))
        except (TypeError, ValueError):
            num_images = 1
        official_data = {
            "model": data["model"],
            "prompt": str(data.get("prompt") or ""),
            "size": _coerce_openai_official_image_size(
                data.get("size")
                or data.get("image_size")
                or data.get("aspect_ratio")
                or data.get("ratio")
                or source_data.get("size")
                or source_data.get("image_size")
                or source_data.get("aspect_ratio")
                or "1024x1024"
            ),
            "n": str(num_images),
        }
        response_format = str(data.get("response_format") or "").strip()
        if response_format:
            official_data["response_format"] = response_format
        return official_data

    return data


async def _submit_image_edit_attempt(
    *,
    attempt_model: str,
    entry: Dict[str, Any],
    data: Dict[str, str],
    files: List[Tuple[str, Tuple[str, bytes, str]]],
) -> Dict[str, Any]:
    token_group = str(entry.get("token_group") or "").strip().lower()
    if token_group == "openai_official":
        return await _openai_official_multipart_request(
            _openai_official_image_url("/images/edits"),
            data,
            files,
            {"Authorization": f"Bearer {_openai_official_image_api_key()}", "Accept": "application/json"},
            _TIMEOUT_IMAGE,
        )
    if token_group == "yunwu":
        return await _yunwu_multipart_request(
            f"{_yunwu_base_url()}/v1/images/edits",
            data,
            files,
            _yunwu_auth_headers(),
            _TIMEOUT_IMAGE,
        )
    if token_group == "sutui":
        return await _sutui_multipart_request(
            "/v1/images/edits",
            data,
            files,
            _TIMEOUT_IMAGE,
        )
    if token_group == "openmindapi":
        return await _openmind_multipart_request(
            "/v1/images/edits",
            data,
            files,
            _TIMEOUT_IMAGE,
        )
    return await _comfly_multipart_request(
        _comfly_url("/v1/images/edits", attempt_model),
        data,
        files,
        _comfly_auth_headers(attempt_model),
        _TIMEOUT_IMAGE,
    )


def _cached_image_edit_response(key: str) -> Optional[Dict[str, Any]]:
    raw = cache_get(key) if key else None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


async def _wait_cached_image_edit_response(key: str, *, max_seconds: float = 75.0) -> Optional[Dict[str, Any]]:
    if not key or max_seconds <= 0:
        return None
    deadline = asyncio.get_running_loop().time() + max_seconds
    while True:
        payload = _cached_image_edit_response(key)
        if payload is not None:
            return payload
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(1.0)


def _guess_image_ext(content_type: str, url: str) -> str:
    lower_url = str(url or "").split("?", 1)[0].split("#", 1)[0].lower()
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        if lower_url.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    ct = (content_type or "").lower()
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    return ".png"


async def _download_image_bytes(url: str) -> Tuple[bytes, str, str]:
    src = str(url or "").strip()
    if src.startswith("data:image/"):
        header, _, b64 = src.partition(",")
        media = header[5:].split(";", 1)[0] if ":" in header else "image/png"
        import base64
        return base64.b64decode(b64), media or "image/png", ".png"
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True, trust_env=False) as client:
        resp = await client.get(src, headers={"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36"})
    resp.raise_for_status()
    media_type = (resp.headers.get("content-type") or "image/png").split(";", 1)[0].strip() or "image/png"
    return resp.content, media_type, _guess_image_ext(media_type, src)


def _is_remote_http_url(value: Any) -> bool:
    src = str(value or "").strip().lower()
    return src.startswith("http://") or src.startswith("https://")


async def _prepare_chat_image_urls_for_upstream(body: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """Comfly chat rejects remote image URLs, so inline only chat image parts before proxying."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body, 0

    prepared_body = copy.deepcopy(body)
    prepared_count = 0
    url_cache: Dict[str, str] = {}
    max_bytes = _env_int(
        "COMFLY_CHAT_IMAGE_PREPARE_MAX_MB",
        int(_MAX_CHAT_IMAGE_PREPARE_BYTES / (1024 * 1024)),
        min_value=1,
        max_value=80,
    ) * 1024 * 1024

    async def _to_data_url(url: str) -> str:
        cached = url_cache.get(url)
        if cached:
            return cached
        data, media_type, _ext = await _download_image_bytes(url)
        if len(data) > max_bytes:
            raise RuntimeError(f"图片过大，当前上限 {int(max_bytes / (1024 * 1024))}MB")
        import base64

        data_url = f"data:{media_type or 'image/png'};base64,{base64.b64encode(data).decode('ascii')}"
        url_cache[url] = data_url
        return data_url

    prepared_messages = prepared_body.get("messages")
    if not isinstance(prepared_messages, list):
        return prepared_body, 0
    for message in prepared_messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        parts = content if isinstance(content, list) else [content] if isinstance(content, dict) else []
        for part in parts:
            if not isinstance(part, dict):
                continue
            image_url = part.get("image_url")
            if isinstance(image_url, dict):
                url = str(image_url.get("url") or "").strip()
                if _is_remote_http_url(url):
                    image_url["url"] = await _to_data_url(url)
                    prepared_count += 1
            elif isinstance(image_url, str) and _is_remote_http_url(image_url):
                part["image_url"] = {"url": await _to_data_url(image_url)}
                prepared_count += 1
    return prepared_body, prepared_count


async def _persist_generated_image_asset(
    db: Session,
    *,
    user_id: int,
    url: str,
    prompt: str,
    model: str,
    job_id: str = "",
) -> Dict[str, Any]:
    data, media_type, ext = await _download_image_bytes(url)
    if len(data) > _MAX_GENERATED_IMAGE_PERSIST_BYTES:
        raise RuntimeError(
            f"generated image exceeds {int(_MAX_GENERATED_IMAGE_PERSIST_BYTES / (1024 * 1024))}MB"
        )
    aid, fname_or_key, fsize, tos_public_url = await _run_asset_upload_io(
        _save_bytes_or_tos,
        data,
        ext,
        media_type,
    )
    if not tos_public_url:
        local_path = Path(__file__).resolve().parent.parent.parent.parent / "assets" / fname_or_key
        try:
            if local_path.exists():
                local_path.unlink()
        except OSError:
            pass
        raise RuntimeError("图片结果保存失败：TOS 公网链接不可用")
    asset = Asset(
        asset_id=aid,
        user_id=user_id,
        filename=fname_or_key,
        media_type="image",
        file_size=fsize,
        source_url=tos_public_url,
        prompt=prompt,
        model=model,
        tags="auto,image_generate,miniprogram",
        meta={"source": "miniprogram_image_generate", "job_id": job_id, "origin_url": url},
    )
    db.add(asset)
    db.flush()
    return {
        "asset_id": aid,
        "media_type": "image",
        "url": tos_public_url,
        "source_url": tos_public_url,
        "file_size": fsize,
        "prompt": prompt,
        "model": model,
    }


async def _save_generated_images_best_effort(
    db: Session,
    *,
    user_id: int,
    response_payload: Dict[str, Any],
    prompt: str,
    model: str,
    limit: int,
    exclude_urls: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    saved_assets: List[Dict[str, Any]] = []
    excluded = {str(url or "").strip().rstrip("/") for url in (exclude_urls or []) if str(url or "").strip()}
    for url in _extract_image_result_urls(response_payload)[: max(1, min(9, int(limit or 1)))]:
        if str(url or "").strip().rstrip("/") in excluded:
            logger.info("[image_generate] skip echoed reference image url=%s", str(url)[:120])
            continue
        try:
            saved_assets.append(
                await _persist_generated_image_asset(
                    db,
                    user_id=user_id,
                    url=url,
                    prompt=prompt,
                    model=model,
                )
            )
        except Exception as exc:
            logger.warning("[image_generate] save generated image failed user_id=%s url=%s err=%s", user_id, url[:120], exc)
    if saved_assets:
        db.commit()
    return saved_assets


async def _openmind_image_request(source_body: Dict[str, Any]) -> Dict[str, Any]:
    body = _openmind_image_body(source_body)
    timeout = httpx.Timeout(
        connect=10.0,
        read=_TIMEOUT_OPENMIND_IMAGE_READ,
        write=30.0,
        pool=8.0,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.post(_openmind_image_url(), headers=_openmind_image_headers(), json=body)
    except httpx.TimeoutException as exc:
        raise RuntimeError(
            f"OpenMind image request timed out after {_TIMEOUT_OPENMIND_IMAGE_READ:.0f}s"
        ) from exc
    except httpx.TransportError as exc:
        raise RuntimeError(f"OpenMind image transport error: {exc!r}") from exc
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenMind HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    try:
        payload = resp.json() if resp.content else {}
    except Exception:
        payload = {"_raw_text": resp.text}
    if isinstance(payload, dict):
        payload.setdefault("fallback_used", True)
        payload.setdefault("fallback_provider", "openmind")
    return payload


async def _openmind_multipart_request(
    path: str,
    data: Dict[str, str],
    files: List[Tuple[str, Tuple[Any, ...]]],
    timeout: float,
) -> Dict[str, Any]:
    headers = _openmind_image_headers()
    headers.pop("Content-Type", None)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.post(
            f"{_openmind_image_base_url()}{path}",
            headers=headers,
            data=data,
            files=files,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenMind HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    try:
        payload = resp.json() if resp.content else {}
    except Exception:
        payload = {"_raw_text": resp.text}
    if isinstance(payload, dict):
        payload.setdefault("fallback_used", True)
        payload.setdefault("fallback_provider", "openmind")
        payload.setdefault("_provider", "openmind")
    return payload


async def _openai_official_image_request(source_body: Dict[str, Any]) -> Dict[str, Any]:
    body = _openai_official_image_body(source_body)
    async with httpx.AsyncClient(timeout=_TIMEOUT_IMAGE, trust_env=False) as client:
        resp = await client.post(
            _openai_official_image_url("/images/generations"),
            headers=_openai_official_image_headers(),
            json=body,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI official HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    try:
        payload = resp.json() if resp.content else {}
    except Exception:
        payload = {"_raw_text": resp.text}
    if isinstance(payload, dict):
        payload.setdefault("fallback_used", True)
        payload.setdefault("fallback_provider", "openai_official")
        payload.setdefault("_provider", "openai_official")
        payload.setdefault("_requested_model", body.get("model"))
    return payload





def _openmind_video_base_url() -> str:
    base = (os.environ.get("OPENMIND_API_BASE") or "https://www.openmindapi.com").strip().rstrip("/")
    return base or "https://www.openmindapi.com"


def _is_openmind_seedance_model(model: str) -> bool:
    value = str(model or "").strip().lower().replace("_", "-").replace(" ", "")
    return value in {
        "doubao-seedance-2-0-260128",
        "doubao-seedance-2-0-fast-260128",
        "seedance-2-0-260128",
        "seedance-2-0-fast-260128",
        "seedance2.0",
        "seedance2.0-hd",
        "seedance2.0-4k",
        "seedance2.0-480",
        "seedance2.0-mini-720",
        "seedance2.0-mini-480",
    }


def _openmind_video_api_key(model: str = "") -> str:
    if _is_openmind_seedance_model(model):
        key = (
            os.environ.get("OPENMIND_SEEDANCE_API_KEY")
            or os.environ.get("OPENMIND_API_KEY")
            or ""
        ).strip()
        if not key:
            raise HTTPException(
                503,
                "Server missing OPENMIND_SEEDANCE_API_KEY or OPENMIND_API_KEY",
            )
        return key
    key = (os.environ.get("OPENMIND_API_KEY") or "").strip()
    if not key:
        raise HTTPException(503, "Server missing OPENMIND_API_KEY")
    return key


def _openmind_video_headers(model: str = "") -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_openmind_video_api_key(model)}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
    }


def _openmind_enabled_for_video() -> bool:
    raw = (os.environ.get("OPENMIND_VIDEO_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def _openmind_video_model(model: str) -> str:
    raw = (model or "").strip()
    low = raw.lower().replace("_", "-").replace(" ", "")
    seedance_model = (
        os.environ.get("OPENMIND_SEEDANCE_MODEL") or "seedance2.0"
    ).strip() or "seedance2.0"
    seedance_fast_model = (
        os.environ.get("OPENMIND_SEEDANCE_FAST_MODEL") or seedance_model
    ).strip() or seedance_model
    explicit = {
        "veo3.1": os.environ.get("OPENMIND_VEO31_MODEL") or "veo31",
        "veo3.1-fast": os.environ.get("OPENMIND_VEO31_FAST_MODEL") or "veo31-fast",
        "veo31": os.environ.get("OPENMIND_VEO31_MODEL") or "veo31",
        "veo31-fast": os.environ.get("OPENMIND_VEO31_FAST_MODEL") or "veo31-fast",
        "grok-video-3": os.environ.get("OPENMIND_GROK_VIDEO_MODEL") or "grok-imagine-video-1.5",
        "grok-imagine-video-1.5-preview": os.environ.get("OPENMIND_GROK_VIDEO_MODEL") or "grok-imagine-video-1.5",
        "doubao-seedance-2-0-260128": seedance_model,
        "doubao-seedance-2-0-fast-260128": seedance_fast_model,
    }
    if low in explicit:
        return (explicit[low] or "").strip() or raw
    return raw


def _openmind_video_body(body: Dict[str, Any], model: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    forwarded = dict(body or {})
    forwarded["model"] = _openmind_video_model(model)
    duration_value = None
    for key in ("duration", "seconds"):
        if key in forwarded and forwarded.get(key) is not None:
            try:
                duration_value = int(float(forwarded.get(key)))
                break
            except (TypeError, ValueError):
                forwarded.pop(key, None)
    if duration_value is not None:
        # OpenMind /v1/videos ?????? duration ? int?????? seconds?
        forwarded["duration"] = duration_value
        forwarded.pop("seconds", None)
    if not forwarded.get("aspect_ratio") and forwarded.get("ratio"):
        forwarded["aspect_ratio"] = forwarded.get("ratio")
    forwarded.setdefault("aspect_ratio", "9:16")
    forwarded.setdefault("resolution", "720p")
    if not forwarded.get("size"):
        forwarded["size"] = {
            "9:16": "720x1280",
            "16:9": "1280x720",
            "1:1": "1024x1024",
            "4:5": "864x1080",
        }.get(str(forwarded.get("aspect_ratio") or ""), "720x1280")
    _image_ref, images = _normalized_image_refs_from_payload(forwarded)
    if images:
        forwarded["images"] = images
        forwarded["image_urls"] = images
        forwarded.setdefault("image", images[0])
        forwarded.setdefault("image_url", images[0])
    return forwarded


async def _openmind_video_submit(body: Dict[str, Any], model: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    if not _openmind_enabled_for_video():
        raise RuntimeError("OpenMind video channel disabled")
    upstream_body = _openmind_video_body(body, model, entry)
    url = f"{_openmind_video_base_url()}/v1/videos"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_OPENMIND_VIDEO_SUBMIT, follow_redirects=True) as client:
            r = await client.post(
                url,
                headers=_openmind_video_headers(model),
                json=upstream_body,
            )
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"OpenMind videos submit timeout after {_TIMEOUT_OPENMIND_VIDEO_SUBMIT}s") from exc
    except httpx.TransportError as exc:
        raise RuntimeError(f"OpenMind videos transport error: {exc!r}") from exc
    if r.status_code >= 400:
        raise RuntimeError(f"OpenMind videos HTTP {r.status_code}: {(r.text or '')[:500]}")
    try:
        payload = r.json() if r.content else {}
    except Exception:
        payload = {"_raw_text": r.text}
    if isinstance(payload, dict):
        payload.setdefault("_provider", "openmind")
        payload.setdefault("_requested_model", upstream_body.get("model"))
    return payload


def _extract_openmind_video_url(payload: Dict[str, Any]) -> str:
    """Find the completed video URL without mistaking an input image URL for output."""
    if not isinstance(payload, dict):
        return ""
    direct_keys = (
        "video_url", "videoUrl", "content_url", "contentUrl",
        "download_url", "downloadUrl", "output_url", "outputUrl",
    )
    for key in direct_keys:
        value = str(payload.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    for container_key in ("video", "output", "result", "data", "content"):
        container = payload.get(container_key)
        if isinstance(container, str):
            value = container.strip()
            if value.startswith(("http://", "https://")):
                return value
        if isinstance(container, dict):
            for key in direct_keys + ("url",):
                value = str(container.get(key) or "").strip()
                if value.startswith(("http://", "https://")):
                    return value
    return ""


def _replace_openmind_video_url(payload: Dict[str, Any], source_url: str, public_url: str) -> None:
    video = payload.get("video")
    if isinstance(video, dict):
        video["source_url"] = source_url
        video["url"] = public_url
    payload["video_url"] = public_url
    payload["tos_url"] = public_url
    payload["source_video_url"] = source_url


def _mark_openmind_video_tos_transfer_queued(payload: Dict[str, Any], source_url: str) -> None:
    video = payload.get("video")
    if isinstance(video, dict):
        video.setdefault("source_url", source_url)
    payload.setdefault("source_video_url", source_url)
    payload["tos_transfer_status"] = "queued"
    payload.pop("tos_transfer_error", None)


def _video_transfer_base_url() -> str:
    return (
        os.environ.get("VIDEO_TRANSFER_API_BASE")
        or os.environ.get("XAI_API_BASE")
        or ""
    ).strip().rstrip("/")


def _video_transfer_token() -> str:
    return (os.environ.get("VIDEO_TRANSFER_TOKEN") or "").strip()


async def _transfer_video_to_tos_via_proxy(source_url: str, *, task_id: str) -> Tuple[str, int]:
    base_url = _video_transfer_base_url()
    token = _video_transfer_token()
    if not base_url:
        raise RuntimeError("VIDEO_TRANSFER_API_BASE is not configured")
    if not token:
        raise RuntimeError("VIDEO_TRANSFER_TOKEN is not configured")

    safe_task_id = "".join(
        char if char.isalnum() or char in "-_" else "_"
        for char in str(task_id or "video")
    )[:120]
    request_body = {
        "url": source_url,
        "filename": f"openmind-{safe_task_id or 'video'}.mp4",
        "content_type": "video/mp4",
    }
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(330.0, connect=30.0),
        follow_redirects=True,
        trust_env=False,
    ) as client:
        response = await client.post(
            f"{base_url}/media/transfer-to-tos",
            headers={
                "X-Video-Transfer-Token": token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=request_body,
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"video transfer proxy HTTP {response.status_code}: {(response.text or '')[:300]}"
        )
    try:
        result = response.json() if response.content else {}
    except Exception as exc:
        raise RuntimeError("video transfer proxy returned invalid JSON") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        detail = result.get("detail") if isinstance(result, dict) else "invalid response"
        raise RuntimeError(f"video transfer proxy failed: {str(detail or result)[:300]}")

    tos_url = str(result.get("tos_url") or "").strip()
    if not tos_url.startswith(("http://", "https://")):
        raise RuntimeError("video transfer proxy returned no public TOS URL")
    try:
        size = int(result.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size > _MAX_OPENMIND_VIDEO_BYTES:
        raise RuntimeError("transferred OpenMind video exceeds 512MB")
    return tos_url, size


async def _transfer_openmind_video_to_tos_in_background(source_url: str, *, task_id: str, cache_key: str) -> None:
    try:
        async with background_heavy_slot("openmind_video_tos_transfer"):
            tos_public_url, transferred_size = await _transfer_video_to_tos_via_proxy(
                source_url,
                task_id=task_id,
            )
            _openmind_tos_url_cache[cache_key] = tos_public_url
            while len(_openmind_tos_url_cache) > _MAX_OPENMIND_TOS_URL_CACHE:
                _openmind_tos_url_cache.popitem(last=False)
            cache_set(f"comfly:openmind-video-tos:{cache_key}", tos_public_url, ttl_seconds=24 * 60 * 60)
            try:
                source_host = httpx.URL(source_url).host or "unknown"
            except Exception:
                source_host = "unknown"
            logger.info(
                "OpenMind video mirrored to TOS via proxy task_id=%s source_host=%s size=%s url=%s",
                task_id,
                source_host,
                transferred_size,
                tos_public_url[:100],
            )
    except WorkloadQueueFull:
        logger.warning(
            "OpenMind video TOS transfer skipped because background queue is full task_id=%s",
            task_id,
        )
    except Exception as exc:
        cache_set(f"comfly:openmind-video-tos-error:{cache_key}", str(exc)[:300], ttl_seconds=10 * 60)
        logger.warning(
            "OpenMind video TOS transfer failed task_id=%s error=%s",
            task_id,
            str(exc)[:300],
        )


async def _mirror_openmind_video_to_tos(payload: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    source_url = _extract_openmind_video_url(payload)
    if not source_url:
        return payload
    public_domain = ""
    try:
        from .assets import _get_tos_config
        cfg = _get_tos_config() or {}
        public_domain = str(cfg.get("public_domain") or "").strip().rstrip("/")
    except Exception:
        pass
    if public_domain and source_url.startswith(public_domain + "/"):
        return payload

    cache_key = f"{task_id}:{source_url}"
    cached_url = _openmind_tos_url_cache.get(cache_key) or cache_get(f"comfly:openmind-video-tos:{cache_key}")
    if cached_url:
        _openmind_tos_url_cache[cache_key] = cached_url
        _replace_openmind_video_url(payload, source_url, cached_url)
        payload["tos_transfer_status"] = "completed"
        payload.pop("tos_transfer_error", None)
        return payload

    error = cache_get(f"comfly:openmind-video-tos-error:{cache_key}")
    _mark_openmind_video_tos_transfer_queued(payload, source_url)
    if error:
        payload["tos_transfer_error"] = error
    claim_key = f"comfly:openmind-video-tos-claim:{cache_key}"
    if cache_set_if_absent(claim_key, "1", ttl_seconds=15 * 60):
        task_coro = _transfer_openmind_video_to_tos_in_background(
            source_url,
            task_id=task_id,
            cache_key=cache_key,
        )
        try:
            spawn_tracked_task(task_coro, name=f"openmind-video-tos-transfer-{task_id}")
        except Exception:
            task_coro.close()
            cache_delete(claim_key)
            payload["tos_transfer_error"] = "failed to queue TOS transfer"
            logger.exception("OpenMind video TOS transfer queue failed task_id=%s", task_id)
    return payload


async def _openmind_video_poll(task_id: str, model: str = "") -> Dict[str, Any]:
    if not _openmind_enabled_for_video():
        raise RuntimeError("OpenMind video channel disabled")
    url = f"{_openmind_video_base_url()}/v1/videos/{task_id}"
    async with httpx.AsyncClient(timeout=_TIMEOUT_VIDEO_POLL, follow_redirects=True) as client:
        r = await client.get(url, headers=_openmind_video_headers(model))
    if r.status_code >= 400:
        raise RuntimeError(f"OpenMind videos poll HTTP {r.status_code}: {(r.text or '')[:500]}")
    try:
        payload = r.json() if r.content else {}
    except Exception:
        payload = {"_raw_text": r.text}
    if isinstance(payload, dict):
        payload.setdefault("_provider", "openmind")
        if _extract_openmind_video_url(payload):
            payload = await _mirror_openmind_video_to_tos(payload, task_id)
    return payload


def _xing_seedance_base_url() -> str:
    base = (os.environ.get("XING_SEEDANCE_API_BASE") or "https://xingapi.top").strip().rstrip("/")
    return base or "https://xingapi.top"


def _xing_seedance_api_key() -> str:
    key = (os.environ.get("XING_SEEDANCE_API_KEY") or "").strip()
    if not key:
        raise HTTPException(503, "Server missing XING_SEEDANCE_API_KEY")
    return key


def _xing_seedance_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_xing_seedance_api_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _xing_seedance_body(body: Dict[str, Any], model: str) -> Dict[str, Any]:
    source = dict(body or {})
    prompt = str(source.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "missing prompt")
    try:
        duration = int(float(source.get("duration") or source.get("seconds") or 10))
    except (TypeError, ValueError):
        duration = 10
    if duration <= 0:
        raise HTTPException(400, "duration must be a positive integer")
    ratio = str(source.get("ratio") or source.get("aspect_ratio") or "9:16").strip()
    if ratio not in {"16:9", "9:16"}:
        ratio = "9:16"
    return {
        "model": (model or os.environ.get("XING_SEEDANCE_MODEL") or "seedance2.0-900").strip(),
        "prompt": prompt,
        "duration": duration,
        "resolution": str(source.get("resolution") or "720p").strip() or "720p",
        "ratio": ratio,
    }


async def _xing_seedance_submit(body: Dict[str, Any], model: str) -> Dict[str, Any]:
    upstream_body = _xing_seedance_body(body, model)
    url = f"{_xing_seedance_base_url()}/v1/videos/generations"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_VIDEO_SUBMIT, follow_redirects=True, trust_env=False) as client:
            response = await client.post(url, headers=_xing_seedance_headers(), json=upstream_body)
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"Xing Seedance submit timeout after {_TIMEOUT_VIDEO_SUBMIT}s") from exc
    except httpx.TransportError as exc:
        raise RuntimeError(f"Xing Seedance transport error: {exc!r}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"Xing Seedance HTTP {response.status_code}: {(response.text or '')[:700]}")
    try:
        payload = response.json() if response.content else {}
    except Exception:
        payload = {"_raw_text": response.text}
    if isinstance(payload, dict):
        payload.setdefault("_provider", "xing")
        payload.setdefault("_requested_model", upstream_body.get("model"))
        task_id = _task_id_from_response(payload)
        if task_id:
            payload.setdefault("task_id", task_id)
    return payload


async def _xing_seedance_poll(task_id: str) -> Dict[str, Any]:
    safe_task_id = (task_id or "").strip()
    if not safe_task_id:
        raise HTTPException(400, "missing task_id")
    url = f"{_xing_seedance_base_url()}/v1/videos/{safe_task_id}"
    async with httpx.AsyncClient(timeout=_TIMEOUT_VIDEO_POLL, follow_redirects=True, trust_env=False) as client:
        response = await client.get(url, headers=_xing_seedance_headers())
    if response.status_code >= 400:
        raise RuntimeError(f"Xing Seedance poll HTTP {response.status_code}: {(response.text or '')[:700]}")
    try:
        payload = response.json() if response.content else {}
    except Exception:
        payload = {"_raw_text": response.text}
    if isinstance(payload, dict):
        payload.setdefault("_provider", "xing")
        payload.setdefault("task_id", safe_task_id)
    return payload


async def _xing_seedance_content(task_id: str) -> Response:
    safe_task_id = (task_id or "").strip()
    if not safe_task_id:
        raise HTTPException(400, "missing task_id")
    url = f"{_xing_seedance_base_url()}/v1/videos/{safe_task_id}/content"
    async with httpx.AsyncClient(timeout=_TIMEOUT_VIDEO_POLL, follow_redirects=True, trust_env=False) as client:
        upstream = await client.get(url, headers=_xing_seedance_headers())
    if upstream.status_code >= 400:
        raise HTTPException(upstream.status_code, f"Xing Seedance content HTTP {upstream.status_code}: {(upstream.text or '')[:500]}")
    response_headers = {}
    for name in ("content-length", "content-disposition", "cache-control"):
        value = upstream.headers.get(name)
        if value:
            response_headers[name] = value
    return Response(content=upstream.content, media_type=upstream.headers.get("content-type") or "video/mp4", headers=response_headers)


def _xai_video_base_url() -> str:
    base = (os.environ.get("XAI_API_BASE") or "https://api.x.ai").strip().rstrip("/")
    return base or "https://api.x.ai"


def _xai_video_api_key() -> str:
    key = (os.environ.get("XAI_API_KEY") or "").strip()
    if not key:
        raise HTTPException(503, "Server missing XAI_API_KEY")
    return key


def _xai_video_body(body: Dict[str, Any], model: str) -> Dict[str, Any]:
    source = dict(body or {})
    prompt = str(source.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "missing prompt")
    try:
        duration = int(float(source.get("duration") or source.get("seconds") or 10))
    except (TypeError, ValueError):
        duration = 10
    if duration <= 0:
        raise HTTPException(400, "duration must be a positive integer")
    image_url = ""
    image = source.get("image")
    if isinstance(image, dict):
        image_url = str(image.get("url") or "").strip()
    elif image:
        image_url = str(image).strip()
    if not image_url:
        image_url = str(source.get("image_url") or "").strip()
    if not image_url:
        for key in ("images", "image_urls"):
            references = source.get(key)
            if not isinstance(references, list):
                continue
            for item in references:
                candidate = str(item.get("url") or "").strip() if isinstance(item, dict) else str(item or "").strip()
                if candidate:
                    image_url = candidate
                    break
            if image_url:
                break
    forwarded: Dict[str, Any] = {
        "model": (os.environ.get("XAI_VIDEO_MODEL") or model or "grok-imagine-video-1.5").strip(),
        "prompt": prompt,
        "duration": duration,
    }
    aspect_ratio = str(source.get("aspect_ratio") or source.get("ratio") or "").strip()
    if aspect_ratio:
        forwarded["aspect_ratio"] = aspect_ratio
    resolution = str(source.get("resolution") or "").strip().lower()
    if resolution:
        forwarded["resolution"] = resolution
    if image_url:
        forwarded["image"] = {"url": image_url}
    return forwarded


def _xai_video_request_headers(*, json_body: bool = False) -> Dict[str, str]:
    base = _xai_video_base_url().lower().rstrip("/")
    proxy_token = (os.environ.get("XAI_PROXY_TOKEN") or "").strip()
    if base != "https://api.x.ai" and proxy_token:
        token = proxy_token
    else:
        token = _xai_video_api_key()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


async def _xai_video_submit(body: Dict[str, Any], model: str) -> Dict[str, Any]:
    upstream_body = _xai_video_body(body, model)
    url = f"{_xai_video_base_url()}/v1/videos/generations"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_XAI_VIDEO_SUBMIT, follow_redirects=True) as client:
            response = await client.post(
                url,
                headers=_xai_video_request_headers(json_body=True),
                json=upstream_body,
            )
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"xAI videos submit timeout after {_TIMEOUT_XAI_VIDEO_SUBMIT}s") from exc
    except httpx.TransportError as exc:
        raise RuntimeError(f"xAI videos transport error: {exc!r}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"xAI videos HTTP {response.status_code}: {(response.text or '')[:500]}")
    try:
        payload = response.json() if response.content else {}
    except Exception:
        payload = {"_raw_text": response.text}
    if isinstance(payload, dict):
        payload.setdefault("_provider", "xai")
        payload.setdefault("_requested_model", upstream_body.get("model"))
        request_id = str(payload.get("request_id") or payload.get("id") or "").strip()
        if request_id:
            payload.setdefault("request_id", request_id)
            payload.setdefault("task_id", request_id)
    return payload


async def _xai_video_poll(request_id: str) -> Dict[str, Any]:
    safe_request_id = (request_id or "").strip()
    if not safe_request_id:
        raise HTTPException(400, "missing request_id")
    url = f"{_xai_video_base_url()}/v1/videos/{safe_request_id}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_VIDEO_POLL, follow_redirects=True) as client:
            response = await client.get(
                url,
                headers=_xai_video_request_headers(),
            )
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"xAI videos poll timeout after {_TIMEOUT_VIDEO_POLL}s") from exc
    except httpx.TransportError as exc:
        raise RuntimeError(f"xAI videos poll transport error: {exc!r}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"xAI videos poll HTTP {response.status_code}: {(response.text or '')[:500]}")
    try:
        payload = response.json() if response.content else {}
    except Exception:
        payload = {"_raw_text": response.text}
    if isinstance(payload, dict):
        payload.setdefault("_provider", "xai")
        payload.setdefault("request_id", safe_request_id)
        payload.setdefault("task_id", safe_request_id)
    return payload


def _video_image_resubmit_pending_payload(
    root_task_id: str,
    *,
    provider: str,
    provider_task_id: str,
) -> Dict[str, Any]:
    return {
        "status": "pending",
        "progress": 0,
        "request_id": root_task_id,
        "task_id": root_task_id,
        "_provider": provider,
        "_resubmitted": True,
        "_resubmit_reason": "image_download_interrupted",
        "_provider_task_id": provider_task_id,
    }


async def _maybe_resubmit_interrupted_video(
    root_task_id: str,
    *,
    provider: str,
    payload: Dict[str, Any],
    request_user_id: int,
) -> Optional[Dict[str, Any]]:
    if not _is_image_download_interrupted_payload(payload):
        return None
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider not in {"xai", "openmind"}:
        return None

    async with _video_image_retry_lock:
        resolved_root, active_task_id, context = _video_image_retry_poll_target(
            root_task_id,
            provider=normalized_provider,
            request_user_id=request_user_id,
        )
        if not context or int(context.get("resubmit_count") or 0) >= 1:
            return None

        claimed = cache_set_if_absent(
            _video_image_retry_claim_key(resolved_root),
            str(os.getpid()),
            120,
        )
        if not claimed:
            return _video_image_resubmit_pending_payload(
                resolved_root,
                provider=normalized_provider,
                provider_task_id=active_task_id,
            )

        context["resubmit_count"] = 1
        context["resubmit_state"] = "submitting"
        _store_video_image_retry_context(resolved_root, context)
        body = dict(context.get("body") or {})
        model = str(context.get("model") or "").strip()
        try:
            if normalized_provider == "xai":
                replacement = await _xai_video_submit(body, model)
            else:
                replacement = await _openmind_video_submit(
                    body,
                    model,
                    _require_model_entry(model),
                )
            replacement_task_id = _task_id_from_response(replacement)
            if not replacement_task_id:
                raise RuntimeError(f"{normalized_provider} retry submit returned no task id")
        except Exception as exc:
            context["resubmit_error"] = str(exc)[:500]
            context["resubmit_state"] = "failed"
            _store_video_image_retry_context(resolved_root, context)
            _audit(
                "video_image_download_resubmit_failed",
                user_id=request_user_id,
                provider=normalized_provider,
                model=model,
                root_task_id=resolved_root,
                failed_task_id=active_task_id,
                error=str(exc)[:300],
                billing_reused=True,
            )
            return None

        context["active_task_id"] = replacement_task_id
        context["replacement_task_id"] = replacement_task_id
        context["resubmit_state"] = "active"
        _store_video_image_retry_context(resolved_root, context)
        _store_video_image_retry_root(replacement_task_id, resolved_root)
        _audit(
            "video_image_download_resubmit_ok",
            user_id=request_user_id,
            provider=normalized_provider,
            model=model,
            root_task_id=resolved_root,
            failed_task_id=active_task_id,
            replacement_task_id=replacement_task_id,
            billing_reused=True,
        )
        return _video_image_resubmit_pending_payload(
            resolved_root,
            provider=normalized_provider,
            provider_task_id=replacement_task_id,
        )


def _normalize_retried_video_poll_payload(
    payload: Dict[str, Any],
    *,
    root_task_id: str,
    active_task_id: str,
) -> Dict[str, Any]:
    if root_task_id == active_task_id or not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    normalized["_provider_task_id"] = active_task_id
    normalized["_resubmitted"] = True
    normalized["request_id"] = root_task_id
    normalized["task_id"] = root_task_id
    return normalized


async def _openmind_video_content(task_id: str, model: str = "") -> Response:
    """Fetch protected OpenMind video bytes with the server-side provider key."""
    safe_task_id = (task_id or "").strip()
    if not safe_task_id:
        raise HTTPException(400, "missing task_id")
    url = f"{_openmind_video_base_url()}/v1/videos/{safe_task_id}/content"
    async with httpx.AsyncClient(timeout=_TIMEOUT_VIDEO_POLL, follow_redirects=True, trust_env=False) as client:
        upstream = await client.get(url, headers=_openmind_video_headers(model))
    if upstream.status_code >= 400:
        raise HTTPException(upstream.status_code, f"OpenMind video content HTTP {upstream.status_code}: {(upstream.text or '')[:500]}")
    media_type = upstream.headers.get("content-type") or "video/mp4"
    response_headers = {}
    for name in ("content-length", "content-disposition", "cache-control"):
        value = upstream.headers.get(name)
        if value:
            response_headers[name] = value
    return Response(content=upstream.content, media_type=media_type, headers=response_headers)


def _task_id_from_response(resp: Dict[str, Any]) -> str:
    if not isinstance(resp, dict):
        return ""
    for key in ("id", "task_id", "video_id", "job_id", "request_id", "generation_id", "run_id"):
        value = resp.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    data = resp.get("data")
    if isinstance(data, dict):
        for key in ("id", "task_id", "video_id", "job_id", "request_id", "generation_id", "run_id"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _remember_proxy_video_task(task_id: str, api_kind: str = "", model: str = "") -> None:
    tid = (task_id or "").strip()
    if not tid:
        return
    _proxy_video_task_meta[tid] = ((api_kind or "").strip(), (model or "").strip())
    while len(_proxy_video_task_meta) > _MAX_PROXY_VIDEO_TASK_TRACK:
        _proxy_video_task_meta.popitem(last=False)


def _proxy_video_task_hint(task_id: str) -> Tuple[str, str]:
    return _proxy_video_task_meta.get((task_id or "").strip(), ("", ""))

def _require_model_entry(model: str) -> Dict[str, Any]:
    entry = lookup_comfly_model(model)
    if not entry:
        raise HTTPException(400, f"模型 {model} 未在 comfly_pricing.json 注册，无法计费")
    return entry


def _upstream_model(model: str, entry: Dict[str, Any]) -> str:
    return str(entry.get("comfly_model") or model).strip() or model


def _coerce_grok_video_resolution(raw: Any) -> str:
    s = str(raw or "").strip().lower().replace(" ", "")
    if "480" in s:
        return "480p"
    return "720p"


def _is_grok_api_format(entry: Dict[str, Any]) -> bool:
    return str((entry or {}).get("api_format") or "").strip().lower() == "grok"


def _is_comfyui_grok_api_format(entry: Dict[str, Any]) -> bool:
    return str((entry or {}).get("api_format") or "").strip().lower() == "comfyui_grok"


def _coerce_grok15_model(duration: Any) -> str:
    try:
        seconds = int(float(duration or 0))
    except (TypeError, ValueError):
        seconds = 6
    if seconds <= 6:
        return "grok-1.5-video-6s"
    if seconds <= 10:
        return "grok-1.5-video-10s"
    return "grok-1.5-video-15s"


def _coerce_video_size_from_ratio(raw: Any) -> str:
    ratio = str(raw or "").strip().lower().replace(" ", "")
    mapping = {
        "16:9": "1280x720",
        "9:16": "720x1280",
        "1:1": "1024x1024",
        "4:3": "1280x960",
        "3:4": "960x1280",
        "3:2": "1200x800",
        "2:3": "800x1200",
    }
    return mapping.get(ratio, "720x1280")


def _coerce_openmind_image_size(raw: Any) -> str:
    value = str(raw or "").strip().lower().replace(" ", "")
    if "x" in value:
        parts = value.split("x", 1)
        try:
            width = int(parts[0])
            height = int(parts[1])
        except (TypeError, ValueError):
            width = 0
            height = 0
        if width > 0 and height > 0:
            if width % 16 == 0 and height % 16 == 0:
                return f"{width}x{height}"
    mapping = {
        "1:1": "1024x1024",
        "4:3": "1024x768",
        "3:4": "768x1024",
        "16:9": "1536x864",
        "9:16": "864x1536",
        "3:2": "1152x768",
        "2:3": "768x1152",
    }
    return mapping.get(value, "1024x1024")


def _coerce_image_edit_size(raw: Any) -> str:
    value = str(raw or "").strip().lower().replace(" ", "")
    if "x" in value:
        parts = value.split("x", 1)
        try:
            width = int(parts[0])
            height = int(parts[1])
        except (TypeError, ValueError):
            width = 0
            height = 0
        if width > 0 and height > 0:
            return f"{width}x{height}"
    mapping = {
        "1:1": "1024x1024",
        "4:3": "1440x1080",
        "3:4": "1080x1440",
        "16:9": "1920x1080",
        "9:16": "1080x1920",
        "3:2": "1440x960",
        "2:3": "960x1440",
    }
    return mapping.get(value, "1024x1024")


def _first_grok_reference(forwarded: Dict[str, Any]) -> str:
    primary, refs = _normalized_image_refs_from_payload(forwarded)
    if refs:
        return refs[0]
    return primary


def _is_http_url(value: str) -> bool:
    lower = str(value or "").strip().lower()
    return lower.startswith("http://") or lower.startswith("https://")


async def _download_reference_url_to_temp_file(url: str) -> Tuple[Path, str, str]:
    src = str(url or "").strip()
    if not _is_http_url(src):
        raise RuntimeError("reference image url must start with http:// or https://")
    tmp_path = ""
    total = 0
    media_type = "image/jpeg"
    suffix = ".jpg"
    temp_file = None
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True, trust_env=False) as client:
            async with client.stream("GET", src, headers={"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36"}) as resp:
                resp.raise_for_status()
                media_type = (resp.headers.get("content-type") or "image/jpeg").split(";", 1)[0].strip() or "image/jpeg"
                if not media_type.lower().startswith("image/"):
                    raise RuntimeError(f"reference url is not an image: {media_type}")
                suffix = _guess_image_ext(media_type, src)
                temp_file = await asyncio.to_thread(
                    tempfile.NamedTemporaryFile,
                    prefix="grok-reference-",
                    suffix=suffix,
                    delete=False,
                )
                tmp_path = temp_file.name
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _MAX_GROK_REFERENCE_BYTES:
                        raise RuntimeError("reference image exceeds max size")
                    await asyncio.to_thread(temp_file.write, chunk)
                await asyncio.to_thread(temp_file.close)
                temp_file = None
        if total <= 0:
            raise RuntimeError("reference image download is empty")
        return Path(tmp_path), f"reference{suffix}", media_type
    except Exception:
        if temp_file is not None:
            try:
                await asyncio.to_thread(temp_file.close)
            except Exception:
                pass
        if tmp_path:
            try:
                await asyncio.to_thread(Path(tmp_path).unlink, missing_ok=True)
            except Exception:
                pass
        raise


async def _reference_url_to_file_tuple(url: str) -> Tuple[str, bytes, str]:
    data, media_type, ext = await _download_image_bytes(url)
    filename = f"reference{ext or '.png'}"
    return filename, data, media_type


async def _build_image_edit_request_parts(
    body: Dict[str, Any],
    model: str,
    entry: Dict[str, Any],
    reference_urls: List[str],
) -> Tuple[Dict[str, str], List[Tuple[str, Tuple[Any, ...]]]]:
    forwarded = _body_for_upstream_model(body, model, entry)
    prompt = str(forwarded.get("prompt") or body.get("prompt") or "").strip()
    raw_image_size = (
        forwarded.get("size")
        or forwarded.get("image_size")
        or forwarded.get("aspect_ratio")
        or forwarded.get("ratio")
        or body.get("size")
        or body.get("image_size")
        or body.get("aspect_ratio")
        or body.get("ratio")
        or "1024x1024"
    )
    token_group = str(entry.get("token_group") or "").strip().lower()
    image_size = (
        _coerce_openai_official_image_size(raw_image_size)
        if token_group == "openai_official"
        else _coerce_image_edit_size(raw_image_size)
    )
    try:
        num_images = max(1, int(forwarded.get("num_images") or forwarded.get("n") or body.get("n") or 1))
    except (TypeError, ValueError):
        num_images = 1
    data: Dict[str, str] = {
        "model": _upstream_model(model, entry),
        "prompt": prompt,
        "size": image_size,
        "n": str(num_images),
    }
    response_format = str(forwarded.get("response_format") or body.get("response_format") or "").strip()
    if response_format:
        data["response_format"] = response_format
    files: List[Tuple[str, Tuple[Any, ...]]] = []
    for index, ref in enumerate(reference_urls):
        filename, raw, media_type = await _reference_url_to_file_tuple(ref)
        field_name = "image" if index == 0 else "image[]"
        files.append((field_name, (filename, raw, media_type)))
    if not files:
        raise RuntimeError("image edit request missing reference image")
    return data, files


async def _build_comfly_grok15_multipart(
    body: Dict[str, Any],
    model: str,
    entry: Dict[str, Any],
) -> Tuple[Dict[str, str], List[Tuple[str, Tuple[Any, ...]]], str, List[Any], List[Path]]:
    forwarded = dict(body or {})
    prompt = str(forwarded.get("prompt") or "").strip()
    duration = forwarded.get("duration") or forwarded.get("seconds") or 6
    # The dedicated ComfyUI video relay accepts the canonical model id. The
    # legacy Comfly Grok route still uses duration-specific model aliases.
    upstream_model = (
        _upstream_model(model, entry)
        if _is_comfyui_grok_api_format(entry)
        else _coerce_grok15_model(duration)
    )
    ratio = forwarded.get("ratio") or forwarded.get("aspect_ratio") or "9:16"
    data: Dict[str, str] = {
        "model": upstream_model,
        "prompt": prompt,
        "size": _coerce_video_size_from_ratio(ratio),
    }
    files: List[Tuple[str, Tuple[Any, ...]]] = []
    open_files: List[Any] = []
    temp_paths: List[Path] = []
    first_ref = _first_grok_reference(forwarded)
    if first_ref:
        path = Path(first_ref)
        if path.exists() and path.is_file():
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            handle = path.open("rb")
            open_files.append(handle)
            files.append(("input_reference", (path.name, handle, content_type)))
        elif _is_http_url(first_ref):
            temp_path, filename, content_type = await _download_reference_url_to_temp_file(first_ref)
            temp_paths.append(temp_path)
            handle = temp_path.open("rb")
            open_files.append(handle)
            files.append(("input_reference", (filename, handle, content_type)))
        elif str(first_ref).startswith("data:image/"):
            filename, raw, content_type = await _reference_url_to_file_tuple(first_ref)
            files.append(("input_reference", (filename, raw, content_type)))
        else:
            raise RuntimeError("Grok 1.5 video requires input_reference as a file, local path, data image, or http image URL")
    return data, files, upstream_model, open_files, temp_paths


async def _submit_comfly_grok15_video(
    body: Dict[str, Any],
    model: str,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    data, files, upstream_model, open_files, temp_paths = await _build_comfly_grok15_multipart(body, model, entry)
    try:
        resp = await _comfly_multipart_request(
            _comfly_url("/v1/videos", model),
            data,
            files,
            _comfly_auth_headers(model),
            _TIMEOUT_VIDEO_SUBMIT,
        )
    finally:
        for handle in open_files:
            try:
                handle.close()
            except Exception:
                pass
        for temp_path in temp_paths:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
    if isinstance(resp, dict):
        is_comfyui = _is_comfyui_grok_api_format(entry)
        resp.setdefault("_provider", "comfyui" if is_comfyui else "comfly")
        resp.setdefault("_api_format", "comfyui_grok_v1" if is_comfyui else "grok_v1")
        resp.setdefault("_requested_model", upstream_model)
    return resp


def _should_try_comfly_v1_poll_fallback(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    return "http 404" in msg or "http 400" in msg


async def _poll_comfly_video_task(task_id: str, model: str = "", api_kind: str = "") -> Dict[str, Any]:
    tid = (task_id or "").strip()
    if not tid:
        raise HTTPException(400, "missing task_id")
    kind = (api_kind or "").strip().lower()
    route_model = (model or "").strip()
    if kind in {"grok_v1", "comfyui_grok_v1"}:
        resp = await _comfly_request(
            "GET",
            _comfly_url(f"/v1/videos/{tid}", route_model or "grok-video-3"),
            None,
            _comfly_auth_headers(route_model or "grok-video-3"),
            _TIMEOUT_VIDEO_POLL,
        )
        if isinstance(resp, dict):
            resp.setdefault("_provider", "comfyui" if kind == "comfyui_grok_v1" else "comfly")
            resp.setdefault("_api_format", kind)
        return resp
    try:
        resp = await _comfly_request(
            "GET",
            _comfly_url(f"/v2/videos/generations/{tid}", route_model),
            None,
            _comfly_headers(route_model),
            _TIMEOUT_VIDEO_POLL,
        )
        if isinstance(resp, dict):
            resp.setdefault("_provider", "comfly")
            resp.setdefault("_api_format", "veo_v2")
        return resp
    except Exception as exc:
        if not _should_try_comfly_v1_poll_fallback(exc):
            raise
        resp = await _comfly_request(
            "GET",
            _comfly_url(f"/v1/videos/{tid}", route_model or "grok-video-3"),
            None,
            _comfly_auth_headers(route_model or "grok-video-3"),
            _TIMEOUT_VIDEO_POLL,
        )
        if isinstance(resp, dict):
            resp.setdefault("_provider", "comfly")
            resp.setdefault("_api_format", "grok_v1")
        _remember_proxy_video_task(tid, "grok_v1", route_model or "grok-video-3")
        return resp


_COMFLY_IMAGE_RATIO_ALIASES = {
    "portrait_9_16": "9:16",
    "landscape_16_9": "16:9",
    "square_hd": "1:1",
    "square": "1:1",
    "vertical": "9:16",
    "portrait": "9:16",
    "horizontal": "16:9",
    "landscape": "16:9",
}

_COMFLY_IMAGE_RATIO_VALUES = {"1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"}


def _coerce_comfly_image_ratio_size(*values: Any) -> str:
    for raw in values:
        value = str(raw or "").strip().lower().replace(" ", "")
        if not value:
            continue
        value = _COMFLY_IMAGE_RATIO_ALIASES.get(value, value)
        if value in _COMFLY_IMAGE_RATIO_VALUES:
            return value
        if "x" in value:
            parts = value.split("x", 1)
            try:
                width = int(parts[0])
                height = int(parts[1])
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue
            known = {
                "1:1": (1, 1),
                "4:3": (4, 3),
                "3:4": (3, 4),
                "16:9": (16, 9),
                "9:16": (9, 16),
                "3:2": (3, 2),
                "2:3": (2, 3),
            }
            ratio = width / height
            return min(known, key=lambda key: abs(ratio - (known[key][0] / known[key][1])))
    return "1:1"


def _body_for_upstream_model(body: Dict[str, Any], model: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    upstream = _upstream_model(model, entry)
    forwarded = dict(body)
    forwarded["model"] = upstream
    api_format = str(entry.get("api_format") or "").strip().lower()
    model_low = str(upstream or model or "").strip().lower()
    if api_format == "dalle" and "nano-banana" in model_low:
        prompt = str(forwarded.get("prompt") or "").strip()
        ratio = _coerce_comfly_image_ratio_size(
            forwarded.get("aspect_ratio"),
            forwarded.get("ratio"),
            forwarded.get("size"),
            forwarded.get("image_size"),
        )
        try:
            num_images = max(1, int(forwarded.get("num_images") or forwarded.get("n") or 1))
        except (TypeError, ValueError):
            num_images = 1
        image_url, image_urls = _normalized_image_refs_from_payload(forwarded)
        out: Dict[str, Any] = {
            "model": upstream,
            "prompt": prompt,
            "image_size": ratio,
            "aspect_ratio": ratio,
            "num_images": num_images,
            "n": num_images,
            "response_format": str(forwarded.get("response_format") or "url"),
        }
        if image_url:
            out["image_url"] = image_url
            out["image"] = image_url
        if image_urls:
            out["image_urls"] = image_urls
        return out
    if api_format == "dalle" and (
        "gpt-image-2" in model_low
        or "gpt-image2" in model_low
        or "gptimage2" in model_low
        or _is_openai_official_image_model(model_low)
    ):
        prompt = str(forwarded.get("prompt") or "").strip()
        ratio = _coerce_comfly_image_ratio_size(
            forwarded.get("aspect_ratio"),
            forwarded.get("ratio"),
            forwarded.get("size"),
            forwarded.get("image_size"),
        )
        try:
            num_images = max(1, int(forwarded.get("num_images") or forwarded.get("n") or 1))
        except (TypeError, ValueError):
            num_images = 1
        image_url, image_urls = _normalized_image_refs_from_payload(forwarded)
        out: Dict[str, Any] = {
            "model": upstream,
            "prompt": prompt,
            "size": ratio,
            "num_images": num_images,
            "n": num_images,
            "response_format": str(forwarded.get("response_format") or "url"),
        }
        if image_url:
            out["image_url"] = image_url
            out["image"] = image_url
        if image_urls:
            out["image_urls"] = image_urls
        return out
    if api_format == "grok":
        prompt = str(forwarded.get("prompt") or "").strip()
        grok_body: Dict[str, Any] = {"model": upstream, "prompt": prompt}
        _primary_image, images = _normalized_image_refs_from_payload(forwarded)
        if images:
            grok_body["images"] = images[:1]
        if "ratio" not in forwarded and forwarded.get("aspect_ratio"):
            forwarded["ratio"] = forwarded.get("aspect_ratio")
        grok_body["ratio"] = str(forwarded.get("ratio") or "9:16")
        grok_body["resolution"] = _coerce_grok_video_resolution(forwarded.get("resolution"))
        try:
            duration = int(forwarded.get("duration") or forwarded.get("seconds") or 6)
        except (TypeError, ValueError):
            duration = 6
        grok_body["duration"] = 10 if duration >= 10 else 6
        return grok_body
    return forwarded


def _image_reference_urls(body: Dict[str, Any]) -> List[str]:
    _primary, refs = _normalized_image_refs_from_payload(body)
    return refs


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_CAPABILITY_FOR_BILLING = "comfly.daihuo.pipeline"


@router.post("/api/comfly-proxy/v1/files", summary="Comfly files upload transparent proxy")
async def proxy_files_upload(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    form = await request.form()
    data: Dict[str, str] = {}
    files: List[Tuple[str, Tuple[Any, ...]]] = []
    total_file_bytes = 0
    for key, value in form.multi_items():
        if hasattr(value, "filename"):
            size = await asyncio.to_thread(_seekable_upload_size, value.file)
            if size <= 0:
                continue
            total_file_bytes += size
            if total_file_bytes > _MAX_PROXY_FILE_BYTES:
                raise HTTPException(status_code=413, detail="上传文件总量不能超过 512MB")
            files.append(
                (
                    key,
                    (
                        value.filename or "file",
                        value.file,
                        (getattr(value, "content_type", None) or "application/octet-stream"),
                    ),
                )
            )
        else:
            data[key] = str(value)
    if not files:
        raise HTTPException(400, "缺少 file 文件")

    try:
        resp = await _comfly_multipart_request(
            _comfly_url("/v1/files"),
            data,
            files,
            _comfly_auth_headers(),
            _TIMEOUT_FILE_UPLOAD,
        )
    except Exception as e:
        _audit("file_upload_failed", user_id=current_user.id, error=str(e)[:300])
        raise HTTPException(502, f"Comfly files 上传失败：{e}")

    _audit("file_upload_ok", user_id=current_user.id, file_count=len(files))
    return JSONResponse(resp)


@router.post("/api/comfly-proxy/v1/chat/completions", summary="Comfly chat 透明 proxy（按 token usage 计费）")
async def proxy_chat_completions(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "缺少 model")
    entry = _require_model_entry(model)
    upstream_body = _body_for_upstream_model(body, model, entry)
    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=False)
    try:
        upstream_body, prepared_image_count = await _prepare_chat_image_urls_for_upstream(upstream_body)
    except Exception as e:
        _audit(
            "chat_image_prepare_failed",
            user_id=billing_user_id,
            request_user_id=request_user_id,
            model=model,
            error=str(e)[:300],
        )
        raise HTTPException(502, f"Comfly chat 图片准备失败：{e}")

    # 预扣（按典型 token 估算）
    estimated = estimate_comfly_credits(model, {}, for_user=True) or 1
    pre = _do_pre_deduct_by_user_id(
        billing_user_id,
        estimated,
        capability_id=_CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="chat",
        extra_meta={"prepared_image_count": prepared_image_count},
    )
    _audit(
        "chat_pre_deduct",
        user_id=billing_user_id,
        request_user_id=request_user_id,
        model=model,
        estimated=estimated,
        prepared_image_count=prepared_image_count,
    )

    try:
        resp = await _comfly_request("POST", _comfly_url("/v1/chat/completions", model),
                                     upstream_body, _comfly_headers(model), _TIMEOUT_CHAT)
    except Exception as e:
        _do_full_refund_by_user_id(billing_user_id, pre=pre,
                        capability_id=_CAPABILITY_FOR_BILLING, model=model, endpoint="chat", error=str(e))
        _audit("chat_failed", user_id=billing_user_id, request_user_id=request_user_id, model=model, error=str(e)[:300])
        raise HTTPException(502, f"Comfly chat 调用失败：{e}")

    # 按 usage 结算
    usage = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
    actual = estimate_comfly_credits(model, {"usage": usage}, for_user=True) or estimated
    _do_settle_by_user_id(billing_user_id, pre=pre, actual=int(actual),
               capability_id=_CAPABILITY_FOR_BILLING, model=model, endpoint="chat",
               extra_meta={"usage": usage, "prepared_image_count": prepared_image_count})
    _audit("chat_settled", user_id=billing_user_id, request_user_id=request_user_id, model=model,
           pre=credits_json_float(pre), actual=int(actual), usage=usage, prepared_image_count=prepared_image_count)
    return JSONResponse(resp)


async def _execute_image_generation_request(
    *,
    request_user_id: int,
    billing_user_id: int,
    model: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    openai_official_first = _openai_official_image_first_for_user(billing_user_id)
    attempt_models = _image_generation_model_attempts_for_user(model, openai_official_first=openai_official_first)
    if len(attempt_models) == 1:
        _require_model_entry(model)
    errors: List[str] = []
    last_error = ""
    attempts_per_model = _env_int("COMFLY_IMAGE_RETRY_ATTEMPTS", 2, min_value=1, max_value=4)
    reference_urls = _image_reference_urls(body)

    for index, attempt_model in enumerate(attempt_models, start=1):
        try:
            entry = _require_model_entry(attempt_model)
        except HTTPException as e:
            last_error = str(e.detail)
            errors.append(f"{attempt_model}: {last_error}")
            _audit(
                "image_channel_skipped",
                user_id=billing_user_id,
                request_user_id=request_user_id,
                requested_model=model,
                model=attempt_model,
                attempt=index,
                error=last_error[:300],
            )
            continue

        upstream_body = _body_for_upstream_model(body, attempt_model, entry)
        provider_label = _image_generation_provider_label(entry, attempt_model)
        if not _image_generation_channel_available(provider_label, attempt_model):
            last_error = f"{provider_label} temporarily disabled after recent upstream failures"
            errors.append(f"{attempt_model}: {last_error}")
            _audit(
                "image_channel_circuit_skipped",
                user_id=billing_user_id,
                request_user_id=request_user_id,
                requested_model=model,
                model=attempt_model,
                provider=provider_label,
                attempt=index,
                error=last_error[:300],
            )
            continue
        if reference_urls:
            upstream_body.setdefault("image", reference_urls[0])
            upstream_body.setdefault("image_url", reference_urls[0])
            upstream_body.setdefault("image_urls", reference_urls)
        logger.info(
            "[image_generate] request model=%s attempt_model=%s upstream_model=%s refs=%d first_ref=%s image_size=%s num_images=%s",
            model,
            attempt_model,
            upstream_body.get("model"),
            len(reference_urls),
            (reference_urls[0][:120] if reference_urls else ""),
            upstream_body.get("image_size") or upstream_body.get("size") or upstream_body.get("aspect_ratio"),
            upstream_body.get("num_images") or upstream_body.get("n"),
        )

        estimated = estimate_comfly_credits(attempt_model, body, for_user=True) or 1
        pre = _do_pre_deduct_by_user_id(
            billing_user_id,
            estimated,
            capability_id=_CAPABILITY_FOR_BILLING,
            model=attempt_model,
            endpoint="image",
            extra_meta={"requested_model": model, "attempt": index},
        )
        _audit(
            "image_pre_deduct",
            user_id=billing_user_id,
            request_user_id=request_user_id,
            requested_model=model,
            model=attempt_model,
            attempt=index,
            estimated=estimated,
        )

        channel_succeeded = False
        for retry_index in range(1, attempts_per_model + 1):
            try:
                endpoint_path = "/v1/images/edits" if reference_urls else "/v1/images/generations"
                _audit(
                    "image_channel_attempt",
                    user_id=billing_user_id,
                    request_user_id=request_user_id,
                    requested_model=model,
                    model=attempt_model,
                    attempt=index,
                    retry=retry_index,
                    token_group=(entry.get("token_group") or ""),
                    refs=len(reference_urls),
                )
                token_group = str(entry.get("token_group") or "").strip().lower()
                if token_group == "openai_official":
                    if reference_urls:
                        edit_data, edit_files = await _build_image_edit_request_parts(body, attempt_model, entry, reference_urls)
                        resp = await _submit_image_edit_attempt(
                            attempt_model=attempt_model,
                            entry=entry,
                            data=edit_data,
                            files=edit_files,
                        )
                    else:
                        resp = await _openai_official_image_request(upstream_body)
                elif reference_urls:
                    if token_group == "openmindapi":
                        resp = await _openmind_image_request(upstream_body)
                    else:
                        edit_data, edit_files = await _build_image_edit_request_parts(body, attempt_model, entry, reference_urls)
                        resp = await _submit_image_edit_attempt(
                            attempt_model=attempt_model,
                            entry=entry,
                            data=edit_data,
                            files=edit_files,
                        )
                elif token_group == "sutui":
                    resp = await _sutui_image_request(upstream_body)
                else:
                    resp = await _comfly_request(
                        "POST",
                        _comfly_url(endpoint_path, attempt_model),
                        upstream_body,
                        _comfly_headers(attempt_model),
                        _TIMEOUT_IMAGE,
                    )
                asset_persistence_queued = _queue_generated_image_asset_persistence(
                    billing_user_id,
                    response_payload=resp,
                    prompt=str(body.get("prompt") or ""),
                    model=attempt_model,
                    limit=int(body.get("n") or body.get("num_images") or 1),
                    exclude_urls=reference_urls,
                ) if isinstance(resp, dict) else False
                if isinstance(resp, dict) and attempt_model != model:
                    resp = dict(resp)
                    fallback = resp.setdefault("_lobster_fallback", {})
                    if isinstance(fallback, dict):
                        fallback.update({"requested_model": model, "used_model": attempt_model, "attempt": index})
                _audit(
                    "image_ok",
                    user_id=billing_user_id,
                    request_user_id=request_user_id,
                    requested_model=model,
                    model=attempt_model,
                    attempt=index,
                    retry=retry_index,
                    pre=credits_json_float(pre),
                    asset_persistence_queued=asset_persistence_queued,
                    refs=len(reference_urls),
                )
                log_model_usage_event(
                    None,
                    category="image",
                    event_kind="attempt",
                    success=True,
                    user_id=billing_user_id,
                    requested_model=model,
                    model=attempt_model,
                    provider=(entry.get("token_group") or "comfly"),
                    channel=(entry.get("token_group") or "comfly"),
                    route=_provider_route_label(token_group),
                    endpoint=endpoint_path,
                    meta={"attempt": index, "retry": retry_index, "asset_persistence_queued": asset_persistence_queued, "refs": len(reference_urls)},
                )
                log_model_usage_event(
                    None,
                    category="image",
                    event_kind="request",
                    success=True,
                    user_id=billing_user_id,
                    requested_model=model,
                    model=attempt_model,
                    provider=(entry.get("token_group") or "comfly"),
                    channel=(entry.get("token_group") or "comfly"),
                    route=_provider_route_label(token_group),
                    endpoint=endpoint_path,
                    meta={"attempt": index, "retry": retry_index, "asset_persistence_queued": asset_persistence_queued, "refs": len(reference_urls)},
                )
                channel_succeeded = True
                return resp
            except Exception as e:
                last_error = str(e)
                _mark_image_generation_channel_failure(provider_label, attempt_model, last_error)
                errors.append(f"{attempt_model}: {last_error[:300]}")
                _audit(
                    "image_channel_attempt_failed",
                    user_id=billing_user_id,
                    request_user_id=request_user_id,
                    requested_model=model,
                    model=attempt_model,
                    attempt=index,
                    retry=retry_index,
                    retries=attempts_per_model,
                    error=last_error[:300],
                )
                log_model_usage_event(
                    None,
                    category="image",
                    event_kind="attempt",
                    success=False,
                    user_id=billing_user_id,
                    requested_model=model,
                    model=attempt_model,
                    provider=(entry.get("token_group") or "comfly"),
                    channel=(entry.get("token_group") or "comfly"),
                    route=_provider_route_label(token_group),
                    endpoint=endpoint_path,
                    error_message=last_error[:1000],
                    meta={"attempt": index, "retry": retry_index, "retries": attempts_per_model, "refs": len(reference_urls)},
                )
                if retry_index >= attempts_per_model or not _is_retryable_image_error(e):
                    break
                await asyncio.sleep(0.8 * retry_index)

        openmind_fallback_provider = "openmind"
        if (
            _openmind_image_fallback_enabled()
            and _image_generation_channel_available(openmind_fallback_provider, attempt_model)
            and (not last_error or _is_retryable_image_error(RuntimeError(last_error)))
        ):
            try:
                resp = await _openmind_image_request(upstream_body)
                asset_persistence_queued = _queue_generated_image_asset_persistence(
                    billing_user_id,
                    response_payload=resp,
                    prompt=str(body.get("prompt") or ""),
                    model=attempt_model,
                    limit=int(body.get("n") or body.get("num_images") or 1),
                    exclude_urls=reference_urls,
                ) if isinstance(resp, dict) else False
                if isinstance(resp, dict):
                    resp = dict(resp)
                    fallback = resp.setdefault("_lobster_fallback", {})
                    if isinstance(fallback, dict):
                        fallback.update({"requested_model": model, "used_model": attempt_model, "provider": "openmind", "attempt": index})
                _audit(
                    "image_openmind_fallback_ok",
                    user_id=billing_user_id,
                    request_user_id=request_user_id,
                    requested_model=model,
                    model=attempt_model,
                    pre=credits_json_float(pre),
                    comfly_error=last_error[:300],
                    asset_persistence_queued=asset_persistence_queued,
                )
                log_model_usage_event(
                    None,
                    category="image",
                    event_kind="attempt",
                    success=True,
                    user_id=billing_user_id,
                    requested_model=model,
                    model=attempt_model,
                    provider="openmind",
                    channel="openmind",
                    route="openmind",
                    endpoint="/openmind/images",
                    meta={"attempt": index, "asset_persistence_queued": asset_persistence_queued, "refs": len(reference_urls)},
                )
                log_model_usage_event(
                    None,
                    category="image",
                    event_kind="request",
                    success=True,
                    user_id=billing_user_id,
                    requested_model=model,
                    model=attempt_model,
                    provider="openmind",
                    channel="openmind",
                    route="openmind",
                    endpoint="/openmind/images",
                    meta={"attempt": index, "asset_persistence_queued": asset_persistence_queued, "refs": len(reference_urls)},
                )
                channel_succeeded = True
                return resp
            except Exception as fallback_error:
                _mark_image_generation_channel_failure(openmind_fallback_provider, attempt_model, str(fallback_error))
                _audit(
                    "image_openmind_fallback_failed",
                    user_id=billing_user_id,
                    request_user_id=request_user_id,
                    requested_model=model,
                    model=attempt_model,
                    comfly_error=last_error[:300],
                    error=str(fallback_error)[:300],
                )
                log_model_usage_event(
                    None,
                    category="image",
                    event_kind="attempt",
                    success=False,
                    user_id=billing_user_id,
                    requested_model=model,
                    model=attempt_model,
                    provider="openmind",
                    channel="openmind",
                    route="openmind",
                    endpoint="/openmind/images",
                    error_message=str(fallback_error)[:1000],
                    meta={"attempt": index, "refs": len(reference_urls)},
                )
                last_error = f"{last_error}; OpenMind fallback failed: {fallback_error}"
                errors.append(f"{attempt_model}/openmind: {str(fallback_error)[:300]}")
        elif _openmind_image_fallback_enabled() and not _image_generation_channel_available(openmind_fallback_provider, attempt_model):
            fallback_skip_error = "openmind temporarily disabled after recent upstream failures"
            errors.append(f"{attempt_model}/openmind: {fallback_skip_error}")
            _audit(
                "image_openmind_fallback_circuit_skipped",
                user_id=billing_user_id,
                request_user_id=request_user_id,
                requested_model=model,
                model=attempt_model,
                error=fallback_skip_error,
            )

        if not channel_succeeded:
            _do_full_refund_by_user_id(
                billing_user_id,
                pre=pre,
                capability_id=_CAPABILITY_FOR_BILLING,
                model=attempt_model,
                endpoint="image",
                error=last_error,
            )
            _audit(
                "image_failed",
                user_id=billing_user_id,
                request_user_id=request_user_id,
                requested_model=model,
                model=attempt_model,
                attempt=index,
                error=last_error[:300],
            )
            log_model_usage_event(
                None,
                category="image",
                event_kind="request",
                success=False,
                user_id=billing_user_id,
                requested_model=model,
                model=attempt_model,
                provider="all",
                channel="all",
                route="final",
                endpoint="/v1/images/generations",
                error_message=last_error[:1000],
                meta={"attempt": index, "refs": len(reference_urls)},
            )

    detail = "; ".join(errors[-3:]) or last_error or "unknown error"
    _audit(
        "image_all_channels_failed",
        user_id=billing_user_id,
        request_user_id=request_user_id,
        model=model,
        errors=errors[-5:],
    )
    log_model_usage_event(
        None,
        category="image",
        event_kind="request",
        success=False,
        user_id=billing_user_id,
        requested_model=model,
        model=model,
        provider="all",
        channel="all",
        route="final",
        endpoint="/v1/images/generations",
        error_message=detail[:1000],
        meta={"errors": errors[-5:]},
    )
    raise HTTPException(502, _public_image_failure_detail())


async def _run_image_generation_proxy_job(
    *,
    job_id: str,
    request_user_id: int,
    billing_user_id: int,
    model: str,
    body: Dict[str, Any],
) -> None:
    _update_image_proxy_job(job_id, status="running", stage="generating")
    try:
        async with background_heavy_slot("image_proxy_generation"):
            result = await _execute_image_generation_request(
                request_user_id=request_user_id,
                billing_user_id=billing_user_id,
                model=model,
                body=body,
            )
        _update_image_proxy_job(
            job_id,
            status="completed",
            stage="completed",
            result=_compact_image_proxy_result(result),
            error=None,
        )
    except Exception as exc:
        logger.warning("[image_generate] async proxy job failed job_id=%s err=%s", job_id, exc)
        _update_image_proxy_job(job_id, status="failed", stage="failed", error=_job_error_detail(exc)[:2000])


@router.post("/api/comfly-proxy/v1/images/generations", summary="Comfly images transparent proxy")
async def proxy_images_generations(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "missing model")
    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=True)
    resp = await _execute_image_generation_request(
        request_user_id=request_user_id,
        billing_user_id=billing_user_id,
        model=model,
        body=body,
    )
    return JSONResponse(resp)


@router.post("/api/comfly-proxy/v1/images/generations/start", summary="Start async image generation proxy job")
async def proxy_images_generations_start(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "missing model")
    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=True)
    job_id = _start_image_proxy_job(
        kind="generation",
        request_user_id=request_user_id,
        billing_user_id=billing_user_id,
        requested_model=model,
    )
    task_coro = _run_image_generation_proxy_job(
        job_id=job_id,
        request_user_id=request_user_id,
        billing_user_id=billing_user_id,
        model=model,
        body=body,
    )
    try:
        spawn_tracked_task(task_coro, name=f"image-proxy-generation-{job_id}")
    except Exception:
        task_coro.close()
        _update_image_proxy_job(job_id, status="failed", stage="failed", error="image job queue unavailable")
        raise HTTPException(503, "image job queue unavailable")
    return {
        "ok": True,
        "async": True,
        "job_id": job_id,
        "status": "queued",
        "poll_path": f"/api/comfly-proxy/v1/images/jobs/{job_id}",
    }


@router.get("/api/comfly-proxy/v1/images/jobs/{job_id}", summary="Get async image proxy job status")
async def proxy_images_job_status(
    job_id: str,
    request: Request,
):
    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=True)
    job = _load_image_proxy_job(job_id)
    if not job:
        raise HTTPException(404, "image job not found or expired")
    if int(job.get("request_user_id") or -1) != int(request_user_id) and int(job.get("billing_user_id") or -1) != int(billing_user_id):
        raise HTTPException(403, "forbidden")
    return JSONResponse(_image_proxy_job_public_payload(job))


async def _parse_image_edit_form_payload(request: Request) -> Tuple[str, Dict[str, str], List[Tuple[str, str, bytes, str]], str]:
    form = await request.form()
    model = str(form.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "缺少 model")
    data: Dict[str, str] = {}
    files: List[Tuple[str, Tuple[Any, ...]]] = []
    total_file_bytes = 0
    for key, value in form.multi_items():
        if hasattr(value, "filename"):
            size = await asyncio.to_thread(_seekable_upload_size, value.file)
            if size <= 0:
                continue
            if size > _MAX_GROK_REFERENCE_BYTES:
                raise HTTPException(status_code=413, detail="单张参考图不能超过 30MB")
            total_file_bytes += size
            if total_file_bytes > _MAX_IMAGE_EDIT_TOTAL_BYTES:
                raise HTTPException(status_code=413, detail="参考图总量不能超过 120MB")
            files.append(
                (
                    key,
                    (
                        value.filename or "image.png",
                        value.file,
                        (getattr(value, "content_type", None) or "application/octet-stream"),
                    ),
                )
            )
        else:
            data[key] = str(value)

    entry = _require_model_entry(model)
    data["model"] = _upstream_model(model, entry)
    data.setdefault("response_format", "url")
    if not files:
        raise HTTPException(400, "缺少 image 文件")
    buffered_files = await _buffer_image_edit_uploads(files)
    if not buffered_files:
        raise HTTPException(400, "缺少 image 文件")

    client_request_id = (
        data.pop("client_request_id", None)
        or data.pop("job_id", None)
        or request.headers.get("X-Client-Request-Id")
        or request.headers.get("X-Idempotency-Key")
        or ""
    )
    return model, data, buffered_files, str(client_request_id or "")


async def _execute_image_edit_request(
    *,
    request_user_id: int,
    billing_user_id: int,
    model: str,
    data: Dict[str, str],
    buffered_files: List[Tuple[str, str, bytes, str]],
    client_request_id: str = "",
) -> Dict[str, Any]:
    entry = _require_model_entry(model)
    openai_official_first = _openai_official_image_first_for_user(billing_user_id)
    attempt_models = _image_edit_model_attempts_for_user(model, openai_official_first=openai_official_first)
    idempotency_key = _image_edit_idempotency_key(billing_user_id, client_request_id)
    if not idempotency_key:
        idempotency_key = _image_edit_buffer_fingerprint_key(billing_user_id, data, buffered_files)
    cached_payload = _cached_image_edit_response(idempotency_key)
    if cached_payload is not None:
        _audit(
            "image_edit_cached",
            user_id=billing_user_id,
            request_user_id=request_user_id,
            model=model,
            client_request_id=str(client_request_id or "")[:120],
        )
        return cached_payload
    pending_key = f"{idempotency_key}:pending" if idempotency_key else ""
    if pending_key:
        claimed = cache_set_if_absent(pending_key, "1", ttl_seconds=max(60, int(_TIMEOUT_IMAGE) + 120))
        if not claimed:
            try:
                wait_seconds = max(0.0, min(120.0, float(os.environ.get("COMFLY_IMAGE_EDIT_PENDING_WAIT_SECONDS") or "75")))
            except (TypeError, ValueError):
                wait_seconds = 75.0
            cached_payload = await _wait_cached_image_edit_response(idempotency_key, max_seconds=wait_seconds)
            if cached_payload is not None:
                _audit(
                    "image_edit_cached_after_wait",
                    user_id=billing_user_id,
                    request_user_id=request_user_id,
                    model=model,
                    client_request_id=str(client_request_id or "")[:120],
                )
                return cached_payload
            _audit(
                "image_edit_pending_duplicate",
                user_id=billing_user_id,
                request_user_id=request_user_id,
                model=model,
                client_request_id=str(client_request_id or "")[:120],
            )
            raise HTTPException(status_code=409, detail="同一个图片任务仍在生成中，请稍后刷新结果，不会重复扣费")
    estimated = estimate_comfly_credits(model, data, for_user=True) or 1
    pre = _do_pre_deduct_by_user_id(
        billing_user_id,
        estimated,
        capability_id=_CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="image_edit",
    )
    _audit(
        "image_edit_pre_deduct",
        user_id=billing_user_id,
        request_user_id=request_user_id,
        model=model,
        estimated=estimated,
    )

    attempts = _env_int("COMFLY_IMAGE_EDIT_RETRY_ATTEMPTS", 3, min_value=1, max_value=4)
    errors: List[str] = []
    last_error = ""
    resp: Dict[str, Any] | None = None
    used_model = model
    used_entry = entry
    requested_data = dict(data)
    for attempt_index, attempt_model in enumerate(attempt_models, start=1):
        try:
            attempt_entry = _require_model_entry(attempt_model)
        except HTTPException as e:
            last_error = str(e.detail)
            errors.append(f"{attempt_model}: {last_error}")
            _audit(
                "image_edit_channel_skipped",
                user_id=billing_user_id,
                request_user_id=request_user_id,
                requested_model=model,
                model=attempt_model,
                attempt=attempt_index,
                error=last_error[:300],
            )
            continue

        provider_label = _image_generation_provider_label(attempt_entry, attempt_model)
        if not _image_generation_channel_available(provider_label, attempt_model):
            last_error = f"{provider_label} temporarily disabled after recent upstream failures"
            errors.append(f"{attempt_model}: {last_error}")
            _audit(
                "image_edit_channel_circuit_skipped",
                user_id=billing_user_id,
                request_user_id=request_user_id,
                requested_model=model,
                model=attempt_model,
                provider=provider_label,
                attempt=attempt_index,
                error=last_error[:300],
            )
            continue

        attempt_data = _image_edit_data_for_attempt(
            requested_data,
            requested_model=model,
            attempt_model=attempt_model,
            entry=attempt_entry,
        )
        for retry_index in range(1, attempts + 1):
            try:
                _audit(
                    "image_edit_attempt",
                    user_id=billing_user_id,
                    request_user_id=request_user_id,
                    requested_model=model,
                    model=attempt_model,
                    attempt=attempt_index,
                    retry=retry_index,
                    retries=attempts,
                    token_group=(attempt_entry.get("token_group") or ""),
                )
                attempt_files = _image_edit_files_from_buffer(buffered_files)
                resp = await _submit_image_edit_attempt(
                    attempt_model=attempt_model,
                    entry=attempt_entry,
                    data=attempt_data,
                    files=attempt_files,
                )
                used_model = attempt_model
                used_entry = attempt_entry
                break
            except Exception as e:
                last_error = str(e)
                _mark_image_generation_channel_failure(provider_label, attempt_model, last_error)
                errors.append(f"{attempt_model}: {last_error[:300]}")
                _audit(
                    "image_edit_attempt_failed",
                    user_id=billing_user_id,
                    request_user_id=request_user_id,
                    model=attempt_model,
                    retry=retry_index,
                    retries=attempts,
                    error=last_error[:300],
                )
                _audit(
                    "image_edit_channel_attempt_failed",
                    user_id=billing_user_id,
                    request_user_id=request_user_id,
                    requested_model=model,
                    model=attempt_model,
                    attempt=attempt_index,
                    retry=retry_index,
                    retries=attempts,
                    provider=provider_label,
                    error=last_error[:300],
                )
                if retry_index >= attempts or not _is_retryable_image_error(e):
                    break
                await asyncio.sleep(0.8 * retry_index)
        if resp is not None:
            break

    if resp is None:
        if pending_key:
            cache_delete(pending_key)
        _do_full_refund_by_user_id(billing_user_id, pre=pre,
                        capability_id=_CAPABILITY_FOR_BILLING, model=model, endpoint="image_edit", error=last_error)
        _audit(
            "image_edit_failed",
            user_id=billing_user_id,
            request_user_id=request_user_id,
            model=model,
            error=last_error[:300],
            errors=errors[-5:],
        )
        raise HTTPException(502, _image_edit_failure_detail(last_error))

    result_urls = _extract_image_result_urls(resp)
    if not result_urls:
        if pending_key:
            cache_delete(pending_key)
        error = "Comfly image edits returned no usable image URL"
        _do_full_refund_by_user_id(
            billing_user_id,
            pre=pre,
            capability_id=_CAPABILITY_FOR_BILLING,
            model=model,
            endpoint="image_edit",
            error=error,
        )
        _audit(
            "image_edit_empty_result",
            user_id=billing_user_id,
            request_user_id=request_user_id,
            model=used_model,
            requested_model=model,
            response_keys=list(resp.keys())[:12] if isinstance(resp, dict) else [],
        )
        raise HTTPException(502, "图片生成成功但没有返回可用图片结果，已自动退款，请稍后重试或切换模型。")

    actual = estimate_comfly_credits(used_model, requested_data, for_user=True) or estimated
    if quantize_credits(int(actual)) != pre:
        _do_settle_by_user_id(
            billing_user_id,
            pre=pre,
            actual=int(actual),
            capability_id=_CAPABILITY_FOR_BILLING,
            model=used_model,
            endpoint="image_edit",
            extra_meta={"requested_model": model, "fallback_used": used_model != model},
        )
    asset_persistence_queued = _queue_generated_image_asset_persistence(
        billing_user_id,
        response_payload=resp,
        prompt=str(requested_data.get("prompt") or ""),
        model=used_model,
        limit=int(requested_data.get("n") or requested_data.get("num_images") or 1),
    ) if isinstance(resp, dict) else False
    if isinstance(resp, dict) and used_model != model:
        resp = dict(resp)
        fallback = resp.setdefault("_lobster_fallback", {})
        if isinstance(fallback, dict):
            fallback.update({"requested_model": model, "used_model": used_model, "provider": _image_generation_provider_label(used_entry, used_model)})

    if idempotency_key:
        cache_set(idempotency_key, json.dumps(resp, ensure_ascii=False, default=str), ttl_seconds=86400)
        if pending_key:
            cache_delete(pending_key)
    _audit(
        "image_edit_ok",
        user_id=billing_user_id,
        request_user_id=request_user_id,
        requested_model=model,
        model=used_model,
        pre=credits_json_float(pre),
        actual=actual,
        result_count=len(result_urls),
        client_request_id=str(client_request_id or "")[:120],
        asset_persistence_queued=asset_persistence_queued,
    )
    return resp


async def _run_image_edit_proxy_job(
    *,
    job_id: str,
    request_user_id: int,
    billing_user_id: int,
    model: str,
    data: Dict[str, str],
    buffered_files: List[Tuple[str, str, bytes, str]],
    client_request_id: str,
) -> None:
    _update_image_proxy_job(job_id, status="running", stage="editing")
    try:
        async with background_heavy_slot("image_proxy_edit"):
            result = await _execute_image_edit_request(
                request_user_id=request_user_id,
                billing_user_id=billing_user_id,
                model=model,
                data=data,
                buffered_files=buffered_files,
                client_request_id=client_request_id,
            )
        _update_image_proxy_job(
            job_id,
            status="completed",
            stage="completed",
            result=_compact_image_proxy_result(result),
            error=None,
        )
    except Exception as exc:
        logger.warning("[image_edit] async proxy job failed job_id=%s err=%s", job_id, exc)
        _update_image_proxy_job(job_id, status="failed", stage="failed", error=_job_error_detail(exc)[:2000])


@router.post("/api/comfly-proxy/v1/images/edits", summary="Comfly image edits transparent proxy")
async def proxy_images_edits(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    model, data, buffered_files, client_request_id = await _parse_image_edit_form_payload(request)
    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=True)
    resp = await _execute_image_edit_request(
        request_user_id=request_user_id,
        billing_user_id=billing_user_id,
        model=model,
        data=data,
        buffered_files=buffered_files,
        client_request_id=client_request_id,
    )
    return JSONResponse(resp)


@router.post("/api/comfly-proxy/v1/images/edits/start", summary="Start async image edit proxy job")
async def proxy_images_edits_start(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    model, data, buffered_files, client_request_id = await _parse_image_edit_form_payload(request)
    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=True)
    job_id = _start_image_proxy_job(
        kind="edit",
        request_user_id=request_user_id,
        billing_user_id=billing_user_id,
        requested_model=model,
    )
    task_coro = _run_image_edit_proxy_job(
        job_id=job_id,
        request_user_id=request_user_id,
        billing_user_id=billing_user_id,
        model=model,
        data=data,
        buffered_files=buffered_files,
        client_request_id=client_request_id,
    )
    try:
        spawn_tracked_task(task_coro, name=f"image-proxy-edit-{job_id}")
    except Exception:
        task_coro.close()
        _update_image_proxy_job(job_id, status="failed", stage="failed", error="image edit job queue unavailable")
        raise HTTPException(503, "image edit job queue unavailable")
    return {
        "ok": True,
        "async": True,
        "job_id": job_id,
        "status": "queued",
        "poll_path": f"/api/comfly-proxy/v1/images/jobs/{job_id}",
    }


@router.post("/api/comfly-proxy/v2/videos/generations", summary="Comfly Veo 视频提交 proxy（按 per_call 预扣）")
async def proxy_videos_generations_submit(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "缺少 model")
    entry = _require_model_entry(model)
    upstream_body = _body_for_upstream_model(body, model, entry)

    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=False)
    estimated = estimate_comfly_credits(model, body, for_user=True) or 1
    internal_fallback = _is_trusted_internal_video_fallback(request)
    pre = Decimal("0") if internal_fallback else _do_pre_deduct_by_user_id(
        billing_user_id,
        estimated,
        capability_id=_CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="video_submit",
    )
    _audit(
        "video_submit_pre_deduct",
        user_id=billing_user_id,
        request_user_id=request_user_id,
        model=model,
        estimated=estimated,
        billing_reused=internal_fallback,
    )

    try:
        if _is_grok_api_format(entry) or _is_comfyui_grok_api_format(entry):
            resp = await _submit_comfly_grok15_video(body, model, entry)
        else:
            resp = await _comfly_request("POST", _comfly_url("/v2/videos/generations", model),
                                         upstream_body, _comfly_headers(model), _TIMEOUT_VIDEO_SUBMIT)
    except Exception as e:
        _do_full_refund_by_user_id(
            billing_user_id,
            pre=pre,
            capability_id=_CAPABILITY_FOR_BILLING,
            model=model,
            endpoint="video_submit",
            error=str(e),
        )
        _audit("video_submit_failed", user_id=billing_user_id, request_user_id=request_user_id, model=model, error=str(e)[:300])
        log_model_usage_event(
            None,
            category="video",
            event_kind="request",
            success=False,
            user_id=billing_user_id,
            requested_model=model,
            model=model,
            provider="comfly",
            channel="comfly",
            route="video_submit",
            endpoint="/api/comfly-proxy/v2/videos/generations",
            error_message=str(e)[:1000],
        )
        raise HTTPException(502, f"Comfly videos submit 调用失败：{e}")

    task_id = _task_id_from_response(resp) or (
        (resp.get("data", {}) or {}).get("task_id") if isinstance(resp.get("data"), dict) else resp.get("task_id")
    )
    api_kind = (
        "comfyui_grok_v1"
        if _is_comfyui_grok_api_format(entry)
        else ("grok_v1" if _is_grok_api_format(entry) else "veo_v2")
    )
    _remember_proxy_video_task(task_id, api_kind, model)
    _audit("video_submit_ok", user_id=billing_user_id, request_user_id=request_user_id, model=model,
           task_id=task_id,
           api_kind=api_kind,
           pre=credits_json_float(pre))
    log_model_usage_event(
        None,
        category="video",
        event_kind="request",
        success=True,
        user_id=billing_user_id,
        requested_model=model,
        model=model,
        provider="comfyui" if _is_comfyui_grok_api_format(entry) else "comfly",
        channel="comfyui" if _is_comfyui_grok_api_format(entry) else "comfly",
        route=api_kind,
        endpoint="/api/comfly-proxy/v2/videos/generations",
        request_id=task_id or "",
        meta={"api_kind": api_kind},
    )
    return JSONResponse(resp)


@router.get("/api/comfly-proxy/v2/videos/generations/{task_id}", summary="Comfly Veo 任务轮询 proxy（不计费）")
async def proxy_videos_generations_poll(
    task_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    remembered_kind, remembered_model = _proxy_video_task_hint(task_id)
    try:
        resp = await _poll_comfly_video_task(task_id, remembered_model, remembered_kind)
    except Exception as e:
        raise HTTPException(502, f"Comfly videos poll 调用失败：{e}")
    return JSONResponse(resp)





def _video_provider_policy(model: str, channel: str = "") -> Dict[str, Any]:
    raw_model = (model or "").strip()
    low_model = raw_model.lower().replace("_", "-").replace(" ", "")
    low_channel = (channel or "").strip().lower()
    proxy_base = "/api/comfly-proxy"

    if low_model.startswith("apiz/veo3.1/image-to-video") or low_model.startswith("apiz/veo3.1/reference-to-video"):
        low_channel = "grok"

    if low_channel in {"comfyui", "comfyui_video", "openmind", "grok", "xai", "official-xai", "x-ai"} or low_model in {"grok-video-3", "grok-imagine-video-1.5", "grok-imagine-video-1.5-preview", "grok-imagine-1.0-video", "yingmeng1.5plus"} or low_model.startswith("xai/grok-imagine-video/") or low_model.startswith("xai/grok-imagine-video-1.5/"):
        return {
            "ok": True,
            "model_family": "grok",
            "providers": [
                {"channel": "comfyui", "model": "grok-imagine-video-1.5", "base_url": proxy_base},
                {"channel": "xai", "model": "grok-imagine-video-1.5", "base_url": proxy_base},
                {"channel": "openmind", "model": "grok-video-3", "base_url": proxy_base},
                {"channel": "comfly", "model": "grok-video-3", "base_url": proxy_base},
            ],
        }

    if low_channel in {"yunwu", "??", "??"} or low_model in {"yunwu-veo3.1-plus", "veo3.1-plus", "veo3.1", "veo31", "veo31-fast", "veo3.1-fast"} or low_model.startswith("apiz/veo3.1/text-to-video"):
        return {
            "ok": True,
            "model_family": "veo31",
            "providers": [
                {"channel": "xai", "model": "grok-imagine-video-1.5", "base_url": proxy_base},
            ],
        }

    if low_model in {"seedance-2.5", "seedance2.5", "seedance2-5", "yingmeng2.5", "影梦2.5"}:
        return {
            "ok": True,
            "model_family": "seedance25",
            "providers": [
                {"channel": "xing", "model": "seedance-2.5", "base_url": proxy_base},
            ],
        }

    if low_model in {"seedance-2-0-pro-250528", "seedance-2-0-lite-250428", "seedance-2-0-260128", "seedance-2-0-fast-260128", "doubao-seedance-2-0-260128", "doubao-seedance-2-0-fast-260128", "seedance2.0-900", "seedance2-0-900"}:
        if low_model in {"seedance2.0-900", "seedance2-0-900"}:
            return {
                "ok": True,
                "model_family": "seedance20",
                "providers": [
                    {"channel": "xing", "model": "seedance2.0-900", "base_url": proxy_base},
                    {"channel": "openmind", "model": "doubao-seedance-2-0-260128", "base_url": proxy_base},
                    {"channel": "seedance", "model": "doubao-seedance-2-0-260128", "base_url": proxy_base},
                ],
            }
        return {
            "ok": True,
            "model_family": "seedance20",
            "providers": [
                {"channel": "openmind", "model": "doubao-seedance-2-0-260128", "base_url": proxy_base},
                {"channel": "seedance", "model": "doubao-seedance-2-0-260128", "base_url": proxy_base},
                {"channel": "xing", "model": "seedance2.0-900", "base_url": proxy_base},
            ],
        }

    return {
        "ok": True,
        "model_family": "default",
        "providers": [
            {"channel": "seedance", "model": raw_model or "doubao-seedance-2-0-260128", "base_url": proxy_base},
        ],
    }


@router.get("/api/comfly-proxy/video/provider-policy", summary="Server-controlled video provider fallback policy")
async def proxy_video_provider_policy(
    request: Request,
    model: str = "",
    channel: str = "",
    feature: str = "",
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    policy = _video_provider_policy(model, channel)
    _audit("video_provider_policy", user_id=current_user.id, model=model, channel=channel, feature=feature, family=policy.get("model_family"))
    return JSONResponse(policy)


@router.post("/api/comfly-proxy/openmind/v1/videos", summary="OpenMind video submit proxy")
async def proxy_openmind_video_submit(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "missing model")
    entry = _require_model_entry(model)
    upstream_body = _openmind_video_body(body, model, entry)

    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=True)
    estimated = estimate_comfly_credits(model, body, for_user=True) or 1
    internal_fallback = _is_trusted_internal_video_fallback(request)
    pre = Decimal("0") if internal_fallback else _do_pre_deduct_by_user_id(
        billing_user_id,
        estimated,
        capability_id=_CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="openmind_video_submit",
        extra_meta={"upstream": "openmind", "openmind_model": upstream_body.get("model")},
    )
    _audit(
        "openmind_video_submit_pre_deduct",
        user_id=billing_user_id,
        request_user_id=request_user_id,
        model=model,
        openmind_model=upstream_body.get("model"),
        estimated=estimated,
        billing_reused=internal_fallback,
    )

    try:
        resp = await _openmind_video_submit(body, model, entry)
    except Exception as e:
        _do_full_refund_by_user_id(
            billing_user_id,
            pre=pre,
            capability_id=_CAPABILITY_FOR_BILLING,
            model=model,
            endpoint="openmind_video_submit",
            error=str(e),
        )
        _audit(
            "openmind_video_submit_failed",
            user_id=billing_user_id,
            request_user_id=request_user_id,
            model=model,
            error=str(e)[:300],
        )
        log_model_usage_event(
            None,
            category="video",
            event_kind="request",
            success=False,
            user_id=billing_user_id,
            requested_model=model,
            model=model,
            provider="openmind",
            channel="openmind",
            route="openmind",
            endpoint="/api/comfly-proxy/openmind/v1/videos",
            error_message=str(e)[:1000],
        )
        raise HTTPException(502, f"OpenMind video submit failed: {e}")

    _audit(
        "openmind_video_submit_ok",
        user_id=billing_user_id,
        request_user_id=request_user_id,
        model=model,
        openmind_model=resp.get("_requested_model") if isinstance(resp, dict) else "",
        task_id=_task_id_from_response(resp),
        pre=credits_json_float(pre),
    )
    _remember_video_image_retry_context(
        _task_id_from_response(resp),
        provider="openmind",
        body=body,
        model=model,
        request_user_id=request_user_id,
    )
    log_model_usage_event(
        None,
        category="video",
        event_kind="request",
        success=True,
        user_id=billing_user_id,
        requested_model=model,
        model=model,
        provider="openmind",
        channel="openmind",
        route="openmind",
        endpoint="/api/comfly-proxy/openmind/v1/videos",
        request_id=_task_id_from_response(resp) or "",
        meta={"openmind_model": resp.get("_requested_model") if isinstance(resp, dict) else ""},
    )
    return JSONResponse(resp)


@router.get("/api/comfly-proxy/openmind/v1/videos/{task_id}", summary="OpenMind video poll proxy")
async def proxy_openmind_video_poll(
    task_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    requested_task_id = (task_id or "").strip()
    if not requested_task_id:
        raise HTTPException(400, "missing task_id")
    root_task_id, active_task_id, retry_context = _video_image_retry_poll_target(
        requested_task_id,
        provider="openmind",
        request_user_id=current_user.id,
    )
    if retry_context and retry_context.get("resubmit_state") == "submitting":
        return JSONResponse(
            _video_image_resubmit_pending_payload(
                root_task_id,
                provider="openmind",
                provider_task_id=active_task_id,
            )
        )
    try:
        poll_model = str((retry_context or {}).get("model") or "").strip()
        resp = await _openmind_video_poll(active_task_id, model=poll_model)
        replacement = await _maybe_resubmit_interrupted_video(
            root_task_id,
            provider="openmind",
            payload=resp,
            request_user_id=current_user.id,
        )
        if replacement is not None:
            resp = replacement
        else:
            resp = _normalize_retried_video_poll_payload(
                resp,
                root_task_id=root_task_id,
                active_task_id=active_task_id,
            )
    except Exception as e:
        raise HTTPException(502, f"OpenMind video poll failed: {e}")
    return JSONResponse(resp)


@router.get("/api/comfly-proxy/openmind/v1/videos/{task_id}/content", summary="OpenMind video content proxy")
async def proxy_openmind_video_content(
    task_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    root_task_id, active_task_id, retry_context = _video_image_retry_poll_target(
        task_id,
        provider="openmind",
        request_user_id=current_user.id,
    )
    content_model = str((retry_context or {}).get("model") or "").strip()
    return await _openmind_video_content(
        active_task_id or root_task_id or task_id,
        model=content_model,
    )


@router.post("/api/comfly-proxy/xing/v1/videos/generations", summary="Xing Seedance2.0-900 video submit proxy")
async def proxy_xing_seedance_submit(request: Request):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = str(body.get("model") or "seedance2.0-900").strip()
    _require_model_entry(model)
    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=False)
    estimated = estimate_comfly_credits(model, body, for_user=True) or 1
    internal_fallback = _is_trusted_internal_video_fallback(request)
    pre = Decimal("0") if internal_fallback else _do_pre_deduct_by_user_id(
        billing_user_id,
        estimated,
        capability_id=_CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="xing_seedance_submit",
        extra_meta={"upstream": "xing", "xing_model": model},
    )
    try:
        response = await _xing_seedance_submit(body, model)
    except Exception as exc:
        _do_full_refund_by_user_id(
            billing_user_id,
            pre=pre,
            capability_id=_CAPABILITY_FOR_BILLING,
            model=model,
            endpoint="xing_seedance_submit",
            error=str(exc),
        )
        _audit("xing_seedance_submit_failed", user_id=billing_user_id, request_user_id=request_user_id, model=model, error=str(exc)[:300])
        raise HTTPException(502, f"Xing Seedance submit failed: {exc}")
    task_id = _task_id_from_response(response)
    _remember_proxy_video_task(task_id, "xing", model)
    _audit("xing_seedance_submit_ok", user_id=billing_user_id, request_user_id=request_user_id, model=model, task_id=task_id, pre=credits_json_float(pre))
    log_model_usage_event(
        None,
        category="video",
        event_kind="request",
        success=True,
        user_id=billing_user_id,
        requested_model=model,
        model=model,
        provider="xing",
        channel="xing",
        route="xing_seedance",
        endpoint="/api/comfly-proxy/xing/v1/videos/generations",
        request_id=task_id or "",
        meta={"xing_model": model},
    )
    return JSONResponse(response)


@router.get("/api/comfly-proxy/xing/v1/videos/{task_id}", summary="Xing Seedance task poll proxy")
async def proxy_xing_seedance_poll(
    task_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    try:
        response = await _xing_seedance_poll(task_id)
    except Exception as exc:
        raise HTTPException(502, f"Xing Seedance poll failed: {exc}")
    return JSONResponse(response)


@router.get("/api/comfly-proxy/xing/v1/videos/{task_id}/content", summary="Xing Seedance content proxy")
async def proxy_xing_seedance_content(
    task_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    return await _xing_seedance_content(task_id)


@router.post("/api/comfly-proxy/xai/v1/videos/generations", summary="Official xAI Grok video submit proxy")
async def proxy_xai_video_submit(request: Request):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = str(body.get("model") or "grok-imagine-video-1.5").strip()
    _require_model_entry(model)
    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=False)
    estimated = estimate_comfly_credits(model, body, for_user=True) or 1
    internal_fallback = _is_trusted_internal_video_fallback(request)
    pre = Decimal("0") if internal_fallback else _do_pre_deduct_by_user_id(
        billing_user_id,
        estimated,
        capability_id=_CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="xai_video_submit",
        extra_meta={"upstream": "xai"},
    )
    try:
        response = await _xai_video_submit(body, model)
    except Exception as exc:
        _do_full_refund_by_user_id(
            billing_user_id,
            pre=pre,
            capability_id=_CAPABILITY_FOR_BILLING,
            model=model,
            endpoint="xai_video_submit",
            error=str(exc),
        )
        _audit("xai_video_submit_failed", user_id=billing_user_id, request_user_id=request_user_id, model=model, error=str(exc)[:300])
        raise HTTPException(502, f"xAI video submit failed: {exc}")
    task_id = _task_id_from_response(response)
    _remember_proxy_video_task(task_id, "xai", model)
    _remember_video_image_retry_context(
        task_id,
        provider="xai",
        body=body,
        model=model,
        request_user_id=request_user_id,
    )
    _audit("xai_video_submit_ok", user_id=billing_user_id, request_user_id=request_user_id, model=model, task_id=task_id, pre=credits_json_float(pre))
    return JSONResponse(response)


@router.get("/api/comfly-proxy/xai/v1/videos/{request_id}", summary="Official xAI Grok video poll proxy")
async def proxy_xai_video_poll(
    request_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    root_task_id, active_task_id, retry_context = _video_image_retry_poll_target(
        request_id,
        provider="xai",
        request_user_id=current_user.id,
    )
    if retry_context and retry_context.get("resubmit_state") == "submitting":
        return JSONResponse(
            _video_image_resubmit_pending_payload(
                root_task_id,
                provider="xai",
                provider_task_id=active_task_id,
            )
        )
    try:
        response = await _xai_video_poll(active_task_id)
        replacement = await _maybe_resubmit_interrupted_video(
            root_task_id,
            provider="xai",
            payload=response,
            request_user_id=current_user.id,
        )
        if replacement is not None:
            response = replacement
        else:
            response = _normalize_retried_video_poll_payload(
                response,
                root_task_id=root_task_id,
                active_task_id=active_task_id,
            )
    except Exception as exc:
        raise HTTPException(502, f"xAI video poll failed: {exc}")
    return JSONResponse(response)


@router.post("/api/comfly-proxy/v1/video/create", summary="Yunwu video create proxy")
async def proxy_yunwu_video_create(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "missing model")
    entry = _require_model_entry(model)
    upstream_body = _body_for_upstream_model(body, model, entry)
    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=False)

    estimated = estimate_comfly_credits(model, body, for_user=True) or 1
    internal_fallback = _is_trusted_internal_video_fallback(request)
    pre = Decimal("0") if internal_fallback else _do_pre_deduct_by_user_id(
        billing_user_id,
        estimated,
        capability_id=_CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="yunwu_video_create",
    )
    _audit(
        "yunwu_video_create_pre_deduct",
        user_id=billing_user_id,
        request_user_id=request_user_id,
        model=model,
        estimated=estimated,
        billing_reused=internal_fallback,
    )

    try:
        resp = await _yunwu_request(
            "POST",
            f"{_yunwu_base_url()}/v1/video/create",
            upstream_body,
            _yunwu_headers(),
            _TIMEOUT_VIDEO_SUBMIT,
        )
    except Exception as e:
        _do_full_refund_by_user_id(
            billing_user_id,
            pre=pre,
            capability_id=_CAPABILITY_FOR_BILLING,
            model=model,
            endpoint="yunwu_video_create",
            error=str(e),
        )
        _audit("yunwu_video_create_failed", user_id=billing_user_id, request_user_id=request_user_id, model=model, error=str(e)[:300])
        log_model_usage_event(
            None,
            category="video",
            event_kind="request",
            success=False,
            user_id=billing_user_id,
            requested_model=model,
            model=model,
            provider="yunwu",
            channel="yunwu",
            route="yunwu",
            endpoint="/api/comfly-proxy/v1/video/create",
            error_message=str(e)[:1000],
        )
        raise HTTPException(502, f"Yunwu video create failed: {e}")

    _audit("yunwu_video_create_ok", user_id=billing_user_id, request_user_id=request_user_id, model=model, task_id=resp.get("id"), pre=credits_json_float(pre))
    log_model_usage_event(
        None,
        category="video",
        event_kind="request",
        success=True,
        user_id=billing_user_id,
        requested_model=model,
        model=model,
        provider="yunwu",
        channel="yunwu",
        route="yunwu",
        endpoint="/api/comfly-proxy/v1/video/create",
        request_id=str(resp.get("id") or ""),
    )
    return JSONResponse(resp)


@router.get("/api/comfly-proxy/v1/video/query", summary="Yunwu video query proxy")
async def proxy_yunwu_video_query(
    id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    task_id = (id or "").strip()
    if not task_id:
        raise HTTPException(400, "missing id")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_VIDEO_POLL) as client:
            r = await client.get(
                f"{_yunwu_base_url()}/v1/video/query",
                params={"id": task_id},
                headers={"Authorization": f"Bearer {_yunwu_api_key()}", "Accept": "application/json"},
            )
        if r.status_code >= 400:
            raise RuntimeError(f"Yunwu HTTP {r.status_code}: {(r.text or '')[:500]}")
        resp = r.json() if r.content else {}
    except Exception as e:
        raise HTTPException(502, f"Yunwu video query failed: {e}")
    return JSONResponse(resp)


@router.post("/api/comfly-proxy/seedance/v3/contents/generations/tasks", summary="Comfly Seedance 视频提交 proxy（按定价表预扣）")
async def proxy_seedance_tasks_submit(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "缺少 model")
    entry = _require_model_entry(model)
    upstream_body = _body_for_upstream_model(body, model, entry)

    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=False)
    estimated = estimate_comfly_credits(model, body, for_user=True) or 1
    pre = _do_pre_deduct_by_user_id(
        billing_user_id,
        estimated,
        capability_id=_CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="seedance_submit",
    )
    _audit("seedance_submit_pre_deduct", user_id=billing_user_id, request_user_id=request_user_id, model=model, estimated=estimated)

    try:
        resp = await _comfly_request(
            "POST",
            _comfly_url("/seedance/v3/contents/generations/tasks", model),
            upstream_body,
            _comfly_headers(model),
            _TIMEOUT_VIDEO_SUBMIT,
        )
    except Exception as e:
        _do_full_refund_by_user_id(billing_user_id, pre=pre,
                        capability_id=_CAPABILITY_FOR_BILLING, model=model, endpoint="seedance_submit", error=str(e))
        _audit("seedance_submit_failed", user_id=billing_user_id, request_user_id=request_user_id, model=model, error=str(e)[:300])
        raise HTTPException(502, f"Comfly Seedance submit 调用失败：{e}")

    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    _audit("seedance_submit_ok", user_id=billing_user_id, request_user_id=request_user_id, model=model,
           task_id=resp.get("id") or resp.get("task_id") or data.get("task_id") or data.get("id"),
           pre=credits_json_float(pre))
    return JSONResponse(resp)


@router.get("/api/comfly-proxy/seedance/v3/contents/generations/tasks/{task_id}", summary="Comfly Seedance 任务轮询 proxy（不计费）")
async def proxy_seedance_tasks_poll(
    task_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    try:
        resp = await _comfly_request(
            "GET",
            _comfly_url(f"/seedance/v3/contents/generations/tasks/{task_id}"),
            None,
            _comfly_headers(),
            _TIMEOUT_VIDEO_POLL,
        )
    except Exception as e:
        raise HTTPException(502, f"Comfly Seedance poll 调用失败：{e}")
    return JSONResponse(resp)
