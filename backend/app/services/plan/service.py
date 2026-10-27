"""策划层执行：拼 prompt → 调内部 LLM → 结构化后处理。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from . import llm, scoring
from .skills import SKILLS, SYSTEM

_HOOK_LINE = re.compile(r"^>\s*(.+)$", re.M)
_JSON_FENCE = re.compile(r"```(?:json)?\s*\{.*?\}\s*```", re.S)


def _strip_json(text: str) -> str:
    """去掉模型输出里的 JSON 块（含 ```json 围栏），保留其余建议。"""
    stripped = _JSON_FENCE.sub("", text or "")
    if stripped == text:
        start = text.find("{")
        end = text.rfind("}")
        if 0 <= start < end:
            stripped = text[:start] + text[end + 1:]
    return stripped.strip()


def _build_user(action: str, params: Dict[str, Any]) -> str:
    spec = SKILLS[action]
    payload = json.dumps(params or {}, ensure_ascii=False)
    return (
        f"任务：{spec['name']}\n"
        f"用户输入：{payload}\n\n"
        f"执行框架：\n{spec['guide']}\n\n"
        f"输出要求：{spec['output']}"
    )


def _hook_report(text: str) -> Dict[str, Any]:
    lines = [m.group(1).strip() for m in _HOOK_LINE.finditer(text)]
    long_lines = [x for x in lines if len(x) > 40]
    return {"hook_count": len(lines) // 2, "over_limit": long_lines}


async def run(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if action not in SKILLS:
        raise KeyError(f"unknown plan skill: {action}")
    text = await llm.complete(SYSTEM, _build_user(action, params), max_tokens=2200)

    if action == "plan_topic_eval":
        data = llm.parse_json_block(text)
        scores = (data or {}).get("scores") if isinstance(data, dict) else None
        if isinstance(scores, dict) and scores:
            body = _strip_json(text)
            parts = [body] if body else []
            parts.append(scoring.table(scores))
            alternatives = (data or {}).get("alternatives") if isinstance(data, dict) else None
            if isinstance(alternatives, list) and alternatives:
                parts.append("**替代选题**：" + "；".join(str(x) for x in alternatives[:3]))
            return {"text": "\n\n".join(parts), "data": data}
        return {"text": text + "\n\n> 未解析到七维分数，未做加权计算。", "data": None}

    if action == "plan_hook":
        report = _hook_report(text)
        if report["over_limit"]:
            text += "\n\n> 超过 40 字的行：" + "；".join(report["over_limit"][:5])
        return {"text": text, "data": report}

    return {"text": text, "data": None}


def selftest() -> List[str]:
    problems: List[str] = []
    if len(SKILLS) < 16:
        problems.append(f"skills < 16: {len(SKILLS)}")
    for key, spec in SKILLS.items():
        for field in ("name", "description", "keywords", "args", "guide", "output"):
            if not spec.get(field):
                problems.append(f"{key}: missing {field}")
    problems.extend(scoring.selftest())
    if _hook_report("### 1. x\n> 短行\n> 另一行")["hook_count"] != 1:
        problems.append("hook count wrong")
    return problems
