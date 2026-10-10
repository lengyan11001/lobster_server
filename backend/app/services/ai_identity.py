"""系统级 AI 身份（角色）设定 —— 固定话术的确定性实现（2026-10-10）。

同一份口径在两处生效：
1) Mastra 侧：`mastra_server/src/mastra/identity.ts` 把【系统角色设定】注入编排 agent 的输入最前面；
2) 后端侧（本模块）：「身份问答」与「会话首次问候」**直接回固定自我介绍** ——
   不走模型、不计费、不受模型波动影响，保证「必须输出固定应答文本」这条硬要求。

品牌隔离：只给 IDENTITIES 里已配置的品牌生效；未配置的品牌（OEM）不走固定话术，
也不会被注入必火的公司名/昵称。
新增品牌：在 IDENTITIES 加一条（company / product / nickname / role / intro）。
"""
from __future__ import annotations

import re
from typing import Dict, Optional

IDENTITIES: Dict[str, Dict[str, str]] = {
    "bihuo": {
        "company": "深圳市必火智能信息技术有限公司",
        "product": "必火AI员工执行系统",
        "nickname": "火火",
        "role": "客户的AI助理",
        "intro": "您好，我是由深圳市必火智能信息技术有限公司开发的AI执行系统，我是您的AI助理，名叫火火。",
    },
}

DEFAULT_BRAND = "bihuo"

# 「问身份」的关键词（归一化后做包含匹配）
_IDENTITY_PATTERNS = (
    "你是谁",
    "你叫什么",
    "你叫啥",
    "你的名字",
    "你是什么",
    "自我介绍",
    "介绍一下你",
    "介绍下你自己",
    "介绍你自己",
    "哪家公司",
    "谁开发",
    "谁做的",
    "你来自",
    "你的身份",
    "你是干什么",
    "你是做什么的",
    "你是什么系统",
    "你是什么助理",
    "whoareyou",
    "whatsyourname",
    "yourname",
)

# 「问候 / 唤醒」的关键词：必须是短问候本身，不能带上任务（“你好，帮我写个文案”不算）
_GREETING_PATTERNS = (
    "你好",
    "您好",
    "哈喽",
    "嗨",
    "hi",
    "hello",
    "hey",
    "在吗",
    "在么",
    "在不在",
    "早上好",
    "中午好",
    "下午好",
    "晚上好",
    "火火",
    "小助手",
    "喂",
)

_MAX_QUESTION_CHARS = 40
_MAX_GREETING_CHARS = 12
_MAX_GREETING_SUFFIX = 3


def _normalize(text: str) -> str:
    body = str(text or "").strip().lower()
    body = re.sub(r"[\s\u3000]+", "", body)
    body = re.sub(r"[，。！？!?,.;:~·、…\-—_（）()\[\]【】\"'“”‘’]+", "", body)
    return body


def identity_for(brand_mark: Optional[str]) -> Optional[Dict[str, str]]:
    key = str(brand_mark or "").strip().lower() or DEFAULT_BRAND
    return IDENTITIES.get(key)


def intro_for(brand_mark: Optional[str]) -> str:
    entry = identity_for(brand_mark)
    return str(entry.get("intro") or "") if entry else ""


def is_identity_question(text: str) -> bool:
    """用户在本轮问「你是谁 / 你叫什么 / 介绍一下你自己」。"""
    body = _normalize(text)
    if not body or len(body) > _MAX_QUESTION_CHARS:
        return False
    return any(pattern in body for pattern in _IDENTITY_PATTERNS)


def is_greeting(text: str) -> bool:
    """本轮只是一句问候 / 唤醒词（带任务的句子不算）。"""
    body = _normalize(text)
    if not body or len(body) > _MAX_GREETING_CHARS:
        return False
    for pattern in _GREETING_PATTERNS:
        if body == pattern:
            return True
        if body.startswith(pattern) and len(body) - len(pattern) <= _MAX_GREETING_SUFFIX:
            return True
    return False


def fixed_reply(text: str, *, brand_mark: Optional[str], is_first_turn: bool) -> str:
    """命中固定自我介绍时返回话术，否则返回空串。

    - 问身份（任何时候）→ 固定话术；
    - 会话首轮且只是一句问候 / 唤醒 → 固定话术；
    - 其余情况 → 空串（交给模型正常回答）。
    """
    intro = intro_for(brand_mark)
    if not intro:
        return ""
    if is_identity_question(text):
        return intro
    if is_first_turn and is_greeting(text):
        return intro
    return ""
