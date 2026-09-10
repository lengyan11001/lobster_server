"""模型把"工具调用"写进正文时的通用清理 + 按模型的策略开关。

为什么不是一堆"逐个格式"的正则：模型（DeepSeek 等）会用各种花样把调用写成正文，
换个模型/换个版本就换一种写法。这里改成按**结构**识别：

1. 信封族标记：``tool_calls`` / ``invoke`` / ``function_call`` / ``tool_use`` /
   ``antml:invoke`` / DeepSeek 的 ``DSML`` 标记等（大小写、空格、分隔符都不敏感）；
2. 工具名标签：标签名命中**本次请求声明的工具名**（或内置已知工具名）的任何
   ``<name ...>`` 元素；
3. 自造标签启发式：任意标签块里出现调用特征（``name=``、``parameter``、``query``、
   ``arguments``、``"name":`` 等）也按假调用处理；
4. 代码块 / JSON 形式：```json 包裹的 ``{"tool_calls": ...}`` 或 ``{"name": ...,
   "arguments": ...}``。

所以新模型的新写法不需要再加规则；只有"这个模型要不要开流式清理 / 要不要丢
reasoning_content / 工具往返几轮后摘 tools"这类**策略**才是按模型配置的
（``SUTUI_CHAT_MODEL_PROFILES_JSON`` / ``SUTUI_CHAT_MODEL_PROFILE_MAP_JSON``）。
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Pattern, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_ID = "default"
DEFAULT_FALLBACK_TEXT = "好的，我来为您总结一下已获取的信息。"

# 信封族：模型把调用写成正文时常用的包裹词
ENVELOPE_WORDS: Tuple[str, ...] = (
    "tool_calls",
    "tool_call",
    "function_calls",
    "function_call",
    "tool_use",
    "tool_result",
    "invoke",
    "invocation",
    "parameter",
    "parameters",
    "antml:invoke",
    "antml:parameter",
    "tool_calls_block",
    "dsml",
)

# 已知工具名（camel + snake），请求没声明 tools 时也能兜住
KNOWN_TOOL_NAMES: Tuple[str, ...] = (
    "listsystemcapabilities",
    "list_system_capabilities",
    "listpersonalmemorydocuments",
    "readpersonalmemorydocument",
    "readpersonalmemory",
    "savepersonalmemorytext",
    "importattachmenttopersonalmemory",
    "readpersonalprofile",
    "updatepersonalprofile",
    "readwechatintelligence",
    "teachwechattakeover",
    "requesttaskapproval",
    "dispatchonlinecapability",
    "dispatchonlinetask",
    "getonlinetaskstatus",
    "search_tools",
    "web_search",
    "browse",
    "read_file",
    "write_file",
)

# 标签块里出现这些字样，说明它是在"描述一次调用"，不是普通 HTML/正文
_PAYLOAD_HINTS: Tuple[str, ...] = (
    "name=",
    "parameter",
    "arguments",
    "tool_call",
    "function_call",
    "capability_id",
    "query>",
    '"name"',
    "'name'",
)

_DSML_START_RE = re.compile(r"(?i)<\s*[\|\uff5c]{1,}\s*DSML")
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\s*([\s\S]*?)```")


@dataclass(frozen=True)
class ModelProfile:
    id: str
    match: Tuple[str, ...] = ("*",)
    # 是否支持 function calling（预留字段，代理暂未强制摘除 tools）
    supports_tools: bool = True
    # 工具往返多少轮后摘掉 tools（0 = 不摘）
    strip_tools_after_rounds: int = 4
    # 请求带了 tools 但模型只回文本时，是否换模型重试
    retry_when_tools_ignored: bool = True
    # 是否清理正文里的假工具调用（通用结构识别，所有模型默认开）
    strip_fake_tool_text: bool = True
    # 流式回复是否也清理（关掉则只在非流式清理）
    guard_stream_fake_tools: bool = True
    # 是否丢掉 reasoning_content / thinking 字段
    strip_reasoning_content: bool = False
    # 兜底文案：整段都是假调用时显示什么
    fake_tool_fallback_text: str = DEFAULT_FALLBACK_TEXT
    # 未来新模型的自造标记/工具名，可在这里补充（不用改代码）
    extra_envelope_words: Tuple[str, ...] = ()
    extra_tool_names: Tuple[str, ...] = ()
    notes: str = ""


_BUILTIN_PROFILES: Tuple[Dict[str, Any], ...] = (
    {
        "id": "deepseek",
        "match": ("deepseek-*", "*deepseek*"),
        "strip_reasoning_content": True,
        "notes": "DeepSeek 系：丢弃 reasoning_content（假调用由通用识别处理）",
    },
    {
        "id": DEFAULT_PROFILE_ID,
        "match": ("*",),
        "notes": "默认档案：通用假调用清理 + 流式清理",
    },
)

_PROFILE_CACHE: Optional[Tuple[ModelProfile, ...]] = None
_MODEL_MAP_CACHE: Optional[Dict[str, str]] = None
_REGEX_CACHE: Dict[Tuple[str, str], Pattern[str]] = {}


def _env_json(name: str) -> Any:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[model-profile] %s 不是合法 JSON，已忽略", name)
        return None


def _build_profiles() -> Tuple[ModelProfile, ...]:
    raw = [dict(item) for item in _BUILTIN_PROFILES]
    overrides = _env_json("SUTUI_CHAT_MODEL_PROFILES_JSON")
    if isinstance(overrides, list):
        by_id = {str(item.get("id") or ""): idx for idx, item in enumerate(raw)}
        for override in overrides:
            if not isinstance(override, dict):
                continue
            pid = str(override.get("id") or "").strip()
            if not pid:
                continue
            if pid in by_id:
                raw[by_id[pid]] = {**raw[by_id[pid]], **override}
            else:
                raw.append(dict(override))
    profiles = []
    for item in raw:
        try:
            profiles.append(
                ModelProfile(
                    id=str(item.get("id") or DEFAULT_PROFILE_ID),
                    match=tuple(str(x) for x in (item.get("match") or ("*",))),
                    supports_tools=bool(item.get("supports_tools", True)),
                    strip_tools_after_rounds=max(0, int(item.get("strip_tools_after_rounds", 4))),
                    retry_when_tools_ignored=bool(item.get("retry_when_tools_ignored", True)),
                    strip_fake_tool_text=bool(item.get("strip_fake_tool_text", True)),
                    guard_stream_fake_tools=bool(item.get("guard_stream_fake_tools", True)),
                    strip_reasoning_content=bool(item.get("strip_reasoning_content", False)),
                    fake_tool_fallback_text=str(item.get("fake_tool_fallback_text") or DEFAULT_FALLBACK_TEXT),
                    extra_envelope_words=tuple(str(x) for x in (item.get("extra_envelope_words") or ())),
                    extra_tool_names=tuple(str(x) for x in (item.get("extra_tool_names") or ())),
                    notes=str(item.get("notes") or ""),
                )
            )
        except (TypeError, ValueError) as exc:  # pragma: no cover - 防御性
            logger.warning("[model-profile] 档案解析失败已跳过: %s (%s)", item, exc)
    if not any(p.id == DEFAULT_PROFILE_ID for p in profiles):
        profiles.append(ModelProfile(id=DEFAULT_PROFILE_ID))
    return tuple(profiles)


def profiles() -> Tuple[ModelProfile, ...]:
    global _PROFILE_CACHE
    if _PROFILE_CACHE is None:
        _PROFILE_CACHE = _build_profiles()
    return _PROFILE_CACHE


def reset_cache() -> None:
    global _PROFILE_CACHE, _MODEL_MAP_CACHE
    _PROFILE_CACHE = None
    _MODEL_MAP_CACHE = None
    _REGEX_CACHE.clear()


def _model_profile_map() -> Dict[str, str]:
    global _MODEL_MAP_CACHE
    if _MODEL_MAP_CACHE is None:
        raw = _env_json("SUTUI_CHAT_MODEL_PROFILE_MAP_JSON")
        _MODEL_MAP_CACHE = {
            str(k).strip().lower(): str(v).strip()
            for k, v in (raw.items() if isinstance(raw, dict) else [])
            if str(k).strip() and str(v).strip()
        }
    return _MODEL_MAP_CACHE


def profile_for(model_id: str) -> ModelProfile:
    mid = str(model_id or "").strip().lower()
    if mid:
        mapped = _model_profile_map().get(mid)
        if mapped:
            for profile in profiles():
                if profile.id == mapped:
                    return profile
    best: Optional[Tuple[Tuple[int, int], ModelProfile]] = None
    for profile in profiles():
        for pattern in profile.match:
            pat = str(pattern or "").strip().lower()
            if not pat:
                continue
            if pat == mid:
                score = (2, len(pat))
            elif fnmatch.fnmatchcase(mid, pat):
                # 越具体的通配（去掉 * 之后越长）越优先，"*" 只在没别的命中时用
                score = (1, len(pat.replace("*", "")))
            else:
                continue
            if best is None or score > best[0]:
                best = (score, profile)
    if best is not None:
        return best[1]
    for profile in profiles():
        if profile.id == DEFAULT_PROFILE_ID:
            return profile
    return profiles()[0]


def _names(profile: ModelProfile, tool_names: Iterable[str]) -> Tuple[str, ...]:
    wanted = {str(x or "").strip().lower() for x in tool_names if str(x or "").strip()}
    wanted |= set(KNOWN_TOOL_NAMES)
    wanted |= {str(x or "").strip().lower() for x in profile.extra_tool_names if str(x or "").strip()}
    wanted |= {str(x or "").strip().lower() for x in ENVELOPE_WORDS}
    wanted |= {str(x or "").strip().lower() for x in profile.extra_envelope_words if str(x or "").strip()}
    wanted.discard("")
    return tuple(sorted(wanted, key=len, reverse=True))


def _alt(names: Sequence[str]) -> str:
    return "|".join(re.escape(name) for name in names)


def _tag_regexes(profile: ModelProfile, tool_names: Iterable[str]) -> Dict[str, Pattern[str]]:
    cache_key = (profile.id, "|".join(sorted(str(x) for x in tool_names)))
    cached = _REGEX_CACHE.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]
    names = _alt(_names(profile, tool_names))
    regexes = {
        # 成对标签块：<x ...>...</x>
        "pair": re.compile(rf"(?is)<\s*(?:{names})(?=[\s/>])[^>]*>[\s\S]*?<\s*/\s*(?:{names})\s*>"),
        # 未闭合标签块：<x ...> 之后全是内容
        "tail": re.compile(rf"(?is)<\s*(?:{names})(?=[\s/>])[^>]*>[\s\S]*$"),
        # 孤立闭合标签
        "stray": re.compile(rf"(?is)</\s*(?:{names})\s*>"),
        # 自造标签：块内容像一次调用（name=/parameter/capability_id/...）
        "payload": re.compile(
            r"(?is)<\s*([A-Za-z_][A-Za-z0-9_.:-]{0,60})(?=[\s/>])[^>]*>[\s\S]*?<\s*/\s*\1\s*>"
        ),
    }
    _REGEX_CACHE[cache_key] = regexes  # type: ignore[assignment]
    return regexes


def _strip_json_tool_calls(text: str, tool_names: Iterable[str]) -> Tuple[str, bool]:
    """删掉正文里的 JSON 工具调用（含 ```json 代码块）。"""
    names = {str(x or "").strip().lower() for x in tool_names}
    changed = False

    def _drop_block(block: str) -> str:
        nonlocal changed
        lowered = block.lower()
        looks_like_call = (
            '"tool_calls"' in lowered
            or '"function_call"' in lowered
            or ('"name"' in lowered and '"arguments"' in lowered)
            or any(f'"{n}"' in lowered for n in names if n)
        )
        if not looks_like_call:
            return block
        changed = True
        return ""

    text = _FENCE_RE.sub(lambda m: _drop_block(m.group(0)), text)
    # 裸 JSON 对象：按花括号配对整段取出，能解析且像工具调用就删掉
    for snippet, start, end in _iter_json_objects(text):
        try:
            parsed = json.loads(snippet)
        except (json.JSONDecodeError, ValueError):
            continue
        if _looks_like_tool_call_object(parsed, names):
            text = text[:start] + text[end:]
            changed = True
            break
    return text, changed


def _iter_json_objects(text: str) -> list[tuple[str, int, int]]:
    """粗略地按花括号配对找出文本里的 JSON 对象（忽略字符串内的括号）。"""
    found: list[tuple[str, int, int]] = []
    start = -1
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    found.append((text[start : index + 1], start, index + 1))
                    start = -1
    return found


def _looks_like_tool_call_object(value: Any, names: Iterable[str]) -> bool:
    if isinstance(value, dict):
        keys = {str(k).lower() for k in value.keys()}
        if keys & {"tool_calls", "function_call", "tool_call"}:
            return True
        if "name" in keys and (keys & {"arguments", "parameters", "input"}):
            return True
        name = str(value.get("name") or "").strip().lower()
        if name and name in set(names):
            return True
        return any(_looks_like_tool_call_object(v, names) for v in value.values())
    if isinstance(value, list):
        return any(_looks_like_tool_call_object(v, names) for v in value)
    return False


def strip_fake_tool_text(
    text: str,
    profile: ModelProfile,
    tool_names: Iterable[str] = (),
) -> Tuple[str, bool]:
    """按结构清掉正文里的假工具调用；返回 (清理后文本, 是否有改动)。"""
    if not isinstance(text, str) or not text:
        return text, False
    changed = False
    # 1) DSML 标记族：按行清掉带标记的行（标记后面的内容也属于这次调用）
    if _DSML_START_RE.search(text):
        lines = text.splitlines(keepends=True)
        kept = [line for line in lines if not _DSML_START_RE.search(line)]
        if len(kept) != len(lines):
            text = "".join(kept)
            changed = True
    # 2) 信封族 / 工具名标签 / 未闭合 / 孤立闭合
    regexes = _tag_regexes(profile, tool_names)
    text, n = regexes["pair"].subn("", text)
    changed |= bool(n)
    text, n = regexes["tail"].subn("", text)
    changed |= bool(n)
    text, n = regexes["stray"].subn("", text)
    changed |= bool(n)
    # 3) 自造标签（块内容像调用）
    def _drop_payload(match: "re.Match[str]") -> str:
        nonlocal changed
        block = match.group(0)
        lowered = block.lower()
        if any(hint in lowered for hint in _PAYLOAD_HINTS):
            changed = True
            return ""
        return block

    text = regexes["payload"].sub(_drop_payload, text)
    # 4) JSON / 代码块形式
    text, n = _strip_json_tool_calls(text, tool_names)
    changed |= bool(n)
    return text.strip(), changed


def sanitize_message_content(
    message: Dict[str, Any],
    profile: ModelProfile,
    tool_names: Iterable[str] = (),
) -> bool:
    if not isinstance(message, dict):
        return False
    changed = False
    if profile.strip_reasoning_content:
        for key in ("reasoning_content", "reasoning", "thinking"):
            if message.get(key):
                message.pop(key, None)
                changed = True
    content = message.get("content")
    if profile.strip_fake_tool_text and isinstance(content, str) and content:
        cleaned, removed = strip_fake_tool_text(content, profile, tool_names)
        if removed and not cleaned:
            cleaned = profile.fake_tool_fallback_text
        if cleaned != content:
            message["content"] = cleaned
            changed = True
    return changed


def apply_to_completion(
    data: Any,
    profile: ModelProfile,
    tool_names: Iterable[str] = (),
) -> bool:
    if not isinstance(data, dict):
        return False
    choices = data.get("choices")
    if not isinstance(choices, list):
        return False
    changed = False
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict) and sanitize_message_content(message, profile, tool_names):
            changed = True
    return changed


def fake_tool_hit(text: Any, profile: ModelProfile, tool_names: Iterable[str] = ()) -> bool:
    if not isinstance(text, str) or not text:
        return False
    _, changed = strip_fake_tool_text(text, profile, tool_names)
    return changed


@dataclass
class StreamGuard:
    """流式清理：只扣住"可能是假调用"的那一小段，其余照常实时输出。"""

    profile: ModelProfile
    tool_names: Tuple[str, ...] = ()
    hold: str = ""
    dropped: bool = False
    _hold_limit: int = 4000

    @property
    def enabled(self) -> bool:
        return bool(self.profile.guard_stream_fake_tools and self.profile.strip_fake_tool_text)

    def feed(self, text: Any) -> str:
        if not self.enabled:
            return text if isinstance(text, str) else ""
        if isinstance(text, str) and text:
            self.hold += text
        return self._drain(final=False)

    def flush(self) -> str:
        if not self.enabled or not self.hold:
            return ""
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> str:
        cleaned, removed = strip_fake_tool_text(self.hold, self.profile, self.tool_names)
        if removed:
            self.dropped = True
            self.hold = cleaned
        if not self.hold:
            return ""
        cut = self._pending_start(self.hold)
        if cut is not None:
            if final:
                if _is_fake_call_start(self.hold[cut:]):
                    self.dropped = True
                    emit, self.hold = self.hold[:cut], ""
                    return emit
                emit, self.hold = self.hold, ""
                return emit
            emit, self.hold = self.hold[:cut], self.hold[cut:]
            return emit
        emit, self.hold = self.hold, ""
        return emit

    def _pending_start(self, text: str) -> Optional[int]:
        """返回需要继续观察的下标：从最后一个可能开启假调用的标记开始。"""
        candidates = [i for i in (text.rfind("<"), text.rfind("{"), text.rfind("`")) if i >= 0]
        if not candidates:
            return None
        cut = max(candidates)
        if len(text) - cut > self._hold_limit:
            return None
        return cut if _could_start_fake_call(text[cut:]) else None


def _could_start_fake_call(tail: str) -> bool:
    """这段尾部是否值得继续扣住观察（宽进严出，宁可多扣一小会儿）。"""
    if tail.startswith("<"):
        if _DSML_START_RE.match(tail):
            return True
        match = re.match(r"(?is)<\s*/?\s*([A-Za-z_|\uff5c])", tail)
        if not match:
            return False  # 例：价格<100
        name = match.group(1).lower()
        rest = tail[match.end(1):]
        more = re.match(r"[A-Za-z0-9_|\uff5c:.-]{0,60}", rest)
        if more:
            name += more.group(0).lower()
        name = name.strip("|").replace("\uff5c", "")
        if not name:
            return True
        if name in set(ENVELOPE_WORDS) or name in set(KNOWN_TOOL_NAMES):
            return True
        all_names = set(ENVELOPE_WORDS) | set(KNOWN_TOOL_NAMES)
        return any(candidate.startswith(name) for candidate in all_names)
    if tail.startswith("{"):
        return _json_object_prefix_ok(tail)
    if tail.startswith("`"):
        stripped = tail.lstrip("`")
        if len(stripped) < 8:
            return True
        return _json_object_prefix_ok(stripped.lstrip("json").lstrip())
    return False


def _is_fake_call_start(tail: str) -> bool:
    """严格判断：这段尾部**确实**是假调用（用于流结束时的取舍）。"""
    if _DSML_START_RE.match(tail):
        return True
    match = re.match(r"(?is)<\s*/?\s*([A-Za-z_|\uff5c][A-Za-z0-9_|\uff5c:.-]{0,60})", tail)
    if match:
        name = match.group(1).lower().strip("|").replace("\uff5c", "")
        return name in set(ENVELOPE_WORDS) or name in set(KNOWN_TOOL_NAMES)
    lowered = tail.lower()
    if tail.startswith("{") or tail.startswith("`"):
        return any(f'"{key}' in lowered for key in _JSON_CALL_KEYS) or "tool_call" in lowered
    return False


_JSON_CALL_KEYS: Tuple[str, ...] = (
    "name",
    "tool_calls",
    "tool_call",
    "function_call",
    "function_calls",
    "arguments",
    "parameters",
    "input",
    "capability_id",
)


def _json_object_prefix_ok(tail: str) -> bool:
    """``{...`` 这段是否可能是"工具调用对象"的开头（宽进严出）。"""
    stripped = tail.lstrip()
    if not stripped.startswith("{"):
        return False
    rest = stripped[1:].lstrip()
    if not rest:
        return True  # 刚出现 '{'，再等一两个字符
    if rest[0] != '"':
        return False  # 不是 JSON 对象的字符串键
    key_part = rest[1:]
    if key_part and not re.match(r"[A-Za-z_]", key_part):
        return False  # 键不是 ASCII 标识符（例如 {"好的}）
    match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", key_part)
    key = (match.group(0) if match else "").lower()
    if not key:
        return len(key_part) < 12  # 键还没吐完
    return any(candidate.startswith(key) or key.startswith(candidate) for candidate in _JSON_CALL_KEYS)


def guard_for(model_id: str, tool_names: Iterable[str] = ()) -> StreamGuard:
    profile = profile_for(model_id)
    return StreamGuard(profile=profile, tool_names=tuple(str(x) for x in tool_names if str(x or "").strip()))
