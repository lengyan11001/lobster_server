"""知识型发现能力：平台算法机制 / 跨平台内容差异。

数据来源：ZJU-REAL/Easel（Apache-2.0）的同名 references 文档，
本地离线读取，不依赖网络。用于回答"这个平台喜欢什么""同一条内容各平台怎么改"。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA = Path(__file__).with_name("data") / "knowledge"

DOCS = {
    "algorithm": {"file": "algorithm-vocabulary.md", "title": "平台算法机制"},
    "sources": {"file": "platform-sources.md", "title": "平台官方公告来源"},
    "traits": {"file": "platform-traits.md", "title": "跨平台内容差异"},
}

_cache: Dict[str, List[Dict[str, str]]] = {}


def _split_sections(text: str, doc: str) -> List[Dict[str, str]]:
    sections: List[Dict[str, str]] = []
    current_title = ""
    buffer: List[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current_title or buffer:
                sections.append({"doc": doc, "title": current_title or doc, "body": "\n".join(buffer).strip()})
            current_title = line[3:].strip()
            buffer = []
        elif line.startswith("# "):
            continue
        else:
            buffer.append(line)
    if current_title or buffer:
        sections.append({"doc": doc, "title": current_title or doc, "body": "\n".join(buffer).strip()})
    return [s for s in sections if s["body"]]


def load() -> Dict[str, List[Dict[str, str]]]:
    if _cache:
        return _cache
    for key, meta in DOCS.items():
        path = DATA / meta["file"]
        if not path.is_file():
            _cache[key] = []
            continue
        _cache[key] = _split_sections(path.read_text(encoding="utf-8"), key)
    return _cache


def search(query: str = "", *, platform: str = "", doc: str = "", limit: int = 5) -> List[Dict[str, str]]:
    words = [w for w in re.split(r"\s+", (query or "").strip()) if w]
    hits: List[Dict[str, str]] = []
    data = load()
    keys = [doc] if doc in data else list(data)
    for key in keys:
        for section in data.get(key) or []:
            hay = f"{section['title']} {section['body']}"
            if platform and platform not in hay:
                continue
            if words and not all(w in hay for w in words):
                continue
            hits.append({"doc": key, "title": section["title"], "snippet": section["body"][:800]})
            if len(hits) >= max(1, limit):
                return hits
    return hits


def format_markdown(items: List[Dict[str, str]], *, heading: str) -> str:
    lines = [f"# {heading}", ""]
    if not items:
        lines.append("知识库里没有匹配条目，可换关键词或平台名。")
        return "\n".join(lines)
    for item in items:
        lines.append(f"## {item['title']}")
        lines.append(item["snippet"])
        lines.append("")
    return "\n".join(lines)


def selftest() -> List[str]:
    problems: List[str] = []
    data = load()
    for key in DOCS:
        if not data.get(key):
            problems.append(f"doc {key} empty")
    if not search("推荐", limit=3):
        problems.append("search '推荐' returns nothing")
    if not search(platform="小红书", limit=3):
        problems.append("search platform 小红书 returns nothing")
    return problems
