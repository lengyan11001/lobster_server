from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter

router = APIRouter()

_TIMEOUT = 30.0
_QUOTA_DIVISOR = 500000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask_token(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 12:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def _clean_base(raw: str, default: str) -> str:
    base = (raw or default or "").strip().rstrip("/")
    return base or default.rstrip("/")


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


async def _get_json(url: str, headers: Dict[str, str]) -> tuple[int, Dict[str, Any] | None, str]:
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True, trust_env=False) as client:
        resp = await client.get(url, headers=headers)
    text = resp.text or ""
    try:
        payload = resp.json() if resp.content else {}
    except Exception:
        payload = None
    return resp.status_code, payload if isinstance(payload, dict) else None, text[:1200]


def _new_api_balance_from_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else {}
    quota = data.get("quota")
    used_quota = data.get("used_quota")
    out: Dict[str, Any] = {}
    if isinstance(quota, (int, float)):
        out["quota"] = quota
        out["balance"] = quota / _QUOTA_DIVISOR
        out["balance_unit"] = "site_credit"
        out["quota_unit"] = f"raw_quota/{_QUOTA_DIVISOR}"
    if isinstance(used_quota, (int, float)):
        out["used_quota"] = used_quota
        out["used_balance"] = used_quota / _QUOTA_DIVISOR
    for key in ("id", "username", "display_name", "status", "group", "request_count"):
        if key in data:
            out[key] = data.get(key)
    return out


def _provider_error(
    *,
    provider: str,
    name: str,
    configured: bool,
    message: str,
    checked_at: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "provider": provider,
        "name": name,
        "ok": False,
        "configured": configured,
        "error": message,
        "checked_at": checked_at,
    }
    if extra:
        item.update(extra)
    return item


async def query_hifly_credit(checked_at: str) -> Dict[str, Any]:
    token = (os.environ.get("HIFLY_DEFAULT_TOKEN") or os.environ.get("HIFLY_TOKEN") or "").strip()
    url = _clean_base(os.environ.get("HIFLY_API_BASE") or "", "https://hfw-api.hifly.cc")
    url += "/api/v2/hifly/account/credit"
    if not token:
        return _provider_error(
            provider="hifly",
            name="HiFly Digital Human",
            configured=False,
            message="Missing HIFLY_DEFAULT_TOKEN",
            checked_at=checked_at,
        )
    try:
        status_code, payload, text = await _get_json(url, {"Authorization": f"Bearer {token}"})
    except Exception as exc:
        return _provider_error(
            provider="hifly",
            name="HiFly Digital Human",
            configured=True,
            message=f"Request failed: {type(exc).__name__}: {exc}",
            checked_at=checked_at,
            extra={"url": url, "token_mask": _mask_token(token)},
        )
    code = payload.get("code") if isinstance(payload, dict) else None
    ok = status_code == 200 and code == 0
    item: Dict[str, Any] = {
        "provider": "hifly",
        "name": "HiFly Digital Human",
        "ok": ok,
        "configured": True,
        "url": url,
        "token_env": "HIFLY_DEFAULT_TOKEN",
        "token_mask": _mask_token(token),
        "status_code": status_code,
        "code": code,
        "message": (payload or {}).get("message", "") if isinstance(payload, dict) else text,
        "request_id": (payload or {}).get("request_id", "") if isinstance(payload, dict) else "",
        "checked_at": checked_at,
    }
    left = (payload or {}).get("left") if isinstance(payload, dict) else None
    if isinstance(left, (int, float)):
        item["balance"] = left
        item["balance_unit"] = "hifly_credit"
    if not ok:
        item["error"] = item["message"] or f"HTTP {status_code}"
    return item


def _pick_account_token(*names: str) -> tuple[str, str]:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value, name
    return "", ""


async def query_comfly_credit(checked_at: str) -> Dict[str, Any]:
    base = _clean_base(
        os.environ.get("COMFLY_ACCOUNT_API_BASE") or os.environ.get("COMFLY_API_BASE") or "",
        "https://ai.comfly.org",
    )
    user_id = (os.environ.get("COMFLY_ACCOUNT_USER_ID") or os.environ.get("COMFLY_NEW_API_USER_ID") or "116583").strip()
    token, token_env = _pick_account_token(
        "COMFLY_ACCOUNT_TOKEN",
        "COMFLY_SYSTEM_TOKEN",
        "COMFLY_NEW_API_TOKEN",
        "COMFLY_USER_SELF_TOKEN",
    )
    if not token and _bool_env("PROVIDER_BALANCE_ALLOW_MODEL_KEY_FALLBACK", True):
        token, token_env = _pick_account_token("COMFLY_API_KEY", "COMFLY_API_KEY_VEO31", "COMFLY_API_KEY_PREMIUM")
    url = base + "/api/user/self"
    if not token or not user_id:
        return _provider_error(
            provider="comfly",
            name="Comfly / New-API",
            configured=False,
            message="Missing COMFLY_ACCOUNT_TOKEN/COMFLY_SYSTEM_TOKEN or COMFLY_ACCOUNT_USER_ID",
            checked_at=checked_at,
            extra={"url": url, "user_id": user_id, "needs_system_token": True},
        )
    headers = {"Authorization": f"Bearer {token}", "New-API-User": user_id}
    try:
        status_code, payload, text = await _get_json(url, headers)
    except Exception as exc:
        return _provider_error(
            provider="comfly",
            name="Comfly / New-API",
            configured=True,
            message=f"Request failed: {type(exc).__name__}: {exc}",
            checked_at=checked_at,
            extra={"url": url, "user_id": user_id, "token_env": token_env, "token_mask": _mask_token(token)},
        )
    success = bool(payload.get("success") is True) if isinstance(payload, dict) else False
    message = str((payload or {}).get("message") or "") if isinstance(payload, dict) else text
    item: Dict[str, Any] = {
        "provider": "comfly",
        "name": "Comfly / New-API",
        "ok": status_code == 200 and success,
        "configured": True,
        "url": url,
        "user_id": user_id,
        "token_env": token_env,
        "token_mask": _mask_token(token),
        "using_model_key_fallback": token_env in {"COMFLY_API_KEY", "COMFLY_API_KEY_VEO31", "COMFLY_API_KEY_PREMIUM"},
        "status_code": status_code,
        "success": success,
        "message": message,
        "checked_at": checked_at,
    }
    item.update(_new_api_balance_from_payload(payload))
    if not item["ok"]:
        item["error"] = message or f"HTTP {status_code}"
        if "invalid access token" in message.lower() or "unauthorized" in message.lower():
            item["needs_system_token"] = True
            item["hint"] = "This endpoint requires a system token from the account center; model sk keys cannot query /api/user/self."
    return item


async def query_yunwu_credit(checked_at: str) -> Dict[str, Any]:
    base = _clean_base(
        os.environ.get("YUNWU_ACCOUNT_API_BASE") or os.environ.get("YUNWU_API_BASE") or "",
        "https://yunwu.ai",
    )
    user_id = (os.environ.get("YUNWU_ACCOUNT_USER_ID") or os.environ.get("YUNWU_NEW_API_USER_ID") or "559736").strip()
    token, token_env = _pick_account_token("YUNWU_ACCOUNT_TOKEN", "YUNWU_SYSTEM_TOKEN", "YUNWU_NEW_API_TOKEN", "YUNWU_USER_SELF_TOKEN")
    if not token and _bool_env("PROVIDER_BALANCE_ALLOW_MODEL_KEY_FALLBACK", True):
        token, token_env = _pick_account_token("YUNWU_API_KEY", "COMFLY_API_KEY_YUNWU")
    url = base + "/api/user/self"
    if not token or not user_id:
        return _provider_error(
            provider="yunwu",
            name="Yunwu / New-API",
            configured=False,
            message="Missing YUNWU_ACCOUNT_TOKEN/YUNWU_SYSTEM_TOKEN or YUNWU_ACCOUNT_USER_ID",
            checked_at=checked_at,
            extra={"url": url, "user_id": user_id, "needs_system_token": True},
        )
    headers = {"Authorization": token, "new-api-user": user_id}
    try:
        status_code, payload, text = await _get_json(url, headers)
    except Exception as exc:
        return _provider_error(
            provider="yunwu",
            name="Yunwu / New-API",
            configured=True,
            message=f"Request failed: {type(exc).__name__}: {exc}",
            checked_at=checked_at,
            extra={"url": url, "user_id": user_id, "token_env": token_env, "token_mask": _mask_token(token)},
        )
    success = bool(payload.get("success") is True) if isinstance(payload, dict) else False
    message = str((payload or {}).get("message") or "") if isinstance(payload, dict) else text
    item: Dict[str, Any] = {
        "provider": "yunwu",
        "name": "Yunwu / New-API",
        "ok": status_code == 200 and success,
        "configured": True,
        "url": url,
        "user_id": user_id,
        "token_env": token_env,
        "token_mask": _mask_token(token),
        "using_model_key_fallback": token_env in {"YUNWU_API_KEY", "COMFLY_API_KEY_YUNWU"},
        "status_code": status_code,
        "success": success,
        "message": message,
        "checked_at": checked_at,
    }
    item.update(_new_api_balance_from_payload(payload))
    if not item["ok"]:
        item["error"] = message or f"HTTP {status_code}"
        if "access token" in message.lower() or "invalid" in message.lower() or "无权" in message:
            item["needs_system_token"] = True
            item["hint"] = "This endpoint requires a system token from Yunwu account center; model sk keys cannot query /api/user/self."
    return item


async def query_openmind_credit(checked_at: str) -> Dict[str, Any]:
    base = _clean_base(os.environ.get("OPENMIND_ACCOUNT_API_BASE") or os.environ.get("OPENMIND_API_BASE") or "", "https://www.openmindapi.com")
    user_id = (os.environ.get("OPENMIND_ACCOUNT_USER_ID") or os.environ.get("OPENMIND_NEW_API_USER_ID") or "").strip()
    token, token_env = _pick_account_token("OPENMIND_ACCOUNT_TOKEN", "OPENMIND_SYSTEM_TOKEN", "OPENMIND_NEW_API_TOKEN", "OPENMIND_USER_SELF_TOKEN")
    api_key, api_key_env = _pick_account_token("OPENMIND_API_KEY")

    if token and user_id:
        url = base + "/api/user/self"
        headers = {"Authorization": token, "New-Api-User": user_id}
        try:
            status_code, payload, text = await _get_json(url, headers)
        except Exception as exc:
            return _provider_error(
                provider="openmind",
                name="OpenMind API / New-API",
                configured=True,
                message=f"Request failed: {type(exc).__name__}: {exc}",
                checked_at=checked_at,
                extra={"url": url, "user_id": user_id, "token_env": token_env, "token_mask": _mask_token(token)},
            )
        success = bool(payload.get("success") is True) if isinstance(payload, dict) else False
        message = str((payload or {}).get("message") or "") if isinstance(payload, dict) else text
        item: Dict[str, Any] = {
            "provider": "openmind",
            "name": "OpenMind API / New-API",
            "ok": status_code == 200 and success,
            "configured": True,
            "url": url,
            "user_id": user_id,
            "token_env": token_env,
            "token_mask": _mask_token(token),
            "status_code": status_code,
            "success": success,
            "message": message,
            "checked_at": checked_at,
        }
        item.update(_new_api_balance_from_payload(payload))
        if not item["ok"]:
            item["error"] = message or f"HTTP {status_code}"
            if "new-api-user" in message.lower() or "unauthorized" in message.lower():
                item["needs_system_token"] = True
                item["hint"] = "OpenMind /api/user/self requires OPENMIND_ACCOUNT_TOKEN and numeric OPENMIND_ACCOUNT_USER_ID."
        return item

    if api_key:
        url = base + "/api/usage/token/"
        try:
            status_code, payload, text = await _get_json(url, {"Authorization": f"Bearer {api_key}"})
        except Exception as exc:
            return _provider_error(
                provider="openmind",
                name="OpenMind API",
                configured=True,
                message=f"Request failed: {type(exc).__name__}: {exc}",
                checked_at=checked_at,
                extra={"url": url, "token_env": api_key_env, "token_mask": _mask_token(api_key)},
            )
        data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else {}
        code = payload.get("code") if isinstance(payload, dict) else None
        ok = status_code == 200 and code is True and isinstance(data, dict)
        item = {
            "provider": "openmind",
            "name": "OpenMind API",
            "ok": ok,
            "configured": True,
            "url": url,
            "token_env": api_key_env,
            "token_mask": _mask_token(api_key),
            "status_code": status_code,
            "code": code,
            "message": (payload or {}).get("message", "") if isinstance(payload, dict) else text,
            "checked_at": checked_at,
            "balance_unit": "site_credit",
            "quota_unit": f"raw_quota/{_QUOTA_DIVISOR}",
        }
        total_available = data.get("total_available")
        total_used = data.get("total_used")
        total_granted = data.get("total_granted")
        if isinstance(total_available, (int, float)):
            item["quota"] = total_available
            item["balance"] = total_available / _QUOTA_DIVISOR
        if isinstance(total_used, (int, float)):
            item["used_quota"] = total_used
            item["used_balance"] = total_used / _QUOTA_DIVISOR
        if isinstance(total_granted, (int, float)):
            item["total_granted"] = total_granted
            item["total_granted_balance"] = total_granted / _QUOTA_DIVISOR
        for key in ("object", "name", "unlimited_quota", "expires_at", "request_count"):
            if key in data:
                item[key] = data.get(key)
        if not ok:
            item["error"] = item["message"] or f"HTTP {status_code}"
        return item

    return _provider_error(
        provider="openmind",
        name="OpenMind API",
        configured=False,
        message="Missing OPENMIND_ACCOUNT_TOKEN + OPENMIND_ACCOUNT_USER_ID or OPENMIND_API_KEY",
        checked_at=checked_at,
        extra={"url": base + "/api/user/self", "needs_system_token": True},
    )


async def collect_provider_balances() -> Dict[str, Any]:
    checked_at = _now_iso()
    results = [
        await query_hifly_credit(checked_at),
        await query_comfly_credit(checked_at),
        await query_yunwu_credit(checked_at),
        await query_openmind_credit(checked_at),
    ]
    return {
        "ok": all(bool(item.get("ok")) for item in results),
        "checked_at": checked_at,
        "providers": results,
        "summary": {
            item["provider"]: {
                "ok": bool(item.get("ok")),
                "balance": item.get("balance"),
                "balance_unit": item.get("balance_unit"),
                "error": item.get("error"),
                "needs_system_token": bool(item.get("needs_system_token")),
            }
            for item in results
        },
    }


@router.get("/api/provider-balances", summary="Query upstream provider balances")
async def provider_balances():
    return await collect_provider_balances()


@router.get("/api/provider-balances/health", summary="Query upstream provider balances summary")
async def provider_balances_health():
    data = await collect_provider_balances()
    return {"ok": data["ok"], "checked_at": data["checked_at"], "summary": data["summary"]}
