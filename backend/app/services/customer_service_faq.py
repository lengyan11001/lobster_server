"""客服模式知识库：选了「客服」就把百问百答整篇交给 LLM 去回答（不做问题判断/隔离）。

- 知识库文件：backend/app/data/customer-service-faq.md
- 注入方式：把 MD 拼到用户消息前面（带起止标记），读接口会把这段再剥掉，避免脏历史。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, Tuple

_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[float, str]] = {}

FAQ_PATH = Path(__file__).resolve().parents[1] / "data" / "customer-service-faq.md"

FAQ_MARK_START = "【客服知识库·百问百答（系统注入，请直接依据它回答用户问题；知识库里没有的就说“我需要确认后再回复您”）】"
FAQ_MARK_END = "【客服知识库结束】"


def _read_cached(path: Path) -> str:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    key = str(path)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    with _LOCK:
        _CACHE[key] = (mtime, text)
    return text


def customer_service_faq_text() -> str:
    return _read_cached(FAQ_PATH).strip()


def with_customer_service_faq(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return message
    faq = customer_service_faq_text()
    if not faq:
        return message
    return f"{FAQ_MARK_START}\n{faq}\n{FAQ_MARK_END}\n\n用户问题：{text}"


def strip_customer_service_faq(value: str) -> str:
    text = str(value or "")
    start = text.find(FAQ_MARK_START)
    if start < 0:
        return text
    end = text.find(FAQ_MARK_END, start)
    if end < 0:
        return text
    tail = text[end + len(FAQ_MARK_END):]
    tail = tail.lstrip()
    if tail.startswith("用户问题："):
        tail = tail[len("用户问题："):]
    return tail.strip()
