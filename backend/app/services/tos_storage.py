"""TOS 配置解析：国内走广州桶，海外实例自动走雅加达桶。

判定顺序（任一命中即海外）：
1. 环境变量 LOBSTER_SERVER_REGION=overseas / global / intl；
2. PUBLIC_BASE_URL 指向 bhos.online；
3. 存在 TOS_CONFIG_OVERSEAS 且显式 LOBSTER_SERVER_REGION 未设为国内。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parents[3]
_CUSTOM_CONFIGS_FILE = _ROOT / "custom_configs.json"

_OVERSEAS_FLAGS = {"overseas", "global", "intl", "international", "海外"}
_DOMESTIC_FLAGS = {"cn", "china", "domestic", "mainland", "国内"}


@lru_cache(maxsize=1)
def _configs() -> dict:
    if not _CUSTOM_CONFIGS_FILE.exists():
        return {}
    try:
        data = json.loads(_CUSTOM_CONFIGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def reload_configs() -> None:
    _configs.cache_clear()


def _env_value(name: str) -> str:
    """os.environ 优先；后台进程并未把 .env 注入 environ，因此再读一次根目录 .env。"""
    value = str(os.environ.get(name) or "").strip()
    if value:
        return value
    env_file = _ROOT / ".env"
    if not env_file.is_file():
        return ""
    try:
        for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, item = line.split("=", 1)
            if key.strip() == name:
                return item.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _configured_public_base() -> str:
    value = _env_value("PUBLIC_BASE_URL")
    if value:
        return value
    try:
        from ..core.config import settings

        return str(getattr(settings, "public_base_url", "") or "").strip()
    except Exception:
        return ""


def is_overseas_server() -> bool:
    flag = _env_value("LOBSTER_SERVER_REGION").lower()
    if flag in _OVERSEAS_FLAGS:
        return True
    if flag in _DOMESTIC_FLAGS:
        return False
    return "bhos.online" in _configured_public_base().lower()


def _valid(cfg: Any) -> bool:
    if not isinstance(cfg, dict):
        return False
    return bool(str(cfg.get("access_key") or "").strip() and str(cfg.get("secret_key") or "").strip())


def get_tos_config(prefer: str = "") -> Optional[dict]:
    cfgs = _configs().get("configs") or {}
    want_overseas = prefer == "overseas" or (not prefer and is_overseas_server())
    if want_overseas:
        cfg = cfgs.get("TOS_CONFIG_OVERSEAS")
        if _valid(cfg):
            return cfg
    cfg = cfgs.get("TOS_CONFIG")
    return cfg if _valid(cfg) else None


def get_tos_config_domestic() -> Optional[dict]:
    cfg = (_configs().get("configs") or {}).get("TOS_CONFIG")
    return cfg if _valid(cfg) else None


def tos_public_base(prefer: str = "") -> str:
    cfg = get_tos_config(prefer)
    if not cfg:
        return ""
    return str(cfg.get("public_domain") or "").strip().rstrip("/")


def tos_url(path: str, prefer: str = "") -> str:
    base = tos_public_base(prefer)
    rel = str(path or "").lstrip("/")
    return f"{base}/{rel}" if base and rel else base


def rewrite_tos_url(url: str, prefer: str = "") -> str:
    """把历史广州桶地址改写成当前地域的公网地址；非 TOS 地址原样返回。"""
    raw = str(url or "").strip()
    base = tos_public_base(prefer)
    if not raw or not base:
        return raw
    markers = (
        "https://lobster-online-assets-2103871705.tos-cn-guangzhou.volces.com",
        "https://lobster-assets-jakarta-2103871705.tos-ap-southeast-3.volces.com",
    )
    for marker in markers:
        if raw.startswith(marker):
            return base + raw[len(marker):]
    return raw
