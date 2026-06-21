from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional


DEFAULT_HANDOFF_KEYWORDS = (
    "人工",
    "转人工",
    "真人",
    "投诉",
    "退款",
    "退货",
    "合同",
    "发票",
    "银行卡",
    "法律",
    "隐私",
    "删除数据",
)


@dataclass
class CustomerServiceDecision:
    action: str
    reply_text: str = ""
    reason: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "action": self.action,
            "reply_text": self.reply_text,
            "reason": self.reason,
        }


def parse_handoff_keywords(raw: Optional[str]) -> List[str]:
    text = (raw or "").strip()
    if not text:
        return list(DEFAULT_HANDOFF_KEYWORDS)
    parts: List[str] = []
    for sep in ("\n", ",", "，", ";", "；", "|"):
        text = text.replace(sep, "\n")
    for item in text.splitlines():
        val = item.strip()
        if val and val not in parts:
            parts.append(val)
    return parts or list(DEFAULT_HANDOFF_KEYWORDS)


def should_handoff(user_message: str, keywords: List[str]) -> Optional[str]:
    text = (user_message or "").strip()
    if not text:
        return "empty_message"
    lowered = text.lower()
    for keyword in keywords:
        key = (keyword or "").strip()
        if key and key.lower() in lowered:
            return f"matched_keyword:{key}"
    return None


async def run_customer_service_agent(
    *,
    user_message: str,
    history: List[Dict[str, str]],
    knowledge: str,
    prompt: str,
    handoff_keywords: Optional[str],
    reply_generator: Callable[[str, List[Dict[str, str]], str, str], Awaitable[str]],
) -> CustomerServiceDecision:
    text = (user_message or "").strip()
    if not text:
        return CustomerServiceDecision(action="ignore", reason="empty_message")

    handoff_reason = should_handoff(text, parse_handoff_keywords(handoff_keywords))
    if handoff_reason:
        return CustomerServiceDecision(action="handoff", reason=handoff_reason)

    reply = (await reply_generator(text, history, knowledge, prompt) or "").strip()
    if not reply:
        return CustomerServiceDecision(action="failed", reason="empty_reply")
    return CustomerServiceDecision(action="reply", reply_text=reply)
