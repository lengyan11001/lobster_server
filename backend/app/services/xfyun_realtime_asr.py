from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

from ..core.config import settings


def xfyun_is_configured() -> bool:
    return bool(
        str(getattr(settings, "xfyun_app_id", "") or "").strip()
        and str(getattr(settings, "xfyun_api_key", "") or "").strip()
        and str(getattr(settings, "xfyun_api_secret", "") or "").strip()
    )


def xfyun_missing_config_fields() -> List[str]:
    missing: List[str] = []
    if not str(getattr(settings, "xfyun_app_id", "") or "").strip():
        missing.append("xfyun_app_id")
    if not str(getattr(settings, "xfyun_api_key", "") or "").strip():
        missing.append("xfyun_api_key")
    if not str(getattr(settings, "xfyun_api_secret", "") or "").strip():
        missing.append("xfyun_api_secret")
    return missing


def build_xfyun_iat_ws_url() -> str:
    base_url = str(getattr(settings, "xfyun_iat_ws_url", "") or "").strip() or "wss://iat-api.xfyun.cn/v2/iat"
    api_key = str(getattr(settings, "xfyun_api_key", "") or "").strip()
    api_secret = str(getattr(settings, "xfyun_api_secret", "") or "").strip()
    parsed = urlparse(base_url)
    host = parsed.netloc
    path = parsed.path or "/v2/iat"
    date = format_datetime(datetime.now(timezone.utc), usegmt=True)
    signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    signature_sha = hmac.new(api_secret.encode("utf-8"), signature_origin.encode("utf-8"), digestmod=hashlib.sha256).digest()
    signature = base64.b64encode(signature_sha).decode("utf-8")
    authorization_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")
    return (
        f"{base_url}?authorization={quote(authorization)}"
        f"&date={quote(date)}&host={quote(host)}"
    )


def build_xfyun_first_frame(audio: bytes) -> Dict[str, Any]:
    return {
        "common": {"app_id": str(getattr(settings, "xfyun_app_id", "") or "").strip()},
        "business": {
            "language": str(getattr(settings, "xfyun_iat_language", "") or "zh_cn"),
            "domain": str(getattr(settings, "xfyun_iat_domain", "") or "iat"),
            "accent": str(getattr(settings, "xfyun_iat_accent", "") or "mandarin"),
            "vad_eos": int(getattr(settings, "xfyun_iat_vad_eos", 1800) or 1800),
            "dwa": str(getattr(settings, "xfyun_iat_dwa", "") or "wpgs"),
        },
        "data": {
            "status": 0,
            "format": "audio/L16;rate=16000",
            "encoding": "raw",
            "audio": base64.b64encode(audio).decode("utf-8"),
        },
    }


def build_xfyun_continue_frame(audio: bytes) -> Dict[str, Any]:
    return {
        "data": {
            "status": 1,
            "format": "audio/L16;rate=16000",
            "encoding": "raw",
            "audio": base64.b64encode(audio).decode("utf-8"),
        }
    }


def build_xfyun_last_frame() -> Dict[str, Any]:
    return {
        "data": {
            "status": 2,
            "format": "audio/L16;rate=16000",
            "encoding": "raw",
            "audio": "",
        }
    }


@dataclass
class XfyunTranscriptState:
    segments: Dict[int, str] = field(default_factory=dict)
    last_text: str = ""
    final_text: str = ""
    finished: bool = False

    def _flatten(self) -> str:
        parts = [self.segments[key] for key in sorted(self.segments.keys())]
        return "".join(parts).strip()

    def _parse_range(self, value: Any) -> tuple[int, int] | None:
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return None
        try:
            start = int(value[0] or 0)
            end = int(value[1] or 0)
        except Exception:
            return None
        if start <= 0 or end <= 0:
            return None
        if start > end:
            start, end = end, start
        return start, end

    def _apply_segment(self, *, sn: int, text: str, pgs: str, rg: Any) -> None:
        if not text:
            return

        # XFYun wpgs incremental mode:
        # - pgs=apd: append new segment
        # - pgs=rpl: replace the previous range from rg=[start,end]
        # Current bug came from treating every update as append, which duplicates
        # phrases after each streaming correction.
        if pgs == "rpl":
            replace_range = self._parse_range(rg)
            if replace_range:
                start, end = replace_range
                for key in [k for k in self.segments.keys() if start <= int(k) <= end]:
                    self.segments.pop(key, None)
        self.segments[sn] = text

    def apply_payload(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None
        code = int(payload.get("code") or 0)
        if code != 0:
            return {
                "type": "error",
                "code": code,
                "message": str(payload.get("message") or payload.get("msg") or "讯飞实时识别失败"),
            }
        data = payload.get("data") or {}
        result = data.get("result") or {}
        if not isinstance(result, dict):
            return None
        ws_rows = result.get("ws") or []
        text = ""
        for item in ws_rows:
            if not isinstance(item, dict):
                continue
            for cw in item.get("cw") or []:
                if not isinstance(cw, dict):
                    continue
                text += str(cw.get("w") or "")
        sn = int(result.get("sn") or 0)
        pgs = str(result.get("pgs") or "").strip().lower()
        rg = result.get("rg")
        if text:
            self._apply_segment(sn=sn, text=text, pgs=pgs, rg=rg)
        merged = self._flatten()
        if merged:
            self.last_text = merged
        is_final = bool(result.get("ls"))
        if is_final:
            self.final_text = merged or self.last_text
            self.finished = True
            return {"type": "final", "text": self.final_text, "is_final": True}
        if merged:
            return {"type": "partial", "text": merged, "is_final": False}
        return None


def resolve_voice_intent(text: str) -> Dict[str, Any]:
    content = str(text or "").strip()
    lowered = content.lower()
    if not content:
        return {
            "intent": "unknown",
            "confidence": 0.0,
            "need_confirm": True,
            "draft_text": "",
            "actions": [],
        }

    if any(word in content for word in ("采集客户", "抖音获客", "私信", "评论", "同行监控")):
        return {
            "intent": "douyin_leads_task",
            "confidence": 0.92,
            "need_confirm": True,
            "draft_text": content,
            "actions": [
                {"label": "安排抖音获客任务", "kind": "edit_message", "payload": {"content": content}},
                {"label": "直接发送", "kind": "submit_message", "payload": {"content": content}},
            ],
        }

    if any(word in content for word in ("视频", "宣传片", "分镜", "口播", "复刻")):
        return {
            "intent": "video_generate",
            "confidence": 0.9,
            "need_confirm": True,
            "draft_text": content,
            "actions": [
                {"label": "生成视频", "kind": "submit_message", "payload": {"content": content}},
                {"label": "继续补充", "kind": "edit_message", "payload": {"content": content}},
            ],
        }

    if any(word in content for word in ("图片", "海报", "配图", "详情图", "封面")):
        return {
            "intent": "image_generate",
            "confidence": 0.9,
            "need_confirm": False,
            "draft_text": content,
            "actions": [
                {"label": "生成图片", "kind": "submit_message", "payload": {"content": content}},
                {"label": "继续补充", "kind": "edit_message", "payload": {"content": content}},
            ],
        }

    return {
        "intent": "chat_freeform",
        "confidence": 0.75,
        "need_confirm": False,
        "draft_text": content,
        "actions": [
            {"label": "发送到电脑端", "kind": "submit_message", "payload": {"content": content}},
            {"label": "继续编辑", "kind": "edit_message", "payload": {"content": content}},
        ],
    }
