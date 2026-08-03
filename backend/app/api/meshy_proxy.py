from __future__ import annotations

import json
import os
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Header, HTTPException


router = APIRouter()

MESHY_API_BASE = "https://api.meshy.ai/openapi/v1"
_TIMEOUT_BALANCE = 30.0
_TIMEOUT_SUBMIT = 120.0
_TIMEOUT_POLL = 45.0


def _secret() -> str:
    return (os.environ.get("LOBSTER_MESHY_PROXY_SECRET") or "").strip()


def _api_key() -> str:
    return (
        os.environ.get("MESHY_API_KEY")
        or os.environ.get("LOBSTER_MESHY_API_KEY")
        or ""
    ).strip()


def _require_secret(header_value: str | None) -> None:
    expected = _secret()
    if not expected:
        raise HTTPException(status_code=503, detail="LOBSTER_MESHY_PROXY_SECRET is not configured")
    if not header_value or header_value != expected:
        raise HTTPException(status_code=401, detail="Unauthorized Meshy proxy request")


def _headers() -> Dict[str, str]:
    key = _api_key()
    if not key:
        raise HTTPException(status_code=503, detail="MESHY_API_KEY is not configured on relay server")
    return {"Authorization": f"Bearer {key}"}


def _response_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        text = (resp.text or "").strip()
        return text or f"Meshy HTTP {resp.status_code}"
    if isinstance(data, dict):
        msg = data.get("message") or data.get("detail") or data.get("error")
        if msg:
            return str(msg)
    return json.dumps(data, ensure_ascii=False)[:800]


async def _meshy_request(method: str, path: str, *, timeout: float, json_body: Any = None) -> Dict[str, Any]:
    url = f"{MESHY_API_BASE}{path}"
    headers = _headers()
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
            resp = await client.request(method, url, headers=headers, json=json_body)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise HTTPException(status_code=502, detail=f"Meshy relay upstream connection failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=_response_error(resp))
    try:
        data = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Meshy relay returned non-JSON response: {resp.text[:500]}") from exc
    return data if isinstance(data, dict) else {"data": data}


@router.get("/api/meshy-proxy/health", summary="Meshy relay health")
async def meshy_proxy_health():
    return {"ok": True, "configured": bool(_api_key()), "requires_secret": bool(_secret())}


@router.get("/api/meshy-proxy/balance", summary="Relay Meshy balance")
async def meshy_proxy_balance(x_lobster_meshy_proxy_secret: str | None = Header(default=None)):
    _require_secret(x_lobster_meshy_proxy_secret)
    return await _meshy_request("GET", "/balance", timeout=_TIMEOUT_BALANCE)


@router.post("/api/meshy-proxy/image-to-3d", summary="Relay Meshy image-to-3d submit")
async def meshy_proxy_image_to_3d(body: Dict[str, Any], x_lobster_meshy_proxy_secret: str | None = Header(default=None)):
    _require_secret(x_lobster_meshy_proxy_secret)
    return await _meshy_request("POST", "/image-to-3d", timeout=_TIMEOUT_SUBMIT, json_body=body)


@router.post("/api/meshy-proxy/multi-image-to-3d", summary="Relay Meshy multi-image-to-3d submit")
async def meshy_proxy_multi_image_to_3d(body: Dict[str, Any], x_lobster_meshy_proxy_secret: str | None = Header(default=None)):
    _require_secret(x_lobster_meshy_proxy_secret)
    return await _meshy_request("POST", "/multi-image-to-3d", timeout=_TIMEOUT_SUBMIT, json_body=body)


@router.get("/api/meshy-proxy/image-to-3d/{task_id}", summary="Relay Meshy image-to-3d poll")
async def meshy_proxy_get_image_to_3d(task_id: str, x_lobster_meshy_proxy_secret: str | None = Header(default=None)):
    _require_secret(x_lobster_meshy_proxy_secret)
    return await _meshy_request("GET", f"/image-to-3d/{task_id}", timeout=_TIMEOUT_POLL)


@router.get("/api/meshy-proxy/multi-image-to-3d/{task_id}", summary="Relay Meshy multi-image-to-3d poll")
async def meshy_proxy_get_multi_image_to_3d(task_id: str, x_lobster_meshy_proxy_secret: str | None = Header(default=None)):
    _require_secret(x_lobster_meshy_proxy_secret)
    return await _meshy_request("GET", f"/multi-image-to-3d/{task_id}", timeout=_TIMEOUT_POLL)
