from __future__ import annotations

import time
from typing import Any, Dict, Optional

import httpx
from ..core.config import settings
from ..models import JuheWechatConfig


JUHE_API_BASE_DEFAULT = "https://chat-api.juhebot.com"


def juhe_api_base() -> str:
    return (
        getattr(settings, "juhe_wechat_api_base", None)
        or JUHE_API_BASE_DEFAULT
    ).strip().rstrip("/")


def resolve_app_credentials(config: Optional[JuheWechatConfig]) -> tuple[str, str]:
    key = ((getattr(config, "app_key", None) or "").strip() if config is not None else "")
    secret = ((getattr(config, "app_secret", None) or "").strip() if config is not None else "")
    if not key or not secret:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="This WeChat instance has no App Key/App Secret configured")
    return key, secret


def mask_secret(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return "****"
    return raw[:4] + "..." + raw[-4:]


def safe_request_snapshot(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data or {})
    if "content" in out and isinstance(out.get("content"), str):
        text = out["content"]
        out["content"] = text[:500] + ("..." if len(text) > 500 else "")
    return out


async def guid_request(
    *,
    path: str,
    data: Dict[str, Any],
    config: Optional[JuheWechatConfig] = None,
    timeout_seconds: float = 30.0,
) -> tuple[Dict[str, Any], int, int]:
    app_key, app_secret = resolve_app_credentials(config)
    url = juhe_api_base() + "/open/GuidRequest"
    started = time.perf_counter()
    payload = {
        "app_key": app_key,
        "app_secret": app_secret,
        "path": path,
        "data": data,
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(url, json=payload)
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:2000]}
    return body, resp.status_code, latency_ms
