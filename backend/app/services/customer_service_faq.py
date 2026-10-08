"""客服模式知识库：两段式取知识（先让 LLM 看目录挑章节，再只把相关章节发给回答模型）。

用户口径（2026-10-01）：
- 不喜欢“关键词撞上一个答一个、换个说法就漏”的方案；
- 希望先给 LLM 目录（章节 + 问题），让它理解用户问题、指出该看哪些章节，
  再把那部分内容发给 LLM；不要一次把整篇全发。
- 多轮：第 1 段把最近几轮对话一起给检索 LLM，能理解“那 H5 呢？”这类追问。

兜底：LLM 挑选失败/超时 → 退回关键词检索；都拿不到 → 只给目录，让回答模型照实说需要确认。
完整 MD 仍在 backend/app/data/customer-service-faq.md（客户端也带一份）。
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[float, str]] = {}
_ENTRY_CACHE: Dict[str, Tuple[float, List[Dict[str, str]], str, List[Tuple[str, str]]]] = {}

FAQ_PATH = Path(__file__).resolve().parents[1] / "data" / "customer-service-faq.md"

FAQ_MARK_START = "【客服知识库·百问百答（系统注入：先看目录判断，再按命中的章节/条目回答；没有的就说“我需要确认后再回复您”，不要编造）】"
FAQ_MARK_END = "【客服知识库结束】"
_QUESTION_PREFIX = "用户问题："

_MAX_ENTRIES = 10
_MAX_ENTRY_CHARS = 520
_MAX_TOTAL_CHARS = 7000
_INTERNAL_LLM_URL = "http://127.0.0.1:8000/api/sutui-chat/completions"
_PICK_TIMEOUT_SECONDS = 25.0

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


def _parse_entries(md: str) -> Tuple[List[Dict[str, str]], str, List[Tuple[str, str]]]:
    entries: List[Dict[str, str]] = []
    sections: List[Tuple[str, List[str]]] = []
    current_section = ""
    current_titles: List[str] = []
    lines = md.splitlines()
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if stripped.startswith("## "):
            if current_section or current_titles:
                sections.append((current_section, current_titles))
            current_section = stripped[3:].strip()
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
            body = re.sub(r"^A\s*[:：]\s*", "", " ".join(body_lines)).strip()
            entries.append({"id": qid, "title": title, "section": current_section, "body": body})
            current_titles.append(f"{qid} {title}")
            continue
        idx += 1
    if current_section or current_titles:
        sections.append((current_section, current_titles))
    index_lines: List[str] = []
    chapter_list: List[Tuple[str, str]] = []
    for name, titles in sections:
        if not name:
            continue
        index_lines.append(f"- {name}：" + "；".join(titles))
        if titles:
            chapter_list.append((name, f"{titles[0].split()[0]}-{titles[-1].split()[0]}"))
    return entries, "\n".join(index_lines), chapter_list


def _data() -> Tuple[List[Dict[str, str]], str, List[Tuple[str, str]]]:
    md = customer_service_faq_text()
    if not md:
        return [], "", []
    key = str(FAQ_PATH)
    try:
        mtime = FAQ_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0
    with _LOCK:
        cached = _ENTRY_CACHE.get(key)
        if cached and cached[0] == mtime:
            return cached[1], cached[2], cached[3]
    entries, index, chapters = _parse_entries(md)
    with _LOCK:
        _ENTRY_CACHE[key] = (mtime, entries, index, chapters)
    return entries, index, chapters


def faq_index_text() -> str:
    return _data()[1]


def faq_chapter_list() -> List[Tuple[str, str]]:
    return _data()[2]


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
            if bigram not in _STOP_TOKENS:
                out.append(bigram)
        if len(chunk) == 1:
            out.append(chunk)
    return out


def retrieve_faq_entries(query: str, limit: int = _MAX_ENTRIES) -> List[Dict[str, str]]:
    question_tokens = list(dict.fromkeys(_tokens(query)))
    entries, _index, _chapters = _data()
    if not entries:
        return []
    if not question_tokens:
        return entries[: min(4, len(entries))]
    query_text = str(query or "")
    wants_image = any(word in query_text for word in ("图片", "图", "作图", "画图", "出图"))
    wants_video = any(word in query_text for word in ("视频", "短片", "成片", "出片"))
    is_howto = any(word in query_text for word in ("怎么", "如何", "步骤", "在哪", "入口", "操作", "打不开", "失败", "报错"))
    scored: List[Tuple[float, int, Dict[str, str]]] = []
    for order, entry in enumerate(entries):
        title = entry.get("title") or ""
        body = entry.get("body") or ""
        text_all = title + " " + body
        score = 0.0
        for token in question_tokens:
            if token in title:
                score += 3.0
            elif token in body:
                score += 1.0
        if wants_image and any(word in text_all for word in ("图片", "出图", "作图", "画图")):
            score += 2.0
        if wants_video and any(word in text_all for word in ("视频", "成片", "出片", "分镜")):
            score += 2.0
        if is_howto and any(word in title for word in ("怎么", "如何", "在哪", "步骤", "吗")):
            score += 2.0
        if is_howto and "操作步骤速查" in (entry.get("section") or ""):
            score += 2.5
        if score > 0:
            scored.append((score, -order, entry))
    scored.sort(reverse=True)
    picked = [item[2] for item in scored[: max(1, int(limit or _MAX_ENTRIES))]]
    return picked or entries[: min(4, len(entries))]


def _entries_by_id() -> Dict[str, Dict[str, str]]:
    entries, _index, _chapters = _data()
    return {entry["id"]: entry for entry in entries}


def _entries_for_chapter(keyword: str) -> List[Dict[str, str]]:
    entries, _index, _chapters = _data()
    keyword = str(keyword or "").strip()
    if not keyword:
        return []
    return [entry for entry in entries if keyword and keyword in (entry.get("section") or "")]


def _render_entries(entries: List[Dict[str, str]]) -> str:
    lines: List[str] = []
    total = 0
    for entry in entries[:_MAX_ENTRIES]:
        body = entry.get("body") or ""
        if len(body) > _MAX_ENTRY_CHARS:
            body = body[: _MAX_ENTRY_CHARS].rstrip() + "…"
        block = f"[{entry.get('id')}] {entry.get('title')}\n{body}"
        if total + len(block) > _MAX_TOTAL_CHARS:
            break
        lines.append(block)
        total += len(block)
    return "\n\n".join(lines)


def _pick_ids_with_llm(question: str, history_text: str, auth_header: str) -> Tuple[List[str], List[str]]:
    """第 1 段：把目录 + 最近对话 + 当前问题交给 LLM，让它挑章节/条目。"""
    entries, index, chapters = _data()
    if not entries and not chapters:
        return [], []
    if not str(auth_header or "").strip():
        return [], []
    chapter_text = "\n".join(f"{idx + 1}. {name}（{span}）" for idx, (name, span) in enumerate(chapters))
    system = (
        "你是客服知识库检索助手。下面给你《客服百问百答》的章节目录和问题清单。\n"
        "请判断用户当前问题最该看哪些章节/条目，让另一个客服助手据此回答。\n"
        "只输出 JSON，不要解释：{\"chapters\":[章节序号数字],\"q_ids\":[\"Q105\"],\"reason\":\"一句话\"}\n"
        "规则：最多 2 个章节、最多 10 个条目；追问（如“那 H5 呢”）要结合最近对话理解；"
        "跟客服无关就返回 {\"chapters\":[],\"q_ids\":[]}。\n"
    )
    user = (
        f"【章节】\n{chapter_text}\n\n【问题清单】\n{index}\n\n"
        f"【最近对话】\n{(history_text or '（无）')[:1500]}\n\n【当前问题】\n{question}"
    )
    body = {
        "model": "",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "temperature": 0.1,
    }
    try:
        import httpx

        with httpx.Client(timeout=_PICK_TIMEOUT_SECONDS, trust_env=False) as client:
            resp = client.post(_INTERNAL_LLM_URL, json=body, headers={"Authorization": auth_header, "Content-Type": "application/json"})
        if resp.status_code >= 400:
            return [], []
        data = resp.json() if resp.content else {}
        text = ""
        try:
            text = str(data["choices"][0]["message"]["content"] or "")
        except Exception:
            text = str(data.get("text") or data.get("content") or "")
    except Exception:
        return [], []
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return [], []
    try:
        parsed = json.loads(match.group(0))
    except Exception:
        return [], []
    chapter_ids: List[str] = []
    for item in parsed.get("chapters") or []:
        try:
            number = int(str(item).strip())
        except Exception:
            continue
        if 1 <= number <= len(chapters):
            chapter_ids.append(chapters[number - 1][0])
    q_ids = [str(item).strip().upper() for item in (parsed.get("q_ids") or []) if str(item).strip()]
    return chapter_ids, q_ids


def build_service_context(question: str, history_text: str = "", auth_header: str = "") -> str:
    """两段式组装：LLM 挑章节 → 只把相关条目拼进去；失败退回关键词检索。"""
    text = str(question or "").strip()
    if not text:
        return question
    entries, index, _chapters = _data()
    if not entries:
        return question
    picked: List[Dict[str, str]] = []
    source = "keyword"
    chapter_names, q_ids = _pick_ids_with_llm(text, history_text, auth_header)
    if chapter_names or q_ids:
        source = "llm"
        by_id = _entries_by_id()
        picked.extend(by_id[qid] for qid in q_ids if qid in by_id)
        for name in chapter_names:
            picked.extend(_entries_for_chapter(name))
    if not picked:
        picked = retrieve_faq_entries(text)
    deduped: List[Dict[str, str]] = []
    seen = set()
    for entry in picked:
        if entry["id"] in seen:
            continue
        seen.add(entry["id"])
        deduped.append(entry)
    hit_text = _render_entries(deduped)
    blocks = [f"{_QUESTION_PREFIX}{text}"]
    if hit_text:
        blocks.append(f"【命中条目（第 1 段检索方式：{source}）】\n{hit_text}")
    if index:
        blocks.append("【客服百问百答目录（需要时再问用户细节）】\n" + index)
    blocks.append(
        "【回答要求】只输出「可以直接复制发给客户」的那一段答复：口语、简短（1-3 句）、不带任何标记或小标题。"
        "禁止写「【可直接发给客户】」「【内部提示】」「内部提示：」这类字样，禁止给内部建议、解释或反问用户；"
        "命中条目里没有答案就照实说：我需要确认后再回复您，稍后给您准确说明。"
    )
    joined = "\n\n".join(blocks)
    return FAQ_MARK_START + "\n" + joined + "\n" + FAQ_MARK_END


def with_customer_service_faq(message: str) -> str:
    """兼容旧调用：只走关键词检索（不调 LLM）。"""
    return build_service_context(message, history_text="", auth_header="")


_INTERNAL_MARKERS = ("【内部提示】", "内部提示：", "内部提示:", "【内部】", "（内部提示）", "【仅内部】")
_LABEL_PREFIXES = (
    "【可直接发给客户】",
    "【可直接发给客户的答复】",
    "【客户答复】",
    "【可发给客户】",
    "可直接发给客户：",
    "【答复】",
)


def clean_service_reply(value: str) -> str:
    """客服模式的答复只保留「可直接发给客户」的那段：去标记 + 砍掉内部提示部分。"""
    text = str(value or "")
    for marker in _INTERNAL_MARKERS:
        index = text.find(marker)
        if index >= 0:
            text = text[:index]
    out: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        for label in _LABEL_PREFIXES:
            if stripped.startswith(label):
                stripped = stripped[len(label) :].strip()
        if stripped:
            out.append(stripped)
    return "\n".join(out).strip()


def strip_customer_service_faq(value: str) -> str:
    text = str(value or "")
    start = text.find(FAQ_MARK_START)
    if start < 0:
        return text
    end = text.find(FAQ_MARK_END, start)
    if end < 0:
        return text
    body = text[start + len(FAQ_MARK_START) : end]
    for line in body.splitlines():
        if line.startswith(_QUESTION_PREFIX):
            return line[len(_QUESTION_PREFIX) :].strip()
    return text[end + len(FAQ_MARK_END) :].strip()
