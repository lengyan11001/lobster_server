"""Server-side Sutui token pool shared by every OEM brand.

User-facing requests intentionally ignore ``brand_mark`` and use one shared
server token pool.  Legacy branded env names are still accepted as a startup
compatibility fallback, but they no longer create brand-specific routing.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_sutui_token_lock = asyncio.Lock()
_sutui_pool_index: dict[str, int] = {}
_BRAND_OR_POOL_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _load_sutui_token_from_file() -> str:
    try:
        p = Path(__file__).resolve().parent.parent / "sutui_config.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return (data.get("token") or "").strip()
    except Exception:
        pass
    return ""


def _legacy_sutui_tokens_list() -> List[str]:
    raw = os.environ.get("SUTUI_SERVER_TOKENS", "").strip()
    if raw:
        tokens = [t.strip() for t in raw.split(",") if t.strip()]
        if tokens:
            return tokens
    single = os.environ.get("SUTUI_SERVER_TOKEN", "").strip()
    if single:
        return [single]
    from_file = _load_sutui_token_from_file()
    if from_file:
        return [from_file]
    return []


def _parse_pool(comma_key: str, single_key: str) -> List[str]:
    raw = os.environ.get(comma_key, "").strip()
    if raw:
        tokens = [t.strip() for t in raw.split(",") if t.strip()]
        if tokens:
            return tokens
    single = os.environ.get(single_key, "").strip()
    if single:
        return [single]
    return []


def get_sutui_tokens_list_bihuo() -> List[str]:
    return _parse_pool("SUTUI_SERVER_TOKENS_BIHUO", "SUTUI_SERVER_TOKEN_BIHUO")


def get_sutui_tokens_list_yingshi() -> List[str]:
    return _parse_pool("SUTUI_SERVER_TOKENS_YINGSHI", "SUTUI_SERVER_TOKEN_YINGSHI")


def _normalized_brand_or_pool(raw: Optional[str]) -> str:
    value = str(raw or "").strip().lower().replace("-", "_")
    return value if _BRAND_OR_POOL_RE.fullmatch(value) else ""


def _configured_brand_pool_map() -> Dict[str, str]:
    raw = os.environ.get("SUTUI_BRAND_POOL_MAP", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    result: Dict[str, str] = {}
    for brand, pool in data.items():
        brand_key = _normalized_brand_or_pool(str(brand))
        pool_key = _normalized_brand_or_pool(str(pool))
        if brand_key and pool_key:
            result[brand_key] = pool_key
    return result


def _tokens_for_pool(pool_key: str) -> List[str]:
    key = _normalized_brand_or_pool(pool_key)
    if not key:
        return []
    suffix = key.upper()
    return _parse_pool(f"SUTUI_SERVER_TOKENS_{suffix}", f"SUTUI_SERVER_TOKEN_{suffix}")


def _shared_user_pool_and_list() -> Tuple[str, List[str]]:
    shared = _legacy_sutui_tokens_list()
    if shared:
        return "shared", shared
    # Compatibility for already deployed servers that only configured the old
    # branded env names.  The brand passed by the caller is ignored.
    for pk, lst in (
        ("bihuo", get_sutui_tokens_list_bihuo()),
        ("yingshi", get_sutui_tokens_list_yingshi()),
    ):
        if lst:
            return pk, lst
    return "none", []


def sutui_pool_key_for_brand(brand_mark: Optional[str]) -> str:
    """Return the effective shared Sutui pool; ``brand_mark`` is ignored."""
    pool_key, _ = _shared_user_pool_and_list()
    return pool_key


def sutui_token_ref_from_secret(token: Optional[str]) -> str:
    """对账用：完整 sk 的短 SHA256 前缀（不可逆），勿写入日志明文。"""
    t = (token or "").strip()
    if not t:
        return ""
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:12]


def sutui_token_recon_meta(token: Optional[str], pool_key: str) -> Dict[str, Any]:
    """写入 credit_ledger.meta['_recon']；仅站内对账，勿对用户端展示。"""
    ref = sutui_token_ref_from_secret(token)
    pk = (pool_key or "").strip() or "unknown"
    if not ref:
        return {}
    return {"_recon": {"sutui_pool": pk, "sutui_token_ref": ref}}


def _tokens_and_pool_key_user(*, brand_mark: Optional[str]) -> Tuple[str, List[str]]:
    """Resolve the shared user-facing token pool; ``brand_mark`` is ignored."""
    return _shared_user_pool_and_list()


def _internal_probe_pool_and_list() -> Tuple[str, List[str]]:
    """站内探测：优先 bihuo → yingshi → legacy；返回 (池名, token 列表)。"""
    for pk, lst in (
        ("bihuo", get_sutui_tokens_list_bihuo()),
        ("yingshi", get_sutui_tokens_list_yingshi()),
        ("legacy", _legacy_sutui_tokens_list()),
    ):
        if lst:
            return pk, lst
    return "none", []


def _internal_probe_token_list() -> List[str]:
    """兼容旧调用方：仅返回第一个非空列表。"""
    _, lst = _internal_probe_pool_and_list()
    return lst


async def next_sutui_server_token_with_pool(*, brand_mark: Optional[str] = None) -> Tuple[Optional[str], str]:
    """Return ``(token, physical_pool)`` for any valid OEM brand."""
    pool_key, lst = _tokens_and_pool_key_user(brand_mark=brand_mark)
    if not lst:
        return None, pool_key
    return lst[0], pool_key


async def next_sutui_server_token(*, brand_mark: Optional[str] = None) -> Optional[str]:
    """Return a token from the brand's resolved physical pool."""
    t, _ = await next_sutui_server_token_with_pool(brand_mark=brand_mark)
    return t


async def next_sutui_server_token_internal_with_pool() -> Tuple[Optional[str], str]:
    """站内 LLM 列表/探测：返回 (token, bihuo|yingshi|legacy|none)。"""
    picked_key, lst = _internal_probe_pool_and_list()
    if not lst:
        return None, picked_key
    lock_key = f"internal::{picked_key}"
    async with _sutui_token_lock:
        idx = _sutui_pool_index.get(lock_key, 0) % len(lst)
        _sutui_pool_index[lock_key] = idx + 1
        return lst[idx], picked_key


async def next_sutui_server_token_internal() -> Optional[str]:
    """站内 LLM 列表/探测：不绑定终端用户品牌，仅从已配置的品牌池或 legacy 取 Token。"""
    t, _ = await next_sutui_server_token_internal_with_pool()
    return t
