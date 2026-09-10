"""按模型区分的回复处理档案（model reply profile）。

不同模型（尤其 DeepSeek 系）会用各自的怪招表达"我要调用工具"：有的返回标准
``tool_calls``，有的把调用写成正文（DSML 标记、``<listSystemCapabilities>`` 这种
XML 标签、代码块里的 JSON 等）。这些毛病只属于个别模型，所以处理规则按模型
分档保存，代理只应用命中档案的规则，其它模型完全不受影响。

新增模型时优先用环境变量覆盖/追加，不必改代码：

* ``SUTUI_CHAT_MODEL_PROFILES_JSON``：JSON 数组，按 ``id`` 覆盖内置档案或追加
  新档案，例如 ``[{"id":"glm","match":["glm-*"],"guard_stream_fake_tools":true}]``
* ``SUTUI_CHAT_MODEL_PROFILE_MAP_JSON``：JSON 对象，把具体模型名钉到某个档案，
  例如 ``{"deepseek-v4-flash":"deepseek"}``

档案字段（全部可选，未声明的用默认档案的值）：

``supports_tools``            该模型是否支持 function calling（预留字段，代理暂未强制摘除）
``strip_tools_after_rounds``  工具往返达到多少轮后摘掉 tools（0=不摘）
``retry_when_tools_ignored``  请求带了 tools 但模型只回文本时，是否换模型重试
``strip_fake_tool_text``      是否清理正文里的假工具调用标记
``fake_tool_patterns``        命中即视为假工具调用的正则（追加）
``fake_tool_block_patterns``  需要整段删除的正则（追加）
``guard_stream_fake_tools``   流式回复是否也做同样清理（默认只清非流式）
``fake_tool_fallback_text``   整段被清空时给用户的兜底文案
``strip_reasoning_content``   是否丢掉回复里的 ``reasoning_content``
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Pattern, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_ID = "default"

# 默认档案：与历史行为一致，普通模型不受任何额外处理影响。
DEFAULT_FAKE_TOOL_PATTERNS: Tuple[str, ...] = (
    r"tool\u2581call",
    r"<\|tool",
    r"<\s*[\uff5c|]+\s*DSML\s*[\uff5c|]+",
    r"```json\s*\{[^}]*capability",
    r"function<\u2581",
)

DEFAULT_BLOCK_PATTERNS: Tuple[str, ...] = (
    r"<\s*[\uff5c|]+\s*DSML\s*[\uff5c|]+(?:tool_calls|function_calls)?\s*>[\s\S]*?"
    r"(?:<\s*/\s*[\uff5c|]+\s*DSML\s*[\uff5c|]+(?:tool_calls|function_calls)?\s*>|$)",
)

DEFAULT_FALLBACK_TEXT = "好的，我来为您总结一下已获取的信息。"

# 会被模型当成标签写进正文的工具名（按小写比较）。
XML_TOOL_TAGS: Tuple[str, ...] = (
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
    "tool_call",
    "tool_calls",
    "function_call",
    "function_calls",
    "invoke",
    "tool_use",
    "web_search",
    "browse",
)

_GENERIC_XML_TOOL_TAG_RE = (
    r"(?i)<\s*/?\s*(?:tool_call|tool_calls|function_call|function_calls|invoke|tool_use)\b"
)


def _xml_tool_tag_re() -> str:
    names = "|".join(sorted(XML_TOOL_TAGS, key=len, reverse=True))
    return rf"(?i)<\s*/?\s*(?:{names})\b"


DEEPSEEK_EXTRA_FAKE_TOOL_PATTERNS: Tuple[str, ...] = (
    _xml_tool_tag_re(),
    _GENERIC_XML_TOOL_TAG_RE,
    r"(?i)<\s*/?\s*(?:antml:)?(?:invoke|parameter)\b",
)

_BUILTIN_PROFILES: Tuple[Dict[str, Any], ...] = (
    {
        "id": "deepseek",
        "match": ("deepseek-*", "*deepseek*"),
        # DeepSeek 除 DSML 外还会把工具名当 XML 标签写进正文，且这类文本经常以
        # 流式增量吐出来，所以额外打开流式清理。
        "extra_fake_tool_patterns": DEEPSEEK_EXTRA_FAKE_TOOL_PATTERNS,
        "guard_stream_fake_tools": True,
        "strip_reasoning_content": True,
        "notes": "DeepSeek 系：DSML / XML 假工具调用 + reasoning_content",
    },
    {
        "id": DEFAULT_PROFILE_ID,
        "match": ("*",),
        "notes": "默认档案：与历史行为一致",
    },
)


@dataclass(frozen=True)
class ModelProfile:
    id: str
    match: Tuple[str, ...]
    supports_tools: bool = True
    strip_tools_after_rounds: int = 4
    retry_when_tools_ignored: bool = True
    strip_fake_tool_text: bool = True
    guard_stream_fake_tools: bool = False
    strip_reasoning_content: bool = False
    fake_tool_fallback_text: str = DEFAULT_FALLBACK_TEXT
    fake_tool_patterns: Tuple[str, ...] = ()
    block_patterns: Tuple[str, ...] = ()
    notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "match": list(self.match),
            "supports_tools": self.supports_tools,
            "strip_tools_after_rounds": self.strip_tools_after_rounds,
            "retry_when_tools_ignored": self.retry_when_tools_ignored,
            "strip_fake_tool_text": self.strip_fake_tool_text,
            "guard_stream_fake_tools": self.guard_stream_fake_tools,
            "strip_reasoning_content": self.strip_reasoning_content,
            "fake_tool_fallback_text": self.fake_tool_fallback_text,
            "fake_tool_patterns": list(self.fake_tool_patterns),
            "block_patterns": list(self.block_patterns),
            "notes": self.notes,
        }


_PROFILE_CACHE: Optional[Tuple[ModelProfile, ...]] = None
_MODEL_MAP_CACHE: Optional[Dict[str, str]] = None
_REGEX_CACHE: Dict[Tuple[str, str], Tuple[Pattern[str], ...]] = {}


def _env_json(name: str) -> Any:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[model-profile] %s 不是合法 JSON，已忽略", name)
        return None


def _merge_profile(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in (override or {}).items():
        if key in {"extra_fake_tool_patterns", "fake_tool_patterns"}:
            merged["fake_tool_patterns"] = tuple(merged.get("fake_tool_patterns") or ()) + tuple(value or ())
        elif key in {"extra_block_patterns", "fake_tool_block_patterns", "block_patterns"}:
            merged["block_patterns"] = tuple(merged.get("block_patterns") or ()) + tuple(value or ())
        elif key == "match":
            merged["match"] = tuple(value or ())
        else:
            merged[key] = value
    return merged


def _build_profiles() -> Tuple[ModelProfile, ...]:
    raw_profiles: List[Dict[str, Any]] = [dict(item) for item in _BUILTIN_PROFILES]
    for item in raw_profiles:
        item["fake_tool_patterns"] = tuple(item.get("fake_tool_patterns") or DEFAULT_FAKE_TOOL_PATTERNS) + tuple(
            item.get("extra_fake_tool_patterns") or ()
        )
        item["block_patterns"] = tuple(item.get("block_patterns") or DEFAULT_BLOCK_PATTERNS) + tuple(
            item.get("extra_block_patterns") or item.get("fake_tool_block_patterns") or ()
        )
    overrides = _env_json("SUTUI_CHAT_MODEL_PROFILES_JSON")
    if isinstance(overrides, list):
        by_id = {str(item.get("id") or ""): idx for idx, item in enumerate(raw_profiles)}
        for override in overrides:
            if not isinstance(override, dict):
                continue
            pid = str(override.get("id") or "").strip()
            if not pid:
                continue
            if pid in by_id:
                raw_profiles[by_id[pid]] = _merge_profile(raw_profiles[by_id[pid]], override)
            else:
                base = {"fake_tool_patterns": DEFAULT_FAKE_TOOL_PATTERNS, "block_patterns": DEFAULT_BLOCK_PATTERNS}
                raw_profiles.append(_merge_profile(base, override))
    profiles: List[ModelProfile] = []
    for item in raw_profiles:
        try:
            profiles.append(
                ModelProfile(
                    id=str(item.get("id") or DEFAULT_PROFILE_ID),
                    match=tuple(str(x) for x in (item.get("match") or ("*",))),
                    supports_tools=bool(item.get("supports_tools", True)),
                    strip_tools_after_rounds=max(0, int(item.get("strip_tools_after_rounds", 4))),
                    retry_when_tools_ignored=bool(item.get("retry_when_tools_ignored", True)),
                    strip_fake_tool_text=bool(item.get("strip_fake_tool_text", True)),
                    guard_stream_fake_tools=bool(item.get("guard_stream_fake_tools", False)),
                    strip_reasoning_content=bool(item.get("strip_reasoning_content", False)),
                    fake_tool_fallback_text=str(item.get("fake_tool_fallback_text") or DEFAULT_FALLBACK_TEXT),
                    fake_tool_patterns=tuple(item.get("fake_tool_patterns") or DEFAULT_FAKE_TOOL_PATTERNS),
                    block_patterns=tuple(item.get("block_patterns") or DEFAULT_BLOCK_PATTERNS),
                    notes=str(item.get("notes") or ""),
                )
            )
        except (TypeError, ValueError) as exc:  # pragma: no cover - 防御性
            logger.warning("[model-profile] 档案解析失败已跳过: %s (%s)", item, exc)
    if not any(p.id == DEFAULT_PROFILE_ID for p in profiles):
        profiles.append(ModelProfile(id=DEFAULT_PROFILE_ID, match=("*",), fake_tool_patterns=DEFAULT_FAKE_TOOL_PATTERNS, block_patterns=DEFAULT_BLOCK_PATTERNS))
    return tuple(profiles)


def profiles() -> Tuple[ModelProfile, ...]:
    global _PROFILE_CACHE
    if _PROFILE_CACHE is None:
        _PROFILE_CACHE = _build_profiles()
    return _PROFILE_CACHE


def reset_cache() -> None:
    """测试/热更新用：清掉档案与环境变量缓存。"""
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
    """返回该模型命中的档案；未命中任何档案时用 default。"""
    mid = str(model_id or "").strip().lower()
    if mid:
        mapped = _model_profile_map().get(mid)
        if mapped:
            for profile in profiles():
                if profile.id == mapped:
                    return profile
    best: Optional[Tuple[int, ModelProfile]] = None
    for profile in profiles():
        for pattern in profile.match:
            pat = str(pattern or "").strip().lower()
            if not pat:
                continue
            if fnmatch.fnmatch(mid, pat):
                weight = 2 if pat == mid else (1 if not pat.startswith("*") else 0)
                if best is None or weight > best[0]:
                    best = (weight, profile)
    if best is not None:
        return best[1]
    for profile in profiles():
        if profile.id == DEFAULT_PROFILE_ID:
            return profile
    return profiles()[0]


def compile_patterns(profile: ModelProfile, key: str, patterns: Sequence[str]) -> Tuple[Pattern[str], ...]:
    cache_key = (profile.id, key)
    cached = _REGEX_CACHE.get(cache_key)
    if cached is not None:
        return cached
    compiled: List[Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:  # pragma: no cover - 配置错误时跳过
            logger.warning("[model-profile] 档案 %s 的正则无效已跳过: %s (%s)", profile.id, pattern, exc)
    result = tuple(compiled)
    _REGEX_CACHE[cache_key] = result
    return result


def fake_tool_hit(text: Any, profile: ModelProfile) -> bool:
    if not isinstance(text, str) or not text:
        return False
    return any(rex.search(text) for rex in compile_patterns(profile, "fake", profile.fake_tool_patterns))


def _strip_blocks(text: str, profile: ModelProfile) -> Tuple[str, bool]:
    removed = False
    for rex in compile_patterns(profile, "block", profile.block_patterns):
        new_text, count = rex.subn("", text)
        if count:
            removed = True
            text = new_text
    # XML 标签形式的假工具调用：整段 <tag ...>...</tag> 删掉
    for rex in compile_patterns(profile, "xml", (
        r"(?is)<\s*([A-Za-z_][A-Za-z0-9_:.-]*)\b[^>]*>[\s\S]*?<\s*/\s*\1\s*>",
        r"(?is)<\s*(?:tool_call|function_call|invoke)\b[^>]*>[\s\S]*$",
        # 已知工具名的标签，闭合标签还没吐完（流被截断）也整段删掉
        r"(?is)<\s*(?:" + "|".join(sorted(XML_TOOL_TAGS, key=len, reverse=True)) + r")\b[^>]*>[\s\S]*$",
        # 只剩一个孤立闭合标签（开头已被删掉）时也清掉，避免后续正文被扣住
        r"(?is)</\s*(?:" + "|".join(sorted(XML_TOOL_TAGS, key=len, reverse=True)) + r")\s*>",
    )):
        def _drop(match: "re.Match[str]") -> str:
            nonlocal removed
            inner = match.group(0)
            tag = (match.group(1) if match.re.groups else "").lower()
            if not tag or tag in XML_TOOL_TAGS or _looks_like_tool_payload(inner):
                removed = True
                return ""
            return inner
        text = rex.sub(_drop, text)
    return text, removed


def _looks_like_tool_payload(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("<query>", "<capability", "tool_call", "function_call", "arguments"))


def sanitize_message_content(message: Dict[str, Any], profile: ModelProfile) -> bool:
    """就地清理一条 assistant message 的假工具调用文本；有改动返回 True。"""
    if not isinstance(message, dict):
        return False
    changed = False
    if profile.strip_reasoning_content:
        for key in ("reasoning_content", "reasoning", "thinking"):
            if message.get(key):
                message.pop(key, None)
                changed = True
    content = message.get("content")
    if (
        profile.strip_fake_tool_text
        and isinstance(content, str)
        and content
        and fake_tool_hit(content, profile)
    ):
        cleaned, removed = _strip_blocks(content, profile)
        cleaned = cleaned.strip()
        if not cleaned or (removed and not cleaned):
            cleaned = profile.fake_tool_fallback_text
        if cleaned != content:
            message["content"] = cleaned
            changed = True
    return changed


@dataclass
class StreamGuard:
    """流式回复的假工具调用清理器。

    只有档案声明 ``guard_stream_fake_tools`` 时才生效；未开启时 ``feed`` 原样返回，
    所以普通模型完全不经过这里。开启后会把"可能是假工具调用开头"的文本先扣住，
    凑齐完整块就丢掉，确认是正常文本再放行。
    """

    profile: ModelProfile
    hold: str = ""
    dropped: bool = False
    _hold_limit: int = 2000

    @property
    def enabled(self) -> bool:
        return bool(self.profile.guard_stream_fake_tools)

    def feed(self, text: Any) -> str:
        if not self.enabled or not isinstance(text, str) or not text:
            return text if isinstance(text, str) else ""
        self.hold += text
        return self._drain(final=False)

    def flush(self) -> str:
        if not self.enabled or not self.hold:
            return ""
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> str:
        cleaned, removed = _strip_blocks(self.hold, self.profile)
        if removed:
            self.dropped = True
            self.hold = cleaned
        if not self.hold:
            return ""
        # 普通文本里也可能夹着一个还没吐完的假工具块，先扣住那一段再放行前缀。
        cut = self._pending_start(self.hold)
        if cut is not None:
            if final:
                self.dropped = True
                emit, self.hold = self.hold[:cut], ""
                return emit
            emit, self.hold = self.hold[:cut], self.hold[cut:]
            return emit
        emit, self.hold = self.hold, ""
        return emit

    def _pending_start(self, text: str) -> Optional[int]:
        """返回需要继续扣住的下标；没有可疑片段返回 None。"""
        idx = text.rfind("<")
        if idx < 0:
            return None
        tail = text[idx:]
        if len(tail) > self._hold_limit:
            return None
        return idx if _tail_could_be_tool_block(tail) else None


def _tail_could_be_tool_block(tail: str) -> bool:
    """``<`` 开头的一小段是否可能是（已知工具名的）假工具块开头。"""
    if not tail.startswith("<"):
        return False
    match = re.match(r"(?is)<\s*/?\s*([A-Za-z_|\uff5c])", tail)
    if not match:
        # '<' 后面不是标签起始字符（例如 "价格<100"）→ 正常文本
        return False
    name = match.group(1).lower()
    rest = tail[match.end(1):]
    more = re.match(r"[A-Za-z0-9_|\uff5c:.-]{0,40}", rest)
    if more:
        name += more.group(0).lower()
    name = name.strip("|").replace("\uff5c", "")
    if not name:
        return True
    if name in XML_TOOL_TAGS:
        return True
    return any(tag.startswith(name) for tag in XML_TOOL_TAGS)


def guard_for(model_id: str) -> StreamGuard:
    return StreamGuard(profile=profile_for(model_id))


def apply_to_completion(data: Any, profile: ModelProfile) -> bool:
    """清理非流式响应里的假工具调用；有改动返回 True。"""
    if not isinstance(data, dict):
        return False
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    changed = False
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict) and sanitize_message_content(message, profile):
            changed = True
    return changed
