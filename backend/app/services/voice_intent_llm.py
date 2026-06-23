from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict

import httpx

from ..core.config import settings
from .xfyun_realtime_asr import resolve_voice_intent

logger = logging.getLogger(__name__)

_RETRY_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
_ALLOWED_INTENTS = {
    "unknown",
    "chat_freeform",
    "image_generate",
    "video_generate",
    "creative_storyboard_video",
    "douyin_leads_task",
    "copywriting",
    "schedule_task",
}
_ALLOWED_EXECUTION_MODES = {"direct", "draft", "ask_followup"}

_VOICE_INTENT_SYSTEM_PROMPT = """
你是龙虾盒子 H5 语音助手的“任务理解器”，只负责把用户说的话解析成结构化任务，不要闲聊，不要解释。

请根据用户原话，判断它更接近哪一类意图，并提取关键参数：
1. image_generate: 图片创作、海报、封面、详情图、配图
2. video_generate: 普通视频生成、口播视频、视频复刻
3. creative_storyboard_video: 创意分镜、分镜脚本、按时长生成多个片段再合成
4. douyin_leads_task: 抖音获客、采集客户、视频评论、私信、同行监控、养号
5. copywriting: 纯文案、脚本、标题、口播稿、详情页文案
6. schedule_task: 定时、排期、稍后执行、每天执行
7. chat_freeform: 普通对话或泛化需求
8. unknown: 信息太少，无法判断

必须返回严格 JSON 对象，不要 Markdown，不要代码块，不要补充说明。

字段要求：
{
  "intent": "creative_storyboard_video",
  "confidence": 0.0,
  "need_confirm": true,
  "execution_mode": "direct|draft|ask_followup",
  "draft_text": "用户原话或适合回填的文本",
  "slots": {},
  "missing_slots": [],
  "actions": [
    {
      "label": "生成创意分镜",
      "kind": "submit_message",
      "payload": {
        "content": "..."
      }
    }
  ]
}

补充规则：
- duration_seconds 用数字，例如 20，不要写 "20秒"。
- 如果用户表达的是“创意分镜 20 秒”这类需求，优先判断为 creative_storyboard_video。
- 如果是抖音获客相关，把动作归并为 douyin_leads_task，并尽量提取 action、keyword、region、comment_mode、comment_text、dm_text。
- 如果信息不足，need_confirm=true，execution_mode=ask_followup，并把缺失参数放到 missing_slots。
- actions 可以为空，但如果能直接执行，尽量给出 submit_message / edit_message 两类动作。
- 返回中文 label，payload.content 尽量使用用户原意整理后的文本。
""".strip()


def _internal_api_base() -> str:
    raw = (os.environ.get("LOBSTER_INTERNAL_API_BASE") or "").strip().rstrip("/")
    if raw:
        return raw
    return f"http://127.0.0.1:{int(getattr(settings, 'port', 8000) or 8000)}"


def _voice_intent_model() -> str:
    return (
        (os.environ.get("H5_VOICE_INTENT_MODEL") or "").strip()
        or (getattr(settings, "lobster_default_sutui_chat_model", None) or "").strip()
        or "deepseek-chat"
    )


def _retry_delay(attempt_index: int) -> float:
    return min(5.0, 1.0 + attempt_index * 1.2)


def _response_message(data: Any) -> str:
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err.get("message"))
        if data.get("message"):
            return str(data.get("message"))
        detail = data.get("detail")
        if detail:
            return str(detail)
    return str(data or "")


def _extract_content_text(data: Any) -> str:
    try:
        message = data["choices"][0]["message"]
    except Exception:
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _strip_code_fence(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = _strip_code_fence(text)
    if not cleaned:
        return {}
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _as_list_of_text(value: Any) -> list[str]:
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                out.append(text[:120])
        return out[:12]
    return []


def _confidence_value(value: Any, default: float = 0.78) -> float:
    try:
        num = float(value)
    except Exception:
        num = default
    if num < 0:
        return 0.0
    if num > 1:
        return 1.0
    return round(num, 4)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _find_first_int(value: Any) -> int | None:
    if value is None:
        return None
    m = re.search(r"(\d{1,3})", str(value))
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _detect_duration_seconds(content: str) -> int | None:
    text = _clean_text(content)
    m = re.search(r"(\d{1,3})\s*秒", text)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def _detect_aspect_ratio(content: str) -> str:
    text = _clean_text(content).replace("：", ":").replace("比", ":")
    for ratio in ("9:16", "16:9", "1:1", "3:4", "4:3", "21:9"):
        if ratio in text:
            return ratio
    if "竖屏" in text:
        return "9:16"
    if "横屏" in text:
        return "16:9"
    return ""


def _extract_keyword_after_marker(content: str, marker: str) -> str:
    text = _clean_text(content)
    m = re.search(rf"{re.escape(marker)}[:： ]*([^，。；;,.]+)", text)
    return _clean_text(m.group(1)) if m else ""


def _is_image_request(content: str) -> bool:
    return any(
        word in content for word in (
            "图片", "海报", "封面", "配图", "详情图", "主图", "图生图", "生成一张", "做一张", "画一张"
        )
    )


def _is_storyboard_request(content: str) -> bool:
    if "创意分镜" in content or "分镜" in content:
        return True
    return bool(_detect_duration_seconds(content) and "视频" in content)


def _is_video_request(content: str) -> bool:
    return any(
        word in content for word in ("视频", "成片", "口播视频", "复刻视频", "生成视频", "做个视频")
    )


def _is_douyin_leads_request(content: str) -> bool:
    return any(
        word in content for word in ("抖音获客", "采集客户", "客户采集", "视频评论", "私信", "同行监控", "养号", "评论客户")
    )


def _is_copywriting_request(content: str) -> bool:
    return any(
        word in content for word in ("文案", "口播稿", "标题", "脚本", "详情页文案", "写一段", "写一个")
    )


def _is_schedule_request(content: str) -> bool:
    return any(
        word in content for word in ("明天", "后天", "今晚", "上午", "下午", "晚上", "定时", "每天", "排期", "安排在")
    )


def _heuristic_actions(intent: str, content: str) -> list[dict[str, Any]]:
    content = _clean_text(content)
    if intent == "creative_storyboard_video":
        return [
            {"label": "生成创意分镜", "kind": "submit_message", "payload": {"content": content}},
            {"label": "继续补充", "kind": "edit_message", "payload": {"content": content}},
        ]
    if intent == "image_generate":
        return [
            {"label": "生成图片", "kind": "submit_message", "payload": {"content": content}},
            {"label": "继续微调", "kind": "edit_message", "payload": {"content": content}},
        ]
    if intent == "douyin_leads_task":
        return [
            {"label": "安排获客任务", "kind": "submit_message", "payload": {"content": content}},
            {"label": "继续补充", "kind": "edit_message", "payload": {"content": content}},
        ]
    if intent == "copywriting":
        return [
            {"label": "发送到电脑端", "kind": "submit_message", "payload": {"content": content}},
            {"label": "继续编辑", "kind": "edit_message", "payload": {"content": content}},
        ]
    if intent == "schedule_task":
        return [
            {"label": "安排定时任务", "kind": "submit_message", "payload": {"content": content}},
            {"label": "继续补充", "kind": "edit_message", "payload": {"content": content}},
        ]
    if intent == "video_generate":
        return [
            {"label": "生成视频", "kind": "submit_message", "payload": {"content": content}},
            {"label": "继续补充", "kind": "edit_message", "payload": {"content": content}},
        ]
    return _default_actions(intent, content)


def _heuristic_intent_payload(content: str) -> Dict[str, Any] | None:
    text = _clean_text(content)
    if not text:
        return None

    slots: dict[str, Any] = {}
    duration_seconds = _detect_duration_seconds(text)
    if duration_seconds:
        slots["duration_seconds"] = duration_seconds
    aspect_ratio = _detect_aspect_ratio(text)
    if aspect_ratio:
        slots["aspect_ratio"] = aspect_ratio
    keyword = _extract_keyword_after_marker(text, "关键词")
    if keyword:
        slots["keyword"] = keyword

    if _is_storyboard_request(text):
        intent = "creative_storyboard_video"
        return {
            "intent": intent,
            "confidence": 0.93,
            "need_confirm": False,
            "execution_mode": "direct",
            "draft_text": text,
            "slots": slots,
            "missing_slots": [],
            "actions": _heuristic_actions(intent, text),
        }

    if _is_image_request(text):
        intent = "image_generate"
        return {
            "intent": intent,
            "confidence": 0.91,
            "need_confirm": False,
            "execution_mode": "direct",
            "draft_text": text,
            "slots": slots,
            "missing_slots": [],
            "actions": _heuristic_actions(intent, text),
        }

    if _is_douyin_leads_request(text):
        intent = "douyin_leads_task"
        action = ""
        if "采集" in text:
            action = "search_collect"
        elif "评论" in text:
            action = "video_comment"
        elif "私信" in text:
            action = "direct_message"
        elif "同行" in text:
            action = "monitor_peer"
        if action:
            slots["action"] = action
        return {
            "intent": intent,
            "confidence": 0.92,
            "need_confirm": False,
            "execution_mode": "direct",
            "draft_text": text,
            "slots": slots,
            "missing_slots": [],
            "actions": _heuristic_actions(intent, text),
        }

    if _is_schedule_request(text):
        intent = "schedule_task"
        return {
            "intent": intent,
            "confidence": 0.82,
            "need_confirm": True,
            "execution_mode": "draft",
            "draft_text": text,
            "slots": slots,
            "missing_slots": [],
            "actions": _heuristic_actions(intent, text),
        }

    if _is_copywriting_request(text):
        intent = "copywriting"
        return {
            "intent": intent,
            "confidence": 0.88,
            "need_confirm": False,
            "execution_mode": "direct",
            "draft_text": text,
            "slots": slots,
            "missing_slots": [],
            "actions": _heuristic_actions(intent, text),
        }

    if _is_video_request(text):
        intent = "video_generate"
        return {
            "intent": intent,
            "confidence": 0.84,
            "need_confirm": False,
            "execution_mode": "direct",
            "draft_text": text,
            "slots": slots,
            "missing_slots": [],
            "actions": _heuristic_actions(intent, text),
        }
    return None


def _merge_heuristic_result(content: str, result: Dict[str, Any]) -> Dict[str, Any]:
    heuristic = _heuristic_intent_payload(content)
    if not heuristic:
        return result

    current_intent = _clean_text(result.get("intent")).lower()
    current_confidence = _confidence_value(result.get("confidence"), 0.0)
    current_actions = result.get("actions") if isinstance(result.get("actions"), list) else []
    only_edit_action = bool(current_actions) and all(
        isinstance(item, dict) and _clean_text(item.get("kind")) == "edit_message"
        for item in current_actions
    )

    should_override = (
        current_intent in {"", "unknown", "chat_freeform"}
        or current_confidence < 0.6
        or only_edit_action
    )
    if not should_override:
        # Keep LLM's primary intent but enrich missing slots or actions when useful.
        merged_slots = dict(heuristic.get("slots") or {})
        merged_slots.update(result.get("slots") if isinstance(result.get("slots"), dict) else {})
        result["slots"] = merged_slots
        if not current_actions:
            result["actions"] = heuristic.get("actions") or []
        return result

    merged = dict(result)
    merged.update({
        "intent": heuristic["intent"],
        "confidence": max(current_confidence, _confidence_value(heuristic.get("confidence"), 0.8)),
        "need_confirm": bool(heuristic.get("need_confirm")),
        "execution_mode": _clean_text(heuristic.get("execution_mode")) or "direct",
        "draft_text": _clean_text(heuristic.get("draft_text")) or content,
        "slots": heuristic.get("slots") or {},
        "missing_slots": heuristic.get("missing_slots") or [],
        "actions": heuristic.get("actions") or [],
    })
    merged["provider"] = f"{_clean_text(result.get('provider')) or 'llm'}+heuristic"
    return merged


def _should_short_circuit_with_heuristic(heuristic: Dict[str, Any] | None) -> bool:
    if not isinstance(heuristic, dict):
        return False
    intent = _clean_text(heuristic.get("intent")).lower()
    if intent not in {
        "image_generate",
        "video_generate",
        "creative_storyboard_video",
        "douyin_leads_task",
    }:
        return False
    if bool(heuristic.get("need_confirm")):
        return False
    if _clean_text(heuristic.get("execution_mode")).lower() != "direct":
        return False
    return True


def _default_actions(intent: str, content: str) -> list[dict[str, Any]]:
    if intent == "douyin_leads_task":
        return [
            {"label": "安排抖音获客任务", "kind": "edit_message", "payload": {"content": content}},
            {"label": "直接发送", "kind": "submit_message", "payload": {"content": content}},
        ]
    if intent in {"creative_storyboard_video", "video_generate"}:
        return [
            {"label": "生成视频", "kind": "submit_message", "payload": {"content": content}},
            {"label": "继续补充", "kind": "edit_message", "payload": {"content": content}},
        ]
    if intent == "image_generate":
        return [
            {"label": "生成图片", "kind": "submit_message", "payload": {"content": content}},
            {"label": "继续补充", "kind": "edit_message", "payload": {"content": content}},
        ]
    if intent in {"copywriting", "schedule_task", "chat_freeform"}:
        return [
            {"label": "发送到电脑端", "kind": "submit_message", "payload": {"content": content}},
            {"label": "继续编辑", "kind": "edit_message", "payload": {"content": content}},
        ]
    return [{"label": "继续补充", "kind": "edit_message", "payload": {"content": content}}]


def _normalize_actions(value: Any, content: str, intent: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value[:4]:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip()
            if kind not in {"submit_message", "edit_message"}:
                continue
            label = str(item.get("label") or "").strip()[:40]
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            next_content = str(payload.get("content") or content).strip() or content
            actions.append(
                {
                    "label": label or ("直接发送" if kind == "submit_message" else "继续编辑"),
                    "kind": kind,
                    "payload": {"content": next_content[:4000]},
                }
            )
    return actions or _default_actions(intent, content)


def _normalize_intent_payload(raw: Dict[str, Any], text: str) -> Dict[str, Any]:
    content = str(text or "").strip()
    intent = str(raw.get("intent") or "").strip().lower()
    if intent not in _ALLOWED_INTENTS:
        intent = "chat_freeform" if content else "unknown"

    execution_mode = str(raw.get("execution_mode") or "").strip().lower()
    if execution_mode not in _ALLOWED_EXECUTION_MODES:
        execution_mode = "ask_followup" if bool(raw.get("need_confirm")) else "direct"

    slots = raw.get("slots") if isinstance(raw.get("slots"), dict) else {}
    missing_slots = _as_list_of_text(raw.get("missing_slots"))
    draft_text = str(raw.get("draft_text") or content).strip() or content
    need_confirm = bool(raw.get("need_confirm"))
    if missing_slots:
        need_confirm = True
        if execution_mode == "direct":
            execution_mode = "ask_followup"

    return {
        "intent": intent,
        "confidence": _confidence_value(raw.get("confidence"), 0.82),
        "need_confirm": need_confirm,
        "execution_mode": execution_mode,
        "draft_text": draft_text[:4000],
        "slots": slots,
        "missing_slots": missing_slots,
        "actions": _normalize_actions(raw.get("actions"), draft_text, intent),
        "provider": "llm",
    }


async def _post_llm_with_retry(*, payload: Dict[str, Any], headers: Dict[str, str], attempts: int = 2) -> Dict[str, Any]:
    tries = max(1, int(attempts or 1))
    last_detail = ""
    for idx in range(tries):
        try:
            async with httpx.AsyncClient(timeout=90.0, trust_env=False) as client:
                resp = await client.post(
                    f"{_internal_api_base()}/api/sutui-chat/completions",
                    json=payload,
                    headers=headers,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_detail = str(exc)
            if idx >= tries - 1:
                raise RuntimeError(last_detail or "voice intent llm transport error") from exc
            await asyncio.sleep(_retry_delay(idx))
            continue

        try:
            data = resp.json() if resp.content else {}
        except Exception:
            data = {"text": resp.text[:10000]}

        if resp.status_code < 400:
            return data if isinstance(data, dict) else {}

        detail = _response_message(data) or f"HTTP {resp.status_code}"
        last_detail = detail
        if resp.status_code not in _RETRY_HTTP_STATUSES:
            raise RuntimeError(detail)
        if idx >= tries - 1:
            raise RuntimeError(detail)
        await asyncio.sleep(_retry_delay(idx))

    raise RuntimeError(last_detail or "voice intent llm failed")


async def _post_direct_deepseek_with_retry(*, payload: Dict[str, Any], attempts: int = 2) -> Dict[str, Any]:
    api_key = str(getattr(settings, "deepseek_api_key", None) or "").strip()
    if not api_key:
        raise RuntimeError("deepseek_api_key not configured")

    api_base = (getattr(settings, "deepseek_api_base", None) or "https://api.deepseek.com").strip().rstrip("/")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    tries = max(1, int(attempts or 1))
    last_detail = ""
    for idx in range(tries):
        try:
            async with httpx.AsyncClient(timeout=90.0, trust_env=True) as client:
                resp = await client.post(
                    f"{api_base}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_detail = str(exc)
            if idx >= tries - 1:
                raise RuntimeError(last_detail or "deepseek transport error") from exc
            await asyncio.sleep(_retry_delay(idx))
            continue

        try:
            data = resp.json() if resp.content else {}
        except Exception:
            data = {"text": resp.text[:10000]}

        if resp.status_code < 400:
            return data if isinstance(data, dict) else {}

        detail = _response_message(data) or f"HTTP {resp.status_code}"
        last_detail = detail
        if resp.status_code not in _RETRY_HTTP_STATUSES:
            raise RuntimeError(detail)
        if idx >= tries - 1:
            raise RuntimeError(detail)
        await asyncio.sleep(_retry_delay(idx))

    raise RuntimeError(last_detail or "deepseek llm failed")


async def resolve_voice_intent_with_llm(
    *,
    text: str,
    token: str = "",
    installation_id: str = "",
) -> Dict[str, Any]:
    content = str(text or "").strip()
    if not content:
        return resolve_voice_intent(content)

    heuristic = _heuristic_intent_payload(content)
    if _should_short_circuit_with_heuristic(heuristic):
        heuristic = dict(heuristic or {})
        heuristic["provider"] = "heuristic_fastpath"
        logger.info(
            "[h5_voice] heuristic fastpath intent=%s confidence=%s text=%s",
            heuristic.get("intent"),
            heuristic.get("confidence"),
            content[:120],
        )
        return heuristic

    bearer_token = str(token or "").strip()
    payload = {
        "model": _voice_intent_model(),
        "messages": [
            {"role": "system", "content": _VOICE_INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "stream": False,
        "temperature": 0.2,
    }

    try:
        data: Dict[str, Any]
        provider = "llm_proxy"
        if bearer_token:
            headers = {
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            if installation_id.strip():
                headers["X-Installation-Id"] = installation_id.strip()
            try:
                data = await _post_llm_with_retry(payload=payload, headers=headers, attempts=2)
            except Exception as proxy_exc:
                logger.warning("[h5_voice] llm proxy intent failed, fallback direct deepseek err=%s", proxy_exc)
                data = await _post_direct_deepseek_with_retry(payload=payload, attempts=2)
                provider = "deepseek_direct"
        else:
            data = await _post_direct_deepseek_with_retry(payload=payload, attempts=2)
            provider = "deepseek_direct"

        response_text = _extract_content_text(data)
        parsed = _extract_json_object(response_text)
        if not parsed:
            raise RuntimeError(f"intent llm returned non-json content: {response_text[:240]}")
        result = _normalize_intent_payload(parsed, content)
        result["provider"] = provider
        result = _merge_heuristic_result(content, result)
        logger.info(
            "[h5_voice] llm intent resolved provider=%s intent=%s confidence=%s text=%s",
            result.get("provider") or provider,
            result.get("intent"),
            result.get("confidence"),
            content[:120],
        )
        return result
    except Exception as exc:
        logger.warning("[h5_voice] llm intent fallback err=%s text=%s", exc, content[:120])
        if heuristic:
            heuristic["provider"] = "heuristic_fallback"
            return heuristic
        fallback = resolve_voice_intent(content)
        fallback["provider"] = "fallback_rule"
        return fallback
