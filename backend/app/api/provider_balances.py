from __future__ import annotations

import os
import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote, urlparse
from xml.etree import ElementTree

import httpx
from fastapi import APIRouter

router = APIRouter()

_TIMEOUT = 30.0
_QUOTA_DIVISOR = 500000
_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_CUSTOM_CONFIGS_FILE = _BASE_DIR / "custom_configs.json"


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


async def _post_form(url: str, data: Dict[str, str], headers: Optional[Dict[str, str]] = None) -> tuple[int, Dict[str, Any] | None, str]:
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True, trust_env=False) as client:
        resp = await client.post(url, data=data, headers=headers or {})
    text = resp.text or ""
    payload: Dict[str, Any] | None = None
    try:
        parsed = resp.json() if resp.content else {}
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:
        try:
            root = ElementTree.fromstring(resp.content)
            payload = {child.tag: child.text for child in list(root)}
            if root.text and not payload:
                payload = {root.tag: root.text}
        except Exception:
            payload = None
    return resp.status_code, payload, text[:1200]


def _read_custom_configs() -> Dict[str, Any]:
    if not _CUSTOM_CONFIGS_FILE.exists():
        return {}
    try:
        data = json.loads(_CUSTOM_CONFIGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _tos_config() -> Dict[str, Any]:
    cfg = (_read_custom_configs().get("configs") or {}).get("TOS_CONFIG")
    return cfg if isinstance(cfg, dict) else {}


def _to_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _find_numeric_field(obj: Any, keys: tuple[str, ...]) -> tuple[Optional[float], str, Any]:
    if isinstance(obj, dict):
        lower = {str(k).lower(): k for k in obj.keys()}
        for key in keys:
            real_key = lower.get(key.lower())
            if real_key is not None:
                value = obj.get(real_key)
                number = _to_float(value)
                if number is not None:
                    return number, str(real_key), value
        for value in obj.values():
            number, key, raw = _find_numeric_field(value, keys)
            if number is not None:
                return number, key, raw
    elif isinstance(obj, list):
        for value in obj:
            number, key, raw = _find_numeric_field(value, keys)
            if number is not None:
                return number, key, raw
    return None, "", None


def _canonical_query(params: Dict[str, str]) -> str:
    return "&".join(f"{quote(str(k), safe='-_.~')}={quote(str(v), safe='-_.~')}" for k, v in sorted(params.items()))


def _hmac_sha256(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _volcengine_signed_headers(
    *,
    method: str,
    url: str,
    query: Dict[str, str],
    body: bytes,
    access_key: str,
    secret_key: str,
    service: str,
    region: str,
) -> Dict[str, str]:
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path or "/"
    x_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]
    payload_hash = hashlib.sha256(body).hexdigest()
    content_type = "application/json"
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_headers = "\n".join(
        [
            f"content-type:{content_type}",
            f"host:{host}",
            f"x-content-sha256:{payload_hash}",
            f"x-date:{x_date}",
        ]
    )
    canonical_request = "\n".join(
        [
            method.upper(),
            path,
            _canonical_query(query),
            canonical_headers + "\n",
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join(
        [
            "HMAC-SHA256",
            x_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    k_date = _hmac_sha256(secret_key.encode("utf-8"), short_date)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    k_signing = _hmac_sha256(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Host": host,
        "Content-Type": content_type,
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
        "Authorization": (
            f"HMAC-SHA256 Credential={access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }


async def _volcengine_get_json(
    *,
    base: str,
    query: Dict[str, str],
    access_key: str,
    secret_key: str,
    service: str,
    region: str,
) -> tuple[int, Dict[str, Any] | None, str, str]:
    base = _clean_base(base, "https://open.volcengineapi.com")
    url = base + "/"
    headers = _volcengine_signed_headers(
        method="GET",
        url=url,
        query=query,
        body=b"",
        access_key=access_key,
        secret_key=secret_key,
        service=service,
        region=region,
    )
    full_url = url + "?" + _canonical_query(query)
    status_code, payload, text = await _get_json(full_url, headers)
    return status_code, payload, text, full_url


def _aliyun_percent_encode(value: Any) -> str:
    return quote(str(value), safe="-_.~")


def _aliyun_signed_query(params: Dict[str, str], access_key_secret: str) -> str:
    canonicalized = "&".join(f"{_aliyun_percent_encode(k)}={_aliyun_percent_encode(v)}" for k, v in sorted(params.items()))
    string_to_sign = "GET&%2F&" + _aliyun_percent_encode(canonicalized)
    digest = hmac.new((access_key_secret + "&").encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    signature = base64.b64encode(digest).decode("utf-8")
    signed = dict(params)
    signed["Signature"] = signature
    return "&".join(f"{_aliyun_percent_encode(k)}={_aliyun_percent_encode(v)}" for k, v in sorted(signed.items()))


async def _aliyun_rpc_get_json(*, base: str, params: Dict[str, str], access_key_secret: str) -> tuple[int, Dict[str, Any] | None, str, str]:
    base = _clean_base(base, "https://business.aliyuncs.com")
    full_url = base + "/?" + _aliyun_signed_query(params, access_key_secret)
    status_code, payload, text = await _get_json(full_url, {})
    display_url = base + "/?Action=" + _aliyun_percent_encode(params.get("Action", ""))
    return status_code, payload, text, display_url


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


async def query_tos_account_balance(checked_at: str) -> Dict[str, Any]:
    cfg = _tos_config()
    ak = (os.environ.get("VOLCENGINE_BILLING_ACCESS_KEY") or os.environ.get("VOLCENGINE_ACCESS_KEY") or str(cfg.get("access_key") or "")).strip()
    sk = (os.environ.get("VOLCENGINE_BILLING_SECRET_KEY") or os.environ.get("VOLCENGINE_SECRET_KEY") or str(cfg.get("secret_key") or "")).strip()
    region = (os.environ.get("VOLCENGINE_BILLING_REGION") or "cn-beijing").strip()
    base = _clean_base(os.environ.get("VOLCENGINE_BILLING_API_BASE") or "", "https://billing.volcengineapi.com")
    service = (os.environ.get("VOLCENGINE_BILLING_SERVICE") or "billing").strip()
    version = (os.environ.get("VOLCENGINE_BILLING_VERSION") or "2022-01-01").strip()
    action = (os.environ.get("VOLCENGINE_BILLING_BALANCE_ACTION") or "QueryBalanceAcct").strip()
    bucket = str(cfg.get("bucket_name") or "").strip()
    if not ak or not sk:
        return _provider_error(
            provider="tos",
            name="Volcengine TOS / Account Balance",
            configured=False,
            message="Missing TOS_CONFIG access_key/secret_key or VOLCENGINE_BILLING_ACCESS_KEY/VOLCENGINE_BILLING_SECRET_KEY",
            checked_at=checked_at,
            extra={"bucket": bucket, "url": base + "/"},
        )
    query = {"Action": action, "Version": version}
    try:
        status_code, payload, text, url = await _volcengine_get_json(
            base=base,
            query=query,
            access_key=ak,
            secret_key=sk,
            service=service,
            region=region,
        )
    except Exception as exc:
        return _provider_error(
            provider="tos",
            name="Volcengine TOS / Account Balance",
            configured=True,
            message=f"Request failed: {type(exc).__name__}: {exc}",
            checked_at=checked_at,
            extra={"url": base + "/", "region": region, "service": service, "bucket": bucket, "token_mask": _mask_token(ak)},
        )
    response_meta = payload.get("ResponseMetadata") if isinstance(payload, dict) else None
    response_error = response_meta.get("Error") if isinstance(response_meta, dict) and isinstance(response_meta.get("Error"), dict) else None
    result = payload.get("Result") if isinstance(payload, dict) else None
    balance, balance_key, raw_balance = _find_numeric_field(
        result if result is not None else payload,
        (
            "AvailableBalance",
            "AvailableAmount",
            "AvailableCashAmount",
            "Balance",
            "CashBalance",
            "AccountBalance",
            "RemainingAmount",
        ),
    )
    ok = status_code == 200 and isinstance(payload, dict) and not response_error and balance is not None
    item: Dict[str, Any] = {
        "provider": "tos",
        "name": "Volcengine TOS / Account Balance",
        "ok": ok,
        "configured": True,
        "url": url,
        "action": action,
        "version": version,
        "service": service,
        "region": region,
        "bucket": bucket,
        "token_env": "TOS_CONFIG" if not os.environ.get("VOLCENGINE_BILLING_ACCESS_KEY") else "VOLCENGINE_BILLING_ACCESS_KEY",
        "token_mask": _mask_token(ak),
        "status_code": status_code,
        "request_id": response_meta.get("RequestId", "") if isinstance(response_meta, dict) else "",
        "checked_at": checked_at,
        "balance_unit": "CNY",
    }
    if balance is not None:
        item["balance"] = balance
        item["balance_field"] = balance_key
        item["raw_balance"] = raw_balance
    if not ok:
        message = ""
        if response_error:
            message = str(response_error.get("Message") or response_error.get("Code") or response_error)
            item["error_code"] = response_error.get("Code")
        else:
            message = text or "Balance field not found"
        item["error"] = message[:500]
        item["hint"] = "TOS 上传权限不等于费用中心读权限；若此项报 AccessDenied，请给该 AK 增加费用中心/账户余额只读权限，或单独配置 VOLCENGINE_BILLING_* 查询账号。"
    return item


async def query_ihuyi_sms_balance(checked_at: str) -> Dict[str, Any]:
    account = (os.environ.get("IHUYI_SMS_ACCOUNT") or "").strip()
    password = (os.environ.get("IHUYI_SMS_PASSWORD") or "").strip()
    base = _clean_base(os.environ.get("IHUYI_SMS_BALANCE_API_BASE") or "", "http://106.ihuyi.com")
    url = base + "/webservice/sms.php?method=GetNum"
    if not account or not password:
        return _provider_error(
            provider="sms_ihuyi",
            name="Ihuyi SMS",
            configured=False,
            message="Missing IHUYI_SMS_ACCOUNT/IHUYI_SMS_PASSWORD",
            checked_at=checked_at,
            extra={"url": url},
        )
    try:
        status_code, payload, text = await _post_form(
            url,
            {"account": account, "password": password, "format": "json"},
            {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json,text/plain,*/*"},
        )
    except Exception as exc:
        return _provider_error(
            provider="sms_ihuyi",
            name="Ihuyi SMS",
            configured=True,
            message=f"Request failed: {type(exc).__name__}: {exc}",
            checked_at=checked_at,
            extra={"url": url, "account_mask": _mask_token(account)},
        )
    code = (payload or {}).get("code") if isinstance(payload, dict) else None
    msg = str((payload or {}).get("msg") or (payload or {}).get("message") or text or "")
    num = (payload or {}).get("num") if isinstance(payload, dict) else None
    balance = _to_float(num)
    ok = status_code == 200 and str(code) == "2" and balance is not None
    item: Dict[str, Any] = {
        "provider": "sms_ihuyi",
        "name": "Ihuyi SMS",
        "ok": ok,
        "configured": True,
        "url": url,
        "account_mask": _mask_token(account),
        "status_code": status_code,
        "code": code,
        "message": msg,
        "checked_at": checked_at,
        "balance_unit": "sms_count",
    }
    if balance is not None:
        item["balance"] = balance
    if not ok:
        item["error"] = msg or f"HTTP {status_code}"
    return item


async def query_aliyun_account_balance(checked_at: str) -> Dict[str, Any]:
    ak = (os.environ.get("ALIYUN_SMS_ACCESS_KEY_ID") or os.environ.get("ALIYUN_ACCESS_KEY_ID") or "").strip()
    sk = (os.environ.get("ALIYUN_SMS_ACCESS_KEY_SECRET") or os.environ.get("ALIYUN_ACCESS_KEY_SECRET") or "").strip()
    base = _clean_base(os.environ.get("ALIYUN_BSS_API_BASE") or "", "https://business.aliyuncs.com")
    if not ak or not sk:
        return _provider_error(
            provider="sms_aliyun",
            name="Aliyun SMS / Account Balance",
            configured=False,
            message="Missing ALIYUN_SMS_ACCESS_KEY_ID/ALIYUN_SMS_ACCESS_KEY_SECRET",
            checked_at=checked_at,
            extra={"url": base + "/"},
        )
    params = {
        "Action": "QueryAccountBalance",
        "Version": "2017-12-14",
        "Format": "JSON",
        "AccessKeyId": ak,
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "SignatureVersion": "1.0",
        "SignatureNonce": uuid.uuid4().hex,
    }
    try:
        status_code, payload, text, url = await _aliyun_rpc_get_json(base=base, params=params, access_key_secret=sk)
    except Exception as exc:
        return _provider_error(
            provider="sms_aliyun",
            name="Aliyun SMS / Account Balance",
            configured=True,
            message=f"Request failed: {type(exc).__name__}: {exc}",
            checked_at=checked_at,
            extra={"url": base + "/", "token_mask": _mask_token(ak)},
        )
    data = payload.get("Data") if isinstance(payload, dict) and isinstance(payload.get("Data"), dict) else payload
    balance, balance_key, raw_balance = _find_numeric_field(
        data,
        (
            "AvailableAmount",
            "AvailableCashAmount",
            "AccountBalance",
            "BalanceAmount",
            "CashAmount",
            "AvailableCredit",
        ),
    )
    success = bool(payload.get("Success") is True) if isinstance(payload, dict) and "Success" in payload else status_code == 200 and balance is not None
    item: Dict[str, Any] = {
        "provider": "sms_aliyun",
        "name": "Aliyun SMS / Account Balance",
        "ok": status_code == 200 and success and balance is not None,
        "configured": True,
        "url": url,
        "token_env": "ALIYUN_SMS_ACCESS_KEY_ID",
        "token_mask": _mask_token(ak),
        "status_code": status_code,
        "request_id": (payload or {}).get("RequestId", "") if isinstance(payload, dict) else "",
        "checked_at": checked_at,
        "balance_unit": "CNY",
    }
    if balance is not None:
        item["balance"] = balance
        item["balance_field"] = balance_key
        item["raw_balance"] = raw_balance
    if not item["ok"]:
        err = ""
        if isinstance(payload, dict):
            err = str(payload.get("Message") or payload.get("Code") or payload)
            item["error_code"] = payload.get("Code")
        item["error"] = (err or text or f"HTTP {status_code}")[:500]
        item["hint"] = "阿里云短信没有独立条数余额口径，这里查询的是阿里云账号可用余额；若报权限错误，请给短信 AK 增加 BSS OpenAPI QueryAccountBalance 只读权限。"
    return item


async def query_sms_balances(checked_at: str) -> list[Dict[str, Any]]:
    results: list[Dict[str, Any]] = []
    use_aliyun = bool((os.environ.get("ALIYUN_SMS_ACCESS_KEY_ID") or "").strip() and (os.environ.get("ALIYUN_SMS_ACCESS_KEY_SECRET") or "").strip())
    use_ihuyi = bool((os.environ.get("IHUYI_SMS_ACCOUNT") or "").strip() and (os.environ.get("IHUYI_SMS_PASSWORD") or "").strip())
    if use_aliyun:
        results.append(await query_aliyun_account_balance(checked_at))
    if use_ihuyi:
        results.append(await query_ihuyi_sms_balance(checked_at))
    if not results:
        results.append(
            _provider_error(
                provider="sms",
                name="SMS Channel",
                configured=False,
                message="Missing SMS channel config: ALIYUN_SMS_* or IHUYI_SMS_*",
                checked_at=checked_at,
            )
        )
    return results


async def collect_provider_balances() -> Dict[str, Any]:
    checked_at = _now_iso()
    results = [
        await query_hifly_credit(checked_at),
        await query_comfly_credit(checked_at),
        await query_yunwu_credit(checked_at),
        await query_openmind_credit(checked_at),
        await query_tos_account_balance(checked_at),
    ]
    results.extend(await query_sms_balances(checked_at))
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
