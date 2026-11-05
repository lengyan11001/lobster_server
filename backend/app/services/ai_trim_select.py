from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是一个短视频剪辑师。用户给你一段长视频的逐句转写，每句带序号、起止时间（秒）和文本。"
    "请判断哪些句子是有效内容，哪些应该删掉。规则："
    "1) 删掉纯口气词、嗯啊哦、重复句、明显跑题、无信息量的寒暄；"
    "2) 保留观点、步骤、数据、结论、和金句；"
    "3) 不要为了凑时长删掉有效内容，也不要按时间截断；"
    "4) 只输出 JSON：{\"keep\": [序号...], \"drop\": [序号...], \"reason\": \"一句话说明\"}，不要输出其它文字。"
)


def _model() -> str:
    # 留空 = 交给服务端默认链路（优先 deepseek），用户充值后自动生效
    return (getattr(settings, "lobster_default_sutui_chat_model", None) or "").strip()


def _base() -> str:
    return f"http://127.0.0.1:{int(getattr(settings, 'port', 8000) or 8000)}"


def _message_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return ""


def _content_text(data: Dict[str, Any]) -> str:
    try:
        text = _message_text(data["choices"][0]["message"])
        if text.strip():
            return text
    except Exception:
        pass
    try:
        text = _message_text(data["choices"][0])
        if text.strip():
            return text
    except Exception:
        pass
    for key in ("text", "content", "output_text", "result", "response"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _extract_json(text: str) -> Dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(raw[start : end + 1])
    except Exception:
        return {}


async def select_sentences(
    sentences: List[Dict[str, Any]],
    *,
    target_seconds: float = 0,
    token: str = "",
    installation_id: str = "",
) -> Dict[str, Any]:
    lines = []
    for index, item in enumerate(sentences):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        lines.append(
            "%d. [%.2f-%.2f] %s" % (index, float(item.get("start") or 0), float(item.get("end") or 0), text[:200])
        )
    if not lines:
        return {"keep": [], "reason": "empty transcript"}

    hint = f"目标时长约 {target_seconds:.0f} 秒（仅作参考上限，不要为凑时长删有效内容）。" if target_seconds else "没有目标时长，按内容决定长度。"
    payload = {
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": hint + "\n\n" + "\n".join(lines)},
        ],
        "stream": False,
        "temperature": 0.1,
    }
    model = _model()
    if model:
        payload["model"] = model
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if installation_id:
        headers["X-Installation-Id"] = installation_id

    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        resp = await client.post(f"{_base()}/api/sutui-chat/completions", json=payload, headers=headers)
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        raise RuntimeError(str(data.get("detail") or data.get("message") or f"HTTP {resp.status_code}"))
    parsed = _extract_json(_content_text(data if isinstance(data, dict) else {}))
    keep = parsed.get("keep")
    if not isinstance(keep, list):
        raise RuntimeError("LLM 没有返回 keep 列表")
    keep_ids = []
    for value in keep:
        try:
            idx = int(value)
        except Exception:
            continue
        if 0 <= idx < len(sentences):
            keep_ids.append(idx)
    return {"keep": sorted(set(keep_ids)), "reason": str(parsed.get("reason") or "")[:200]}
