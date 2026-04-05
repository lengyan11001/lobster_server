"""服务器侧速推 Token：仅 bihuo / yingshi 两池；无品牌或非这两类不提供 Token（无 USER 兜底）。

环境变量：
- 必火：SUTUI_SERVER_TOKENS_BIHUO、SUTUI_SERVER_TOKEN_BIHUO
- 影视：SUTUI_SERVER_TOKENS_YINGSHI、SUTUI_SERVER_TOKEN_YINGSHI
- 兼容（仅站内 LLM 探测等 internal 路径）：SUTUI_SERVER_TOKENS、SUTUI_SERVER_TOKEN、sutui_config.json

不再读取 SUTUI_SERVER_TOKENS_USER（无品牌用户不允许走速推）。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import List, Optional, Tuple

_sutui_token_lock = asyncio.Lock()
_sutui_pool_index: dict[str, int] = {}


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


def _tokens_and_pool_key_user(*, brand_mark: Optional[str]) -> Tuple[str, List[str]]:
    """终端用户：仅 bihuo / yingshi；池为空则无 Token，不使用 USER/legacy 兜底。"""
    b = (brand_mark or "").strip().lower()
    if b == "bihuo":
        return "bihuo", get_sutui_tokens_list_bihuo()
    if b == "yingshi":
        return "yingshi", get_sutui_tokens_list_yingshi()
    return "none", []


def _internal_probe_token_list() -> List[str]:
    """站内探测 mcp/models 等：优先 bihuo → yingshi → 兼容 legacy，不用 USER 池。"""
    for lst in (get_sutui_tokens_list_bihuo(), get_sutui_tokens_list_yingshi(), _legacy_sutui_tokens_list()):
        if lst:
            return lst
    return []


async def next_sutui_server_token(*, brand_mark: Optional[str] = None) -> Optional[str]:
    """终端请求：brand_mark 必须为 bihuo/yingshi 且对应池已配置。"""
    pool_key, lst = _tokens_and_pool_key_user(brand_mark=brand_mark)
    if not lst:
        return None
    async with _sutui_token_lock:
        idx = _sutui_pool_index.get(pool_key, 0) % len(lst)
        _sutui_pool_index[pool_key] = idx + 1
        return lst[idx]


async def next_sutui_server_token_internal() -> Optional[str]:
    """站内 LLM 列表/探测：不绑定终端用户品牌，仅从已配置的品牌池或 legacy 取 Token。"""
    lst = _internal_probe_token_list()
    if not lst:
        return None
    pool_key = "internal"
    async with _sutui_token_lock:
        idx = _sutui_pool_index.get(pool_key, 0) % len(lst)
        _sutui_pool_index[pool_key] = idx + 1
        return lst[idx]
