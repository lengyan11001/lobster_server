"""客服模式知识库：按用户问题从「客服百问百答」里挑相关条目交给 LLM 回答。

设计取舍（2026-10-01）：整篇 20KB 直接塞进一条消息会被下游截断（实测模型只看到开头
「账号与登录」那一段），所以改成：
1) 把用户问题放最前面（即使被截断也先保住问题）；
2) 按关键词从 MD 里检索最相关的若干条（含答案）拼进去；
3) 再附一份紧凑目录（章节 + 问题标题），让模型知道知识库里还有什么；
4) 检索不到就说“我需要确认后再回复您”，不编造。
完整 MD 仍保留在文件里（backend/app/data/customer-service-faq.md），客户端也带一份。
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Dict, List, Tuple

_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[float, str]] = {}
_ENTRY_CACHE: Dict[str, Tuple[float, List[Dict[str, str]], str]] = {}

FAQ_PATH = Path(__file__).resolve().parents[1] / "data" / "customer-service-faq.md"

FAQ_MARK_START = "【客服知识库·百问百答（系统注入，请优先依据下面命中的条目回答；没有的直接说“我需要确认后再回复您”，不要编造）】"
FAQ_MARK_END = "【客服知识库结束】"

_MAX_ENTRIES = 8
_MAX_ENTRY_CHARS = 420
_QUESTION_PREFIX = "用户问题："

_STOP_TOKENS = {
    "怎么", "如何", "如何用", "什么", "哪些", "可以", "能不能", "是否", "有没有", "的话", "吗", "呢", "吧",
    "我们", "你们", "他们", "这个", "那个", "一下", "一个", "就是", "现在", "然后", "以及", "还是", "或者",
    "为什么", "在哪", "哪里", "多少", "我要", "我想", "帮我", "请问", "你好", "谢谢",
}


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


def _parse_entries(md: str) -> Tuple[List[Dict[str, str]], str]:
    entries: List[Dict[str, str]] = []
    sections: List[Tuple[str, List[str]]] = []
    current_section = ""
    current_titles: List[str] = []
    lines = md.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped[3:].strip()
            if current_titles:
                sections.append((current_section, current_titles))
                current_titles = []
            idx += 1
            continue
        match = re.match(r"^\*\*(Q\d+)\s*[:：]\s*(.+?)\*\*\s*$", stripped)
        if match:
            qid, title = match.group(1), match.group(2).strip()
            body_lines: List[str] = []
            idx += 1
            while idx < len(lines):
                nxt = lines[idx].strip()
                if re.match(r"^\*\*Q\d+\s*[:：]", nxt) or nxt.startswith("## "):
                    break
                if nxt:
                    body_lines.append(nxt)
                idx += 1
            body = " ".join(body_lines)
            body = re.sub(r"^A\s*[:：]\s*", "", body).strip()
            entries.append(
                {
                    "id": qid,
                    "title": title,
                    "section": current_section,
                    "body": body,
                }
            )
            current_titles.append(f"{qid} {title}")
            continue
        idx += 1
    if current_titles:
        sections.append((current_section, current_titles))
    index_lines: List[str] = []
    for name, titles in sections:
        index_lines.append(f"- {name}：" + "；".join(titles))
    return entries, "\n".join(index_lines)


def _data() -> Tuple[List[Dict[str, str]], str]:
    md = customer_service_faq_text()
    if not md:
        return [], ""
    key = str(FAQ_PATH)
    try:
        mtime = FAQ_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0
    with _LOCK:
        cached = _ENTRY_CACHE.get(key)
        if cached and cached[0] == mtime:
            return cached[1], cached[2]
    entries, index = _parse_entries(md)
    with _LOCK:
        _ENTRY_CACHE[key] = (mtime, entries, index)
    return entries, index


def _tokens(text: str) -> List[str]:
    raw = re.findall(r"[A-Za-z0-9_.\-]+|[\u4e00-\u9fff]+", str(text or "").lower())
    out: List[str] = []
    for chunk in raw:
        if re.fullmatch(r"[a-z0-9_.\-]+", chunk):
            if len(chunk) >= 2:
                out.append(chunk)
            continue
        for i in range(len(chunk) - 1):
            bigram = chunk[i : i + 2]
            if bigram in _STOP_TOKENS:
                continue
            out.append(bigram)
        if len(chunk) == 1:
            out.append(chunk)
    return out


def retrieve_faq_entries(query: str, limit: int = _MAX_ENTRIES) -> List[Dict[str, str]]:
    question_tokens = _tokens(query)
    if not question_tokens:
        return []
    unique = list(dict.fromkeys(question_tokens))
    query_text = str(query or "")
    entries, _index = _data()
    wants_image = any(word in query_text for word in ("图片", "图", "作图", "画图", "出图"))
    wants_video = any(word in query_text for word in ("视频", "短片", "成片", "出片"))
    is_howto = any(word in query_text for word in ("怎么", "如何", "步骤", "在哪", "入口", "操作", "打不开", "失败", "报错"))
    scored: List[Tuple[float, int, Dict[str, str]]] = []
    for order, entry in enumerate(entries):
        title = entry.get("title") or ""
        body = entry.get("body") or ""
        text_all = title + " " + body
        score = 0.0
        for token in unique:
            if token in title:
                score += 3.0
            elif token in body:
                score += 1.0
        # 题材加权重：问图片优先图片类条目、问视频优先视频类条目
        if wants_image and any(word in text_all for word in ("图片", "出图", "作图", "画图")):
            score += 2.0
        if wants_video and any(word in text_all for word in ("视频", "成片", "出片", "分镜")):
            score += 2.0
        # 操作类问题（怎么/步骤/在哪）优先带操作说明的条目
        if is_howto and any(word in title for word in ("怎么", "如何", "在哪", "步骤", "吗")):
            score += 2.0
        # 第 13 章是「操作步骤速查」，操作类问题给它加权
        if is_howto and "操作步骤速查" in (entry.get("section") or ""):
            score += 2.5
        if score > 0:
            scored.append((score, -order, entry))
    scored.sort(reverse=True)
    picked = [item[2] for item in scored[: max(1, int(limit or _MAX_ENTRIES))]]
    if picked:
        return picked
    # 没命中：给最前面的几条“通用”条目兜底（账号/安装/调度助手）
    return entries[: min(4, len(entries))]


def faq_index_text() -> str:
    _entries, index = _data()
    return index


def with_customer_service_faq(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return message
    entries = retrieve_faq_entries(text)
    index = faq_index_text()
    if not entries and not index:
        return message
    hit_lines: List[str] = []
    for entry in entries:
        body = entry.get("body") or ""
        if len(body) > _MAX_ENTRY_CHARS:
            body = body[: _MAX_ENTRY_CHARS].rstrip() + "…"
        hit_lines.append(f"[{entry.get('id')}] {entry.get('title')}\n{body}")
    blocks = [f"{_QUESTION_PREFIX}{text}"]
    if hit_lines:
        blocks.append("【命中条目（来自客服百问百答，请据此回答）】\n" + "\n\n".join(hit_lines))
    if index:
        blocks.append("【客服百问百答目录（知识库里还有这些）】\n" + index)
    blocks.append(
        "【回答要求】先给可直接复制给客户的答复（口语、简短），再给一行「内部提示」；"
        "命中条目里没有答案就照实说需要确认，不要编造；不要执行任何创作/发布类动作。"
    )
    body = "\n\n".join(blocks)
    return f"{FAQ_MARK_START}\n{body}\n{FAQ_MARK_END}"


def strip_customer_service_faq(value: str) -> str:
    text = str(value or "")
    start = text.find(FAQ_MARK_START)
    if start < 0:
        return text
    end = text.find(FAQ_MARK_END, start)
    if end < 0:
        return text
    body = text[start + len(FAQ_MARK_START) : end]
    match = re.search(re.escape(_QUESTION_PREFIX) + r"(.+)", body)
    if match:
        return match.group(1).strip()
    tail = text[end + len(FAQ_MARK_END) :].strip()
    return tail
