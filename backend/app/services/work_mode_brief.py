"""工作模式：先把「简要目录」预注入给模型，省掉“先调工具问目录”的那一轮（不灰度，默认开）。

用户口径（2026-10-01）：
- 工作模式参照客服模式：先给 LLM 简要信息（目录），需要细节时它再按需要；
- 目标是提速（少一次模型往返）；
- 不用灰度，直接开；需要时用 WORK_MODE_BRIEF_CONTEXT=0 关掉。

数据来源（都在服务端，免额外往返）：
- 能力目录：capability_configs（enabled）
- 记忆文件目录：openclaw_memory_documents（该用户 active 的标题/编号）

注入内容只在“给模型的消息”里；读接口（列表/历史）会剥掉这段，避免脏历史。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Dict, List, Optional, Tuple

WORK_BRIEF_START = (
    "【工作模式·预取简要目录（系统注入，用于快速判断：需要完整参数/正文时再调 "
    "listSystemCapabilities / readPersonalMemoryDocument，不要重复拉目录）】"
)
WORK_BRIEF_END = "【简要目录结束】"

_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[float, List[str]]] = {}
_CACHE_TTL_SECONDS = 120.0
_MAX_ITEMS = 40
_MEMORY_LIMIT = 30


def _enabled() -> bool:
    raw = (os.environ.get("WORK_MODE_BRIEF_CONTEXT") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def _capability_lines(db) -> List[str]:
    key = "capabilities"
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return list(cached[1])
    lines: List[str] = []
    try:
        from ..models import CapabilityConfig

        rows = (
            db.query(CapabilityConfig)
            .filter(CapabilityConfig.enabled.is_(True))
            .order_by(CapabilityConfig.capability_id.asc())
            .limit(200)
            .all()
        )
        for row in rows:
            cid = str(getattr(row, "capability_id", "") or "").strip()
            desc = str(getattr(row, "description", "") or "").strip()
            if not cid:
                continue
            lines.append(f"- {cid}：{desc[:80]}" if desc else f"- {cid}")
    except Exception:
        lines = []
    lines = lines[:_MAX_ITEMS]
    with _CACHE_LOCK:
        _CACHE[key] = (now, list(lines))
    return lines


def _memory_lines(db, user_id: int, installation_id: str = "") -> List[str]:
    lines: List[str] = []
    try:
        from ..models import OpenClawMemoryDocument

        query = db.query(OpenClawMemoryDocument).filter(
            OpenClawMemoryDocument.target_user_id == int(user_id),
            OpenClawMemoryDocument.status == "active",
        )
        install = str(installation_id or "").strip()
        rows = []
        if install:
            # H5 的 installation_id 可能和上传记忆时的设备不同：先按设备过滤，查不到就退回该用户全部记忆
            rows = (
                query.filter(OpenClawMemoryDocument.installation_id == install)
                .order_by(OpenClawMemoryDocument.updated_at.desc())
                .limit(_MEMORY_LIMIT)
                .all()
            )
        if not rows:
            rows = query.order_by(OpenClawMemoryDocument.updated_at.desc()).limit(_MEMORY_LIMIT).all()
        for row in rows:
            doc_id = str(getattr(row, "doc_id", "") or "").strip()
            title = str(getattr(row, "title", "") or "").strip()
            if not doc_id:
                continue
            lines.append(f"- {doc_id}：{title[:60]}" if title else f"- {doc_id}")
    except Exception:
        lines = []
    return lines


def build_brief_block(db, user_id: int, installation_id: str = "") -> str:
    if not _enabled():
        return ""
    caps = _capability_lines(db)
    memories = _memory_lines(db, user_id, installation_id)
    if not caps and not memories:
        return ""
    blocks: List[str] = [WORK_BRIEF_START]
    if caps:
        blocks.append("【可用能力目录】\n" + "\n".join(caps))
    if memories:
        blocks.append("【我的记忆文件目录（要正文请按 doc_id 调 readPersonalMemoryDocument）】\n" + "\n".join(memories))
    blocks.append(
        "【用法】先用上面的目录判断该用哪个能力/哪份记忆；确实需要细节时再按需调用对应工具取，"
        "不要为了拿目录重复调用 listSystemCapabilities / listPersonalMemoryDocuments。"
    )
    blocks.append(WORK_BRIEF_END)
    return "\n\n".join(blocks)


def maybe_prepend_brief(content: str, db, user_id: int, installation_id: str = "") -> str:
    text = str(content or "")
    if not text.strip():
        return content
    if WORK_BRIEF_START in text:
        return content
    block = build_brief_block(db, user_id, installation_id)
    if not block:
        return content
    return block + "\n\n" + text


def strip_work_brief(value: str) -> str:
    text = str(value or "")
    start = text.find(WORK_BRIEF_START)
    if start < 0:
        return text
    end = text.find(WORK_BRIEF_END, start)
    if end < 0:
        return text
    before = text[:start].strip()
    after = text[end + len(WORK_BRIEF_END):].strip()
    return (before + "\n\n" + after).strip() if before else after
