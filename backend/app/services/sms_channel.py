"""Resolve SMS provider settings for an OEM without exposing credentials."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class AliyunSmsChannel:
    access_key_id: str
    access_key_secret: str
    sign_name: str
    template_code: str
    brand_specific: bool = False

    @property
    def ready(self) -> bool:
        return bool(
            self.access_key_id
            and self.access_key_secret
            and self.sign_name
            and self.template_code
        )


def _value(source: Mapping[str, Any], *names: str) -> str:
    for name in names:
        raw = source.get(name)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return ""


def _brand_mark(raw: Optional[str]) -> str:
    return str(raw or "").strip().lower()


def _json_brand_config(settings_obj: Any, mark: str) -> tuple[bool, Mapping[str, Any]]:
    raw = getattr(settings_obj, "aliyun_sms_brand_channels_json", None)
    if not raw:
        return False, {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return False, {}
    if not isinstance(parsed, Mapping):
        return False, {}
    for key, value in parsed.items():
        if str(key).strip().lower() == mark:
            return True, value if isinstance(value, Mapping) else {}
    return False, {}


def resolve_aliyun_sms_channel(brand_mark: Optional[str], settings_obj: Any) -> AliyunSmsChannel:
    """Resolve a brand channel without falling back once a brand is configured."""
    mark = _brand_mark(brand_mark)
    settings_values = vars(settings_obj)
    global_values = {
        "access_key_id": _value(settings_values, "aliyun_sms_access_key_id"),
        "access_key_secret": _value(settings_values, "aliyun_sms_access_key_secret"),
        "sign_name": _value(settings_values, "aliyun_sms_sign_name") or "深圳市必火智能信息技术",
        "template_code": _value(settings_values, "aliyun_sms_template_code") or "SMS_333406023",
    }
    json_configured, configured = _json_brand_config(settings_obj, mark)
    env_prefix = f"ALIYUN_SMS_{mark.upper()}_" if mark else ""
    brand_env = {
        key: (os.environ.get(env_prefix + key.upper()) or "").strip()
        for key in global_values
    } if env_prefix else {}
    brand_specific = json_configured or any(brand_env.values())
    base = {key: "" for key in global_values} if brand_specific else dict(global_values)
    for key in base:
        value = _value(configured, key, key.upper())
        if value:
            base[key] = value
    for key, value in brand_env.items():
        if value:
            base[key] = value
    return AliyunSmsChannel(**base, brand_specific=brand_specific)
