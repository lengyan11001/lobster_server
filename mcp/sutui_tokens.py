"""服务器侧速推 Token 双池（管理员 / 普通用户）轮询负载均衡。

环境变量（显式池优先，否则回退到既有 SUTUI_SERVER_TOKENS / SUTUI_SERVER_TOKEN / sutui_config.json）：
- 用户池：SUTUI_SERVER_TOKENS_USER、SUTUI_SERVER_TOKEN_USER
- 管理员池：SUTUI_SERVER_TOKENS_ADMIN、SUTUI_SERVER_TOKEN_ADMIN
- 兼容：SUTUI_SERVER_TOKENS、SUTUI_SERVER_TOKEN、sutui_config.json
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import List, Optional

_sutui_tokens_list_user: List[str] = []
_sutui_token_index_user = 0
_sutui_tokens_list_admin: List[str] = []
_sutui_token_index_admin = 0
_sutui_token_lock = asyncio.Lock()


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


def get_sutui_tokens_list_user() -> List[str]:
    u = _parse_pool("SUTUI_SERVER_TOKENS_USER", "SUTUI_SERVER_TOKEN_USER")
    if u:
        return u
    return _legacy_sutui_tokens_list()


def get_sutui_tokens_list_admin() -> List[str]:
    a = _parse_pool("SUTUI_SERVER_TOKENS_ADMIN", "SUTUI_SERVER_TOKEN_ADMIN")
    if a:
        return a
    return _legacy_sutui_tokens_list()


async def next_sutui_server_token(*, is_admin: bool) -> Optional[str]:
    """从对应池中轮询取下一条 Token。"""
    global _sutui_tokens_list_user, _sutui_token_index_user
    global _sutui_tokens_list_admin, _sutui_token_index_admin
    if is_admin:
        if not _sutui_tokens_list_admin:
            _sutui_tokens_list_admin = get_sutui_tokens_list_admin()
        lst = _sutui_tokens_list_admin
        if not lst:
            return None
        async with _sutui_token_lock:
            idx = _sutui_token_index_admin % len(lst)
            _sutui_token_index_admin += 1
            return lst[idx]
    if not _sutui_tokens_list_user:
        _sutui_tokens_list_user = get_sutui_tokens_list_user()
    lst = _sutui_tokens_list_user
    if not lst:
        return None
    async with _sutui_token_lock:
        idx = _sutui_token_index_user % len(lst)
        _sutui_token_index_user += 1
        return lst[idx]
