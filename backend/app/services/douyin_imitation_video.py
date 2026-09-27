"""抖音信息台「做同款」：用户传一张参考图 -> wan 图生视频（服务器 Comfly key）。

实测（2026-09-27，生产服务器）：
- 可用通道 POST {COMFLY_API_BASE}/v2/videos/generations，图生视频的图走 `images: [url]`；
- 查询 GET {COMFLY_API_BASE}/v2/videos/generations/{task_id}，SUCCESS 时 data.output 是 mp4；
- 该通道支持的 wan 系列白名单到 wan2.6（wan2.2-i2v-plus / wan2.6-i2v / wan2.6-r2v / wan3.0-video …），
  没有 wan2.7，所以默认用 wan2.6-i2v（首帧图生视频）；可用 DOUYIN_IMITATION_MODEL 覆盖。
- 图必须 ≥240px（上游限制），所以前端先走 /api/assets/upload-temp 拿公网 URL。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "wan2.6-i2v"
DEFAULT_DURATION = 5
DEFAULT_RATIO = "9:16"
TERMINAL_STATUSES = {"SUCCESS", "FAILURE", "CANCELED", "CANCELLED"}

PROMPT_TEMPLATE = (
    "参考抖音热门内容「{title}」的题材与节奏，做一条同款风格的竖屏短视频："
    "画面自然运动、镜头平稳推进、主体清晰、光线自然，不要出现文字、logo 或水印。"
)


def _base() -> str:
    base = (os.environ.get("COMFLY_API_BASE") or "").strip().rstrip("/")
    return base or "https://ai.comfly.org"


def _key() -> str:
    return (os.environ.get("DOUYIN_IMITATION_API_KEY") or os.environ.get("COMFLY_API_KEY") or "").strip()


def _model() -> str:
    return (os.environ.get("DOUYIN_IMITATION_MODEL") or DEFAULT_MODEL).strip()


def build_prompt(title: str = "", keyword: str = "", extra: str = "") -> str:
    """把榜单条目的标题/关键词整理成做同款的提示词（用户也可以在弹窗里自己改）。"""
    clean_title = str(title or "").strip()[:60] or "热门内容"
    clean_keyword = str(keyword or "").strip()[:40]
    prompt = PROMPT_TEMPLATE.format(title=clean_title)
    if clean_keyword and clean_keyword != clean_title:
        prompt += f"关键词：{clean_keyword}。"
    tail = str(extra or "").strip()[:200]
    if tail:
        prompt += f"补充要求：{tail}"
    return prompt[:800]


def _clean_duration(value: Any) -> int:
    try:
        duration = int(float(value))
    except (TypeError, ValueError):
        duration = DEFAULT_DURATION
    return max(2, min(10, duration)) if duration else DEFAULT_DURATION


def _clean_ratio(value: Any) -> str:
    ratio = str(value or "").strip()
    return ratio if ratio in {"9:16", "16:9", "1:1"} else DEFAULT_RATIO


async def submit_imitation(image_url: str, prompt: str, *, duration: Any = None, ratio: Any = None) -> Dict[str, Any]:
    """提交一条做同款任务，返回 {ok, task_id, model, prompt} 或 {ok: False, error}。"""
    image = str(image_url or "").strip()
    if not image.startswith(("http://", "https://")):
        return {"ok": False, "error": "参考图地址无效，请重新上传"}
    key = _key()
    if not key:
        return {"ok": False, "error": "服务端未配置视频模型 Key（COMFLY_API_KEY）"}
    model = _model()
    body = {
        "model": model,
        "prompt": str(prompt or "").strip()[:800] or build_prompt(),
        "images": [image],
        "ratio": _clean_ratio(ratio),
        "duration": _clean_duration(duration),
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0), trust_env=False) as client:
            resp = await client.post(
                _base() + "/v2/videos/generations",
                json=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"提交做同款失败：{exc}"}
    if resp.status_code != 200:
        return {"ok": False, "error": f"视频模型返回 HTTP {resp.status_code}：{resp.text[:200]}"}
    try:
        task_id = str((resp.json() or {}).get("task_id") or "").strip()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"解析视频任务失败：{exc}"}
    if not task_id:
        return {"ok": False, "error": f"视频模型没有返回任务号：{resp.text[:200]}"}
    logger.info("[douyin-imitation] submit model=%s task=%s", model, task_id)
    return {"ok": True, "task_id": task_id, "model": model, "prompt": body["prompt"],
            "duration": body["duration"], "ratio": body["ratio"], "image_url": image}


async def query_imitation(task_id: str) -> Dict[str, Any]:
    """查询做同款任务：{ok, status, progress, video_url, fail_reason}。"""
    clean_id = str(task_id or "").strip()
    if not clean_id:
        return {"ok": False, "error": "缺少任务号"}
    key = _key()
    if not key:
        return {"ok": False, "error": "服务端未配置视频模型 Key（COMFLY_API_KEY）"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=15.0), trust_env=False) as client:
            resp = await client.get(
                _base() + "/v2/videos/generations/" + clean_id,
                headers={"Authorization": f"Bearer {key}"},
            )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"查询做同款失败：{exc}"}
    if resp.status_code != 200:
        return {"ok": False, "error": f"视频模型返回 HTTP {resp.status_code}：{resp.text[:200]}"}
    try:
        data = resp.json() or {}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"解析做同款结果失败：{exc}"}
    status = str(data.get("status") or "").upper()
    output = str(((data.get("data") or {}) or {}).get("output") or "").strip()
    return {
        "ok": True,
        "task_id": clean_id,
        "status": status,
        "progress": str(data.get("progress") or ""),
        "video_url": output if status == "SUCCESS" and output else "",
        "fail_reason": str(data.get("fail_reason") or ""),
        "done": status in TERMINAL_STATUSES,
        "cost": data.get("cost"),
    }
