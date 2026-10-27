"""内部 LLM 调用（复用 /api/sutui-chat/completions）。"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

import httpx

from ...core.config import settings

_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _base() -> str:
    raw = (os.environ.get("LOBSTER_INTERNAL_API_BASE") or "").strip().rstrip("/")
    return raw or f"http://127.0.0.1:{int(getattr(settings, 'port', 8000) or 8000)}"


FALLBACK_MODELS = ("deepseek-v4-pro", "deepseek-v4.1-flash", "glm-5.3")


def _model() -> str:
    return ((os.environ.get("PLAN_LLM_MODEL") or "").strip()
            or (getattr(settings, "lobster_default_sutui_chat_model", None) or "").strip()
            or FALLBACK_MODELS[0])


def extract_text(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data or "")
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg = (choices[0] or {}).get("message") or {}
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = [str((c or {}).get("text") or "") for c in content if isinstance(c, dict)]
            joined = "".join(parts).strip()
            if joined:
                return joined
    for key in ("text", "content", "reply"):
        if isinstance(data.get(key), str) and data[key].strip():
            return data[key]
    return ""


async def complete(system: str, user: str, *, model: Optional[str] = None,
                   max_tokens: int = 2000, temperature: float = 0.6, attempts: int = 3) -> str:
    from mcp.sutui_tokens import next_sutui_server_token_with_pool

    base = (getattr(settings, "sutui_api_base", None) or "https://api.xskill.ai").rstrip("/")
    url = f"{base}/v1/chat/completions"
    last: Exception | None = None
    candidates = [model or _model()] + [m for m in FALLBACK_MODELS if m != (model or _model())]
    for attempt in range(max(1, attempts)):
        token, pool = await next_sutui_server_token_with_pool()
        current_model = candidates[min(attempt, len(candidates) - 1)]
        if not token:
            raise RuntimeError("策划生成不可用：未配置速推 Key")
        payload: Dict[str, Any] = {
            "model": current_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=120.0, trust_env=True) as client:
                resp = await client.post(url, json=payload, headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                })
            if resp.status_code in (410, 404) and attempt + 1 < len(candidates):
                await asyncio.sleep(0.4)
                continue
            if resp.status_code in _RETRY_STATUS and attempt + 1 < attempts:
                await asyncio.sleep(1.2 + attempt)
                continue
            data = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                detail = data.get("error") if isinstance(data, dict) else None
                raise RuntimeError(str((detail or {}).get("message") if isinstance(detail, dict) else detail) or f"HTTP {resp.status_code}")
            text = extract_text(data).strip()
            if text:
                return text
            raise RuntimeError("empty completion")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(1.2 + attempt)
    raise RuntimeError(f"LLM 调用失败：{last}")


def parse_json_block(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出里提取第一个 JSON 对象。"""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None
