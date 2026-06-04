from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import Asset, CreativeGenerationJob, User
from .assets import ASSETS_DIR, _upload_to_tos
from .auth import brand_mark_for_jwt_claim, get_current_user
from ..core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_ROOT_DIR = Path(__file__).resolve().parents[3]
_JOBS_DIR = _ROOT_DIR / "data" / "cutcli_templates"
_JOBS_DIR.mkdir(parents=True, exist_ok=True)
_PREVIEW_CATALOG_FILE = _JOBS_DIR / "template_previews.json"
_STATIC_PREVIEW_DIR = _ROOT_DIR / "client_static" / "client_code" / "cutcli_templates"
_STATIC_PREVIEW_PUBLIC_PREFIX = "/client/client-code/cutcli_templates"
_AUTO_CAPTION_TEMPLATE_ID = "auto_caption_pop_huazi_v1"
_AUTO_CAPTION_CLEAN_TEMPLATE_ID = "auto_caption_clean_fade_v1"
_AUTO_CAPTION_NEON_TEMPLATE_ID = "auto_caption_neon_focus_v1"
_AUTO_CAPTION_PUNCH_TEMPLATE_ID = "auto_caption_punch_big_v1"
_AUTO_CAPTION_HEALTH_BANNER_TEMPLATE_ID = "auto_caption_health_banner_v1"
_AUTO_CAPTION_QUOTE_FOCUS_TEMPLATE_ID = "auto_caption_quote_focus_v1"
_AUTO_CAPTION_MARKET_LABEL_TEMPLATE_ID = "auto_caption_market_label_v1"
_CUTCLI_TEMPLATE_JOB_FEATURE = "cutcli_template"
_STT_MODEL = "volcengine/speech-to-text/bigmodel-v2"
_STT_API_BASE = (
    getattr(settings, "sutui_api_base", None)
    or os.environ.get("SUTUI_API_BASE")
    or "https://api.xskill.ai"
).rstrip("/")
_CAPTION_HUAZI_EFFECT_ID = "7336838590023912710"
# Keep CapCut effect names ASCII-escaped so this file stays stable on mixed-codepage Windows.
_CAPTION_HUAZI_NAME = "4B\u9ec4\u5b57\u84dd\u8fb9\u6295\u5f71"
_CAPTION_IN_ANIMATION = "\u54cd\u4eae\u5f3a\u8c03"
_CAPTION_LOOP_ANIMATION = "\u9010\u5b57\u653e\u5927"
_CAPTION_TYPEWRITER_ANIMATION = "\u6545\u969c\u6253\u5b57"
_STT_TERMINAL_SUCCESS = {"completed", "complete", "success", "succeeded", "finished", "done"}
_STT_TERMINAL_FAILURE = {"failed", "error", "cancelled", "canceled", "timeout", "rejected"}
_STT_RUNNING = {"pending", "queued", "running", "processing", "waiting", "created"}
_SUTUI_TOKEN_POOL_LOCK = threading.Lock()
_SUTUI_TOKEN_POOL_INDEX: Dict[str, int] = {}


def _public_url(path: str) -> str:
    value = str(path or "").strip()
    if not value or re.match(r"^https?://", value, flags=re.IGNORECASE):
        return value
    base = (getattr(settings, "public_base_url", None) or "").strip().rstrip("/")
    if not base:
        return value
    return f"{base}/{value.lstrip('/')}"


_DEFAULT_OVERLAY_FIELDS: List[Dict[str, Any]] = [
    {
        "key": "headline",
        "label": "\u4e3b\u6807\u9898",
        "placeholder": "\u6bcf\u5929 3 \u5206\u949f\n\u6c14\u8840\u65fa\u5230\u5192\u7ea2\u5149",
        "default": "",
        "multiline": True,
        "max_length": 32,
    },
    {
        "key": "subheadline",
        "label": "\u526f\u6807\u9898",
        "placeholder": "Emotional stability",
        "default": "",
        "multiline": False,
        "max_length": 36,
    },
    {
        "key": "badge",
        "label": "\u6807\u7b7e",
        "placeholder": "\u517b\u751f\u65b0\u5e02\u573a",
        "default": "",
        "multiline": False,
        "max_length": 16,
    },
]


def _overlay_fields(*, headline: str = "", subheadline: str = "", badge: str = "") -> List[Dict[str, Any]]:
    defaults = {"headline": headline, "subheadline": subheadline, "badge": badge}
    fields: List[Dict[str, Any]] = []
    for field in _DEFAULT_OVERLAY_FIELDS:
        row = dict(field)
        row["default"] = defaults.get(str(row.get("key") or ""), "")
        fields.append(row)
    return fields


_TEMPLATES: Dict[str, Dict[str, Any]] = {
    _AUTO_CAPTION_TEMPLATE_ID: {
        "id": _AUTO_CAPTION_TEMPLATE_ID,
        "kind": "auto_caption",
        "name": "\u7206\u70b9\u9ec4\u5b57\u5f39\u8df3",
        "description": "\u77ed\u53e5\u7206\u70b9\u578b\u5b57\u5e55\uff0c\u9ec4\u5b57\u84dd\u8fb9\u3001\u91cd\u5165\u573a\u3001\u5f3a\u5f39\u8df3\uff0c\u9002\u5408\u5f00\u573a\u94a9\u5b50\u3001\u5356\u70b9\u5f3a\u8c03\u548c\u53e3\u64ad\u91cd\u70b9\u53e5\u3002",
        "aspect_ratio": "source",
        "default_duration": 0,
        "tags": ["\u7206\u70b9", "\u9ec4\u5b57\u84dd\u8fb9", "\u5f39\u8df3"],
        "input_modes": ["upload", "asset_id"],
        "preserve_source_video": True,
        "quality_label": "\u4e2d\u4e0b\u5927\u5b57 + \u7206\u70b9\u5f39\u8df3",
        "sample_video_url": "/client/client-code/cutcli_templates/auto_caption_pop_huazi_v1.mp4",
        "overlay_fields": _overlay_fields(),
        "caption_style": {
            "id": "yellow_burst",
            "text_effect": _CAPTION_HUAZI_NAME,
            "text_effect_id": _CAPTION_HUAZI_EFFECT_ID,
            "in_animation": _CAPTION_IN_ANIMATION,
            "in_animation_duration": 220_000,
            "loop_animation": _CAPTION_LOOP_ANIMATION,
            "loop_animation_duration": 430_000,
            "font_size": 14,
            "font_size_pattern": "burst",
            "caption_max_chars": 11,
            "text_color": "#FFFFFF",
            "border_color": "#041B51",
            "border_width": "0.09",
            "has_shadow": True,
            "shadow_color": "#000000",
            "transform_x": "0",
            "transform_y": "-0.55",
            "ass_layout": "center_burst",
            "ass_font_size": 96,
            "ass_primary": "&H0000F7FF",
            "ass_outline": "&H00FF3600",
            "ass_shadow": 5,
            "ass_border": 8,
            "ass_alignment": 2,
            "ass_margin_v": 250,
        },
    },
    _AUTO_CAPTION_CLEAN_TEMPLATE_ID: {
        "id": _AUTO_CAPTION_CLEAN_TEMPLATE_ID,
        "kind": "auto_caption",
        "name": "\u8bbf\u8c08\u6e05\u900f\u5b57\u5e55",
        "description": "\u7ec6\u63cf\u8fb9\u767d\u5b57\uff0c\u7a33\u5b9a\u653e\u5728\u753b\u9762\u4e0b\u4e09\u5206\u4e4b\u4e00\uff0c\u6e10\u663e\u5165\u573a\uff0c\u4e0d\u62a2\u4eba\u7269\u8868\u60c5\uff0c\u9002\u5408\u8bfe\u7a0b\u3001\u91c7\u8bbf\u548c\u5458\u5de5\u53e3\u64ad\u3002",
        "aspect_ratio": "source",
        "default_duration": 0,
        "tags": ["\u8bbf\u8c08", "\u8bfe\u7a0b", "\u6e05\u900f"],
        "input_modes": ["upload", "asset_id"],
        "preserve_source_video": True,
        "quality_label": "\u4e0b\u4e09\u5206\u4e4b\u4e00 + \u8f7b\u6e10\u663e",
        "sample_video_url": "/client/client-code/cutcli_templates/auto_caption_clean_fade_v1.mp4",
        "overlay_fields": _overlay_fields(),
        "caption_style": {
            "id": "clean_fade",
            "text_effect": "",
            "text_effect_id": "",
            "in_animation": "\u6e10\u663e",
            "in_animation_duration": 360_000,
            "loop_animation": "",
            "loop_animation_duration": 0,
            "font_size": 11,
            "font_size_pattern": "steady",
            "caption_max_chars": 14,
            "text_color": "#FFFFFF",
            "border_color": "#111827",
            "border_width": "0.065",
            "has_shadow": True,
            "shadow_color": "#000000",
            "transform_x": "0",
            "transform_y": "-0.72",
            "ass_layout": "lower_clean",
            "ass_font_size": 72,
            "ass_primary": "&H00FFFFFF",
            "ass_outline": "&H00231B12",
            "ass_shadow": 2,
            "ass_border": 5,
            "ass_alignment": 2,
            "ass_margin_v": 310,
        },
    },
    _AUTO_CAPTION_NEON_TEMPLATE_ID: {
        "id": _AUTO_CAPTION_NEON_TEMPLATE_ID,
        "kind": "auto_caption",
        "name": "\u79d1\u6280\u4fa7\u6807\u5b57\u5e55",
        "description": "\u5b57\u5e55\u4ece\u753b\u9762\u5de6\u4fa7\u50cf UI \u6807\u6ce8\u4e00\u6837\u6d6e\u51fa\uff0c\u9752\u84dd\u9713\u8679\u914d\u6df1\u8272\u63cf\u8fb9\uff0c\u9002\u5408 AI\u3001SaaS\u3001\u4ea7\u54c1\u6f14\u793a\u548c\u79d1\u6280\u611f\u5185\u5bb9\u3002",
        "aspect_ratio": "source",
        "default_duration": 0,
        "tags": ["\u79d1\u6280", "\u4fa7\u6807", "\u9713\u8679"],
        "input_modes": ["upload", "asset_id"],
        "preserve_source_video": True,
        "quality_label": "\u5de6\u4fa7\u7ec8\u7aef\u5feb\u6253 + \u9752\u84dd\u9713\u8679",
        "sample_video_url": "/client/client-code/cutcli_templates/auto_caption_neon_focus_v1.mp4",
        "overlay_fields": _overlay_fields(),
        "caption_style": {
            "id": "side_neon",
            "text_effect": "",
            "text_effect_id": "",
            "in_animation": _CAPTION_TYPEWRITER_ANIMATION,
            "in_animation_duration": 420_000,
            "loop_animation": "",
            "loop_animation_duration": 0,
            "font_size": 11,
            "font_size_pattern": "side_neon",
            "caption_max_chars": 11,
            "caption_motion": "typewriter",
            "typing_interval_ms": 85,
            "typing_min_hold_ms": 420,
            "typing_cursor": "|",
            "text_color": "#7CFBFF",
            "border_color": "#062A4D",
            "border_width": "0.085",
            "has_shadow": True,
            "shadow_color": "#00111F",
            "transform_x": "-0.56",
            "transform_y": "0.38",
            "ass_layout": "side_neon",
            "ass_font_size": 74,
            "ass_primary": "&H00FFFB7C",
            "ass_outline": "&H004D2A06",
            "ass_shadow": 5,
            "ass_border": 6,
            "ass_alignment": 7,
            "ass_margin_v": 250,
        },
    },
    _AUTO_CAPTION_PUNCH_TEMPLATE_ID: {
        "id": _AUTO_CAPTION_PUNCH_TEMPLATE_ID,
        "kind": "auto_caption",
        "name": "\u77ed\u5267\u91cd\u51fb\u5927\u5b57",
        "description": "\u4e00\u53e5\u4e00\u7838\u7684\u77ed\u5267\u5b57\u5e55\uff0c\u5b57\u53f7\u66f4\u5927\u3001\u4f4d\u7f6e\u66f4\u9ad8\u3001\u5165\u573a\u66f4\u731b\uff0c\u9002\u5408\u60c5\u7eea\u53cd\u8f6c\u3001\u51b2\u7a81\u53e5\u548c\u5f3a\u94a9\u5b50\u3002",
        "aspect_ratio": "source",
        "default_duration": 0,
        "tags": ["\u77ed\u5267", "\u91cd\u51fb", "\u94a9\u5b50"],
        "input_modes": ["upload", "asset_id"],
        "preserve_source_video": True,
        "quality_label": "\u5c45\u4e2d\u91cd\u51fb + \u60c5\u7eea\u53cd\u8f6c",
        "sample_video_url": "/client/client-code/cutcli_templates/auto_caption_punch_big_v1.mp4",
        "overlay_fields": _overlay_fields(),
        "caption_style": {
            "id": "punch_big",
            "text_effect": _CAPTION_HUAZI_NAME,
            "text_effect_id": _CAPTION_HUAZI_EFFECT_ID,
            "in_animation": _CAPTION_IN_ANIMATION,
            "in_animation_duration": 160_000,
            "loop_animation": _CAPTION_LOOP_ANIMATION,
            "loop_animation_duration": 360_000,
            "font_size": 16,
            "font_size_pattern": "punch",
            "caption_max_chars": 7,
            "text_color": "#FFFFFF",
            "border_color": "#07123F",
            "border_width": "0.10",
            "has_shadow": True,
            "shadow_color": "#000000",
            "transform_x": "0",
            "transform_y": "-0.34",
            "ass_layout": "dramatic_hook",
            "ass_font_size": 118,
            "ass_primary": "&H0000F7FF",
            "ass_outline": "&H003F1207",
            "ass_shadow": 7,
            "ass_border": 9,
            "ass_alignment": 5,
            "ass_margin_v": 220,
        },
    },
    _AUTO_CAPTION_HEALTH_BANNER_TEMPLATE_ID: {
        "id": _AUTO_CAPTION_HEALTH_BANNER_TEMPLATE_ID,
        "kind": "auto_caption",
        "name": "\u517b\u751f\u9876\u680f\u91d1\u53e5",
        "description": "\u53c2\u7167\u4e13\u5bb6\u53e3\u64ad\u7c7b\u7248\u5f0f\uff1a\u9876\u90e8\u534a\u900f\u660e\u6a2a\u680f\u627f\u8f7d\u56fa\u5b9a\u5927\u6807\u9898\uff0c\u4e2d\u4e0b\u90e8\u4fdd\u7559\u8ddf\u8bf4\u5b57\u5e55\uff0c\u9002\u5408\u517b\u751f\u3001\u8d22\u7a0e\u3001\u5b9e\u7528\u6280\u5de7\u548c\u77e5\u8bc6\u53e3\u64ad\u3002",
        "aspect_ratio": "source",
        "default_duration": 0,
        "tags": ["\u9876\u680f", "\u91d1\u53e5", "\u77e5\u8bc6\u53e3\u64ad"],
        "input_modes": ["upload", "asset_id"],
        "preserve_source_video": True,
        "quality_label": "\u9876\u90e8\u5927\u6807\u9898 + \u4e0b\u65b9\u8ddf\u8bf4",
        "sample_video_url": "/client/client-code/cutcli_templates/auto_caption_health_banner_v1.mp4",
        "overlay_fields": _overlay_fields(headline="\u6bcf\u5929 3 \u5206\u949f\n\u6c14\u8840\u65fa\u5230\u5192\u7ea2\u5149"),
        "caption_style": {
            "id": "health_banner",
            "font_size": 12,
            "font_size_pattern": "steady",
            "caption_max_chars": 13,
            "text_color": "#FFFFFF",
            "border_color": "#2A1606",
            "border_width": "0.07",
            "has_shadow": True,
            "shadow_color": "#000000",
            "transform_x": "0",
            "transform_y": "-0.68",
            "ass_layout": "health_banner",
            "ass_font_size": 78,
            "ass_primary": "&H00FFFFFF",
            "ass_outline": "&H00151208",
            "ass_shadow": 4,
            "ass_border": 5,
            "ass_alignment": 2,
            "ass_margin_v": 300,
            "overlay_style": {
                "layout": "top_banner",
                "headline_font_size": 80,
                "banner_height_ratio": 0.30,
                "headline_y_ratio": 0.56,
                "headline_color": "&H001F4A86",
                "headline_outline": "&H00FFFFFF",
                "banner_color": "&HA8F3E7CF",
            },
        },
    },
    _AUTO_CAPTION_QUOTE_FOCUS_TEMPLATE_ID: {
        "id": _AUTO_CAPTION_QUOTE_FOCUS_TEMPLATE_ID,
        "kind": "auto_caption",
        "name": "\u60c5\u7eea\u5f15\u53f7\u7126\u70b9",
        "description": "\u753b\u9762\u4e2d\u90e8\u7528\u5f15\u53f7\u5927\u5b57\u6253\u51fa\u56fa\u5b9a\u4e3b\u9898\uff0c\u4e0b\u65b9\u52a0\u82f1\u6587/\u526f\u6807\u9898\u5c42\u6b21\uff0c\u8ddf\u8bf4\u5b57\u5e55\u653e\u5728\u66f4\u4f4e\u5904\uff0c\u9002\u5408\u60c5\u7eea\u3001\u6559\u80b2\u3001\u6210\u957f\u7c7b\u77ed\u89c6\u9891\u3002",
        "aspect_ratio": "source",
        "default_duration": 0,
        "tags": ["\u5f15\u53f7", "\u60c5\u7eea", "\u53cc\u8bed"],
        "input_modes": ["upload", "asset_id"],
        "preserve_source_video": True,
        "quality_label": "\u4e2d\u90e8\u4e3b\u9898\u5f15\u53f7 + \u53cc\u8bed\u526f\u6807",
        "sample_video_url": "/client/client-code/cutcli_templates/auto_caption_quote_focus_v1.mp4",
        "overlay_fields": _overlay_fields(headline="\u300c\u60c5\u7eea\u7a33\u5b9a\u300d", subheadline="Emotional stability"),
        "caption_style": {
            "id": "quote_focus",
            "font_size": 11,
            "font_size_pattern": "steady",
            "caption_max_chars": 13,
            "text_color": "#FFFFFF",
            "border_color": "#1F2937",
            "border_width": "0.07",
            "has_shadow": True,
            "shadow_color": "#000000",
            "transform_x": "0",
            "transform_y": "-0.76",
            "ass_layout": "quote_focus",
            "ass_font_size": 68,
            "ass_primary": "&H00FFFFFF",
            "ass_outline": "&H001F2937",
            "ass_shadow": 3,
            "ass_border": 5,
            "ass_alignment": 2,
            "ass_margin_v": 340,
            "overlay_style": {
                "layout": "center_quote",
                "headline_font_size": 88,
                "headline_y_ratio": 0.46,
                "headline_color": "&H00FFFFFF",
                "headline_outline": "&H00222931",
                "subheadline_font_size": 42,
            },
        },
    },
    _AUTO_CAPTION_MARKET_LABEL_TEMPLATE_ID: {
        "id": _AUTO_CAPTION_MARKET_LABEL_TEMPLATE_ID,
        "kind": "auto_caption",
        "name": "\u54c1\u724c\u6807\u7b7e\u5927\u5b57",
        "description": "\u4e2d\u4e0b\u5927\u6807\u9898\u914d\u6a59\u8272\u80f6\u56ca\u6807\u7b7e\uff0c\u753b\u9762\u5e95\u90e8\u4fdd\u7559\u8ddf\u8bf4\u5b57\u5e55\uff0c\u9002\u5408\u52a0\u76df\u3001\u4ea7\u54c1\u5356\u70b9\u3001\u54c1\u724c\u62db\u5546\u548c\u65b0\u5e02\u573a\u5185\u5bb9\u3002",
        "aspect_ratio": "source",
        "default_duration": 0,
        "tags": ["\u6807\u7b7e", "\u62db\u5546", "\u5927\u5b57"],
        "input_modes": ["upload", "asset_id"],
        "preserve_source_video": True,
        "quality_label": "\u6a59\u8272\u80f6\u56ca\u6807\u7b7e + \u4e2d\u4e0b\u5927\u6807\u9898",
        "sample_video_url": "/client/client-code/cutcli_templates/auto_caption_market_label_v1.mp4",
        "overlay_fields": _overlay_fields(headline="\u65b0\u5174\u517b\u751f\u52a0\u76df", badge="\u517b\u751f\u65b0\u5e02\u573a"),
        "caption_style": {
            "id": "market_label",
            "font_size": 12,
            "font_size_pattern": "steady",
            "caption_max_chars": 13,
            "text_color": "#FFFFFF",
            "border_color": "#111827",
            "border_width": "0.07",
            "has_shadow": True,
            "shadow_color": "#000000",
            "transform_x": "0",
            "transform_y": "-0.78",
            "ass_layout": "market_label",
            "ass_font_size": 68,
            "ass_primary": "&H00FFFFFF",
            "ass_outline": "&H00111111",
            "ass_shadow": 4,
            "ass_border": 5,
            "ass_alignment": 2,
            "ass_margin_v": 340,
            "overlay_style": {
                "layout": "market_label",
                "headline_font_size": 84,
                "headline_y_ratio": 0.57,
                "headline_color": "&H00FFFFFF",
                "headline_outline": "&H00111111",
                "badge_color": "&H001B7BE6",
            },
        },
    },
}


class TemplateListItem(BaseModel):
    id: str
    kind: str = "auto_caption"
    name: str
    description: str
    aspect_ratio: str = "9:16"
    default_duration: int = 8
    tags: List[str] = []
    input_modes: List[str] = []
    preserve_source_video: bool = True
    quality_label: str = ""
    render_path: str = ""
    preview_url: str = ""
    sample_video_url: str = ""
    sample_asset_id: str = ""
    render_modes: List[str] = Field(default_factory=list)
    generation_strategy: Dict[str, Any] = Field(default_factory=dict)
    caption_style: Dict[str, Any] = Field(default_factory=dict)
    overlay_fields: List[Dict[str, Any]] = Field(default_factory=list)


class CutcliSttTranscribeBody(BaseModel):
    audio_url: str


class CutcliCloudRenderDraftBody(BaseModel):
    draft_id: str
    draft_zip_url: str
    timeout_seconds: int = 1800
    mirror_to_tos: bool = True


def _caption_style_for_template(template: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = dict((_TEMPLATES.get(_AUTO_CAPTION_TEMPLATE_ID) or {}).get("caption_style") or {})
    if isinstance(template, dict):
        base.update(template.get("caption_style") or {})
        overlay_fields = template.get("overlay_fields") if isinstance(template.get("overlay_fields"), list) else []
        if overlay_fields:
            base["overlay_fields"] = overlay_fields
    base.setdefault("text_effect", _CAPTION_HUAZI_NAME)
    base.setdefault("text_effect_id", _CAPTION_HUAZI_EFFECT_ID)
    base.setdefault("in_animation", _CAPTION_IN_ANIMATION)
    base.setdefault("in_animation_duration", 280_000)
    base.setdefault("loop_animation", _CAPTION_LOOP_ANIMATION)
    base.setdefault("loop_animation_duration", 500_000)
    base.setdefault("font_size", 13)
    base.setdefault("text_color", "#FFFFFF")
    base.setdefault("border_color", "#061A48")
    base.setdefault("border_width", "0.08")
    base.setdefault("has_shadow", True)
    base.setdefault("shadow_color", "#000000")
    base.setdefault("transform_x", "0")
    base.setdefault("transform_y", "-0.66")
    base.setdefault("caption_max_chars", 11)
    base.setdefault("font_size_pattern", "steady")
    base.setdefault("ass_layout", "center_burst")
    base.setdefault("ass_font_size", 86)
    base.setdefault("ass_primary", "&H0000F7FF")
    base.setdefault("ass_outline", "&H00FF3C00")
    base.setdefault("ass_shadow", 4)
    base.setdefault("ass_border", 7)
    base.setdefault("ass_alignment", 2)
    base.setdefault("ass_margin_v", 265)
    base.setdefault("ass_effect", "")
    return base


def _caption_style_public(style: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": style.get("id") or "",
        "text_effect": style.get("text_effect") or "",
        "text_effect_id": style.get("text_effect_id") or "",
        "in_animation": style.get("in_animation") or "",
        "loop_animation": style.get("loop_animation") or "",
        "font_size": style.get("font_size") or 0,
        "font_size_pattern": style.get("font_size_pattern") or "",
        "text_color": style.get("text_color") or "",
        "border_color": style.get("border_color") or "",
        "transform_x": style.get("transform_x") or "",
        "transform_y": style.get("transform_y") or "",
        "ass_layout": style.get("ass_layout") or "",
    }


def _find_cutcli_bin() -> str:
    candidates = [
        os.environ.get("CUTCLI_BIN"),
        shutil.which("cutcli"),
        shutil.which("cutcli.exe"),
        str(Path.home() / "bin" / "cutcli"),
        str(Path.home() / "bin" / "cutcli.exe"),
    ]
    for value in candidates:
        if value and Path(value).exists():
            return str(Path(value))
    raise HTTPException(
        status_code=500,
        detail="server cutcli binary not found; install CutCLI or set CUTCLI_BIN",
    )


def _load_preview_catalog() -> Dict[str, Any]:
    if not _PREVIEW_CATALOG_FILE.exists():
        return {}
    try:
        data = json.loads(_PREVIEW_CATALOG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("[CutCLI妯℃澘] failed to read preview catalog: %s", exc)
        return {}


def _template_preview_url(template_id: str) -> str:
    catalog = _load_preview_catalog()
    item = catalog.get(template_id)
    if isinstance(item, dict):
        url = str(item.get("preview_url") or item.get("sample_video_url") or "").strip()
        if url:
            return _public_url(url)
    elif isinstance(item, str) and item.strip():
        return _public_url(item.strip())

    for ext in (".mp4", ".mov", ".webm"):
        sample = _STATIC_PREVIEW_DIR / f"{template_id}{ext}"
        if sample.exists():
            return _public_url(f"{_STATIC_PREVIEW_PUBLIC_PREFIX}/{sample.name}")
    tpl = _TEMPLATES.get(template_id) or {}
    return _public_url(str(tpl.get("preview_url") or tpl.get("sample_video_url") or "").strip())


def _template_sample_asset_id(template_id: str) -> str:
    catalog = _load_preview_catalog()
    item = catalog.get(template_id)
    if isinstance(item, dict):
        return str(item.get("sample_asset_id") or "").strip()
    return ""


def _find_ffprobe_bin() -> str:
    ffprobe = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    bundled = (
        _ROOT_DIR
        / "skills"
        / "comfly_veo3_daihuo_video"
        / "tools"
        / "ffmpeg"
        / "windows"
    )
    if not ffprobe:
        b = bundled / "ffprobe.exe"
        if b.exists():
            ffprobe = str(b)
    if not ffprobe or not Path(ffprobe).exists():
        raise HTTPException(status_code=500, detail="server ffprobe binary not found")
    return str(ffprobe)


def _find_ffmpeg_bin() -> str:
    ffmpeg = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    bundled = (
        _ROOT_DIR
        / "skills"
        / "comfly_veo3_daihuo_video"
        / "tools"
        / "ffmpeg"
        / "windows"
    )
    if not ffmpeg:
        b = bundled / "ffmpeg.exe"
        if b.exists():
            ffmpeg = str(b)
    if not ffmpeg or not Path(ffmpeg).exists():
        raise HTTPException(status_code=500, detail="service ffmpeg is missing")
    return str(ffmpeg)


def _run_cmd(
    args: List[str],
    *,
    timeout: int = 180,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
) -> str:
    logger.info("[CutCLI妯℃澘] run: %s", " ".join(str(x) for x in args[:4]))
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(msg[:1000] or f"command failed: {proc.returncode}")
    return proc.stdout or ""


def _json_from_cmd(
    args: List[str],
    *,
    timeout: int = 180,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    out = _run_cmd(args, timeout=timeout, cwd=cwd, env=env)
    try:
        value = json.loads(out)
    except Exception as exc:
        raise RuntimeError(f"鍛戒护杩斿洖涓嶆槸 JSON: {out[:500]}") from exc
    return value if isinstance(value, dict) else {}


def _asset_local_path(row: Asset) -> Optional[Path]:
    if not row or not row.filename:
        return None
    p = ASSETS_DIR / row.filename
    return p if p.exists() else None


def _safe_ext(name: str, default: str = ".mp4") -> str:
    ext = Path(name or "").suffix.lower()
    if ext in {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}:
        return ext
    return default


def _clean_public_http_url(value: str) -> str:
    url = str(value or "").strip()
    if re.match(r"^https?://", url, flags=re.IGNORECASE):
        return url
    return ""


async def _resolve_source_video(
    *,
    file: Optional[UploadFile],
    asset_id: str,
    video_url: str,
    user_id: int,
    db: Session,
    job_dir: Path,
) -> Tuple[str, Optional[str], str]:
    if file is not None and file.filename:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="uploaded video file is empty")
        ext = _safe_ext(file.filename)
        src_path = job_dir / f"source{ext}"
        src_path.write_bytes(data)
        return str(src_path), None, file.filename

    aid = (asset_id or "").strip()
    if aid:
        row = db.query(Asset).filter(Asset.asset_id == aid, Asset.user_id == user_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="asset not found")
        if (row.media_type or "").lower() != "video":
            raise HTTPException(status_code=400, detail="asset is not a video")
        local = _asset_local_path(row)
        if local:
            return str(local), row.asset_id, row.filename
        url = (row.source_url or "").strip()
        if url.startswith(("http://", "https://")):
            return url, row.asset_id, row.filename
        raise HTTPException(status_code=400, detail="asset has no usable local video file or public URL")

    url = (video_url or "").strip()
    if url.startswith(("http://", "https://")):
        return url, None, Path(url.split("?")[0]).name or "remote-video.mp4"

    raise HTTPException(status_code=400, detail="video file, video asset_id, or video_url is required")


def _probe_video(ffprobe: str, source: str) -> Dict[str, Any]:
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration:format=duration",
        "-of",
        "json",
        source,
    ]
    raw = _run_cmd(cmd, timeout=60)
    try:
        data = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="cannot read video info; make sure the video is playable") from exc
    stream = (data.get("streams") or [{}])[0] or {}
    fmt = data.get("format") or {}
    width = int(stream.get("width") or 1080)
    height = int(stream.get("height") or 1920)
    duration = stream.get("duration") or fmt.get("duration") or 0
    try:
        duration_f = float(duration)
    except Exception:
        duration_f = 0.0
    return {"width": width, "height": height, "duration": max(duration_f, 0.1)}


def _font_path() -> Optional[str]:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def _make_overlay_png(path: Path, *, title: str, subtitle: str, duration: int) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:
        raise HTTPException(status_code=500, detail="server Pillow is missing; cannot generate overlay") from exc

    w, h = 1080, 1920
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for y in range(0, 520):
        alpha = int(165 * (1 - y / 520))
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, max(alpha, 0)))
    for y in range(1280, h):
        alpha = int(185 * ((y - 1280) / (h - 1280)))
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, min(alpha, 185)))

    gold = (224, 176, 92, 235)
    white = (255, 255, 255, 245)
    muted = (232, 236, 242, 210)
    panel = (8, 13, 26, 132)

    draw.rounded_rectangle((72, 112, 430, 174), radius=26, outline=gold, width=2, fill=(8, 13, 26, 80))
    draw.text((102, 129), "PREMIUM BRAND FILM", fill=gold, font=_pil_font(28))
    draw.line((72, 1450, 1008, 1450), fill=(255, 255, 255, 68), width=2)
    draw.line((72, 1480, 245, 1480), fill=gold, width=6)
    draw.rounded_rectangle((72, 1515, 1008, 1778), radius=34, fill=panel, outline=(255, 255, 255, 42), width=1)

    title = (title or "鍝佺墝楂樺厜鏃跺埢").strip()[:28]
    subtitle = (subtitle or "鐢ㄤ竴鏉¤棰戠敓鎴愰珮绾у浼犵墖").strip()[:46]
    draw.text((118, 1565), title, fill=white, font=_pil_font(60, bold=True))
    draw.text((120, 1650), subtitle, fill=muted, font=_pil_font(34))
    draw.text((120, 1722), f"{duration}s TEMPLATE VIDEO", fill=gold, font=_pil_font(26))
    draw.rounded_rectangle((734, 1704, 958, 1756), radius=24, fill=(224, 176, 92, 230))
    draw.text((776, 1718), "VIEW NOW", fill=(16, 24, 39, 255), font=_pil_font(26, bold=True))
    img.save(path)


def _pil_font(size: int, bold: bool = False):
    from PIL import ImageFont

    fp = _font_path()
    if fp:
        try:
            return ImageFont.truetype(fp, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _write_json(path: Path, data: Any) -> str:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return "@" + str(path)


def _json_value_from_cmd(
    args: List[str],
    *,
    timeout: int = 180,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
) -> Any:
    out = _run_cmd(args, timeout=timeout, cwd=cwd, env=env)
    try:
        return json.loads(out)
    except Exception as exc:
        raise RuntimeError(f"command returned non-json: {out[:500]}") from exc


class AutoCaptionJobError(RuntimeError):
    def __init__(self, code: str, message: str, *, detail: Optional[Any] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def _utcnow() -> datetime:
    return datetime.utcnow()


def _ts(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    try:
        return int(value or 0)
    except Exception:
        return 0


def _job_row_to_public(row: CreativeGenerationJob) -> Dict[str, Any]:
    request_payload = row.request_payload if isinstance(row.request_payload, dict) else {}
    result_payload = row.result_payload if isinstance(row.result_payload, dict) else {}
    meta = row.meta if isinstance(row.meta, dict) else {}
    template = request_payload.get("template") if isinstance(request_payload.get("template"), dict) else {}
    quality = result_payload.get("quality") if isinstance(result_payload.get("quality"), dict) else {}
    source_asset_id = (
        result_payload.get("source_asset_id")
        or request_payload.get("source_asset_id")
        or ""
    )
    source_name = result_payload.get("source_name") or request_payload.get("source_name") or ""
    audio_url = (
        result_payload.get("audio_url")
        or request_payload.get("audio_url")
        or ""
    )
    preview_asset_id = (
        result_payload.get("preview_asset_id")
        or result_payload.get("final_asset_id")
        or ((row.asset_ids or [None])[0] if isinstance(row.asset_ids, list) and row.asset_ids else "")
        or ""
    )
    preview_url = result_payload.get("preview_url") or result_payload.get("open_url") or ""
    open_url = result_payload.get("open_url") or result_payload.get("preview_url") or ""
    return {
        "ok": True,
        "async": True,
        "job_id": row.job_id,
        "status": row.status or "",
        "stage": row.stage or "",
        "user_id": row.user_id,
        "template_id": request_payload.get("template_id") or template.get("id") or "",
        "template": template,
        "template_name": template.get("name") or row.title or "",
        "source_asset_id": source_asset_id,
        "source_name": source_name,
        "source_info": request_payload.get("source_info") or {},
        "audio_url": audio_url,
        "stt_model": meta.get("stt_model") or request_payload.get("stt_model") or _STT_MODEL,
        "quality_policy": request_payload.get("quality_policy") or {},
        "preview_asset_id": preview_asset_id,
        "final_asset_id": result_payload.get("final_asset_id") or preview_asset_id,
        "preview_url": preview_url,
        "open_url": open_url,
        "caption_count": result_payload.get("caption_count") or quality.get("caption_count") or 0,
        "render_strategy": result_payload.get("render_strategy") or "",
        "error": row.error or "",
        "error_code": result_payload.get("error_code") or "",
        "quality": quality,
        "warnings": result_payload.get("warnings") or [],
        "draft_id": result_payload.get("draft_id") or "",
        "cloud_job_id": result_payload.get("cloud_job_id") or "",
        "token_source": meta.get("token_source") or "",
        "token_masked": meta.get("token_masked") or "",
        "local_workspace_cleanup": meta.get("local_workspace_cleanup") or {},
        "local_workspace_cleaned_at": _ts(meta.get("local_workspace_cleaned_at")),
        "created_at": _ts(row.created_at),
        "updated_at": _ts(row.updated_at),
        "completed_at": _ts(row.completed_at),
        "poll_path": f"/api/cutcli/templates/jobs/{row.job_id}",
    }


def _get_cutcli_job_row(db: Session, job_id: str) -> Optional[CreativeGenerationJob]:
    return (
        db.query(CreativeGenerationJob)
        .filter(
            CreativeGenerationJob.job_id == job_id,
            CreativeGenerationJob.feature_type == _CUTCLI_TEMPLATE_JOB_FEATURE,
            CreativeGenerationJob.deleted_at.is_(None),
        )
        .first()
    )


def _create_cutcli_job(
    db: Session,
    *,
    job_id: str,
    user_id: int,
    template: Dict[str, Any],
    source_asset_id: Optional[str],
    source_name: str,
    source_info: Dict[str, Any],
    quality_policy: Dict[str, Any],
    audio_url: str = "",
) -> CreativeGenerationJob:
    now = _utcnow()
    row = CreativeGenerationJob(
        job_id=job_id,
        user_id=user_id,
        feature_type=_CUTCLI_TEMPLATE_JOB_FEATURE,
        provider="cutcli",
        status="running",
        stage="queued",
        title=str(template.get("name") or ""),
        request_payload={
            "template_id": template.get("id") or "",
            "template": template,
            "source_asset_id": source_asset_id,
            "source_name": source_name,
            "source_info": source_info,
            "audio_url": audio_url,
            "stt_model": _STT_MODEL,
            "quality_policy": quality_policy,
        },
        result_payload={"audio_url": audio_url} if audio_url else {},
        asset_ids=[],
        saved_assets=[],
        meta={
            "stt_model": _STT_MODEL,
            "audio_source": "client_audio_url" if audio_url else "",
        },
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _merge_dict(base: Any, updates: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base) if isinstance(base, dict) else {}
    out.update(updates)
    return out


def _update_cutcli_job(job_id: str, **fields: Any) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        row = _get_cutcli_job_row(db, job_id)
        if not row:
            return {}
        now = _utcnow()
        status = fields.pop("status", None)
        stage = fields.pop("stage", None)
        error = fields.pop("error", None)
        result_updates = fields.pop("result_updates", None)
        meta_updates = fields.pop("meta_updates", None)
        asset_ids = fields.pop("asset_ids", None)
        provider_task_id = fields.pop("provider_task_id", None)
        if status is not None:
            row.status = str(status)
        if stage is not None:
            row.stage = str(stage)
        if error is not None:
            row.error = str(error) if error else None
        if provider_task_id is not None:
            row.provider_task_id = str(provider_task_id) if provider_task_id else None
        if asset_ids is not None:
            row.asset_ids = asset_ids
            row.saved_assets = asset_ids
        if result_updates:
            row.result_payload = _merge_dict(row.result_payload, result_updates)
        if meta_updates:
            row.meta = _merge_dict(row.meta, meta_updates)
        row.updated_at = now
        if row.status in {"completed", "failed"} and row.completed_at is None:
            row.completed_at = now
        db.commit()
        db.refresh(row)
        return _job_row_to_public(row)
    finally:
        db.close()


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _path_size_bytes(path: Path) -> int:
    try:
        if path.is_file() or path.is_symlink():
            return int(path.stat().st_size)
        if path.is_dir():
            total = 0
            for item in path.rglob("*"):
                try:
                    if item.is_file() or item.is_symlink():
                        total += int(item.stat().st_size)
                except OSError:
                    continue
            return total
    except OSError:
        return 0
    return 0


def _remove_workspace_path(path: Path) -> Tuple[int, bool]:
    size = _path_size_bytes(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
        return size, True
    try:
        path.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    return size, False


def _cleanup_auto_caption_workspace(job_id: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "kept": [],
        "removed_files": 0,
        "removed_dirs": 0,
        "removed_bytes": 0,
        "warnings": [],
    }
    job_dir = _JOBS_DIR / job_id
    try:
        jobs_root = _JOBS_DIR.resolve()
        resolved_job_dir = job_dir.resolve()
    except OSError as exc:
        result["warnings"].append(f"cleanup_resolve_failed: {str(exc)[:180]}")
        return result
    if resolved_job_dir == jobs_root or not _path_is_relative_to(resolved_job_dir, jobs_root):
        result["warnings"].append("cleanup_skipped_unsafe_job_dir")
        return result
    if not resolved_job_dir.exists():
        return result
    for item in list(resolved_job_dir.iterdir()):
        try:
            resolved_item = item.resolve()
        except OSError:
            continue
        if not _path_is_relative_to(resolved_item, resolved_job_dir):
            result["warnings"].append(f"cleanup_skipped_unsafe_path: {item.name}")
            continue
        try:
            size, was_dir = _remove_workspace_path(resolved_item)
            result["removed_bytes"] += size
            if was_dir:
                result["removed_dirs"] += 1
            else:
                result["removed_files"] += 1
        except Exception as exc:
            result["warnings"].append(f"cleanup_failed_{item.name}: {str(exc)[:180]}")
    try:
        resolved_job_dir.rmdir()
        result["removed_dirs"] += 1
    except OSError:
        pass
    return result


def _cleanup_auto_caption_workspace_and_record(job_id: str) -> None:
    try:
        cleanup = _cleanup_auto_caption_workspace(job_id)
        _update_cutcli_job(
            job_id,
            meta_updates={
                "local_workspace_cleanup": cleanup,
                "local_workspace_cleaned_at": int(time.time()),
            },
        )
    except Exception as exc:
        logger.warning("[cutcli-auto-caption] workspace cleanup failed job_id=%s: %s", job_id, exc)
        _update_cutcli_job(
            job_id,
            meta_updates={
                "local_workspace_cleanup": {
                    "warnings": [f"cleanup_failed: {str(exc)[:180]}"],
                },
                "local_workspace_cleaned_at": int(time.time()),
            },
        )


def _safe_error_text(value: Any, limit: int = 1200) -> str:
    if isinstance(value, dict):
        for key in ("detail", "message", "msg", "error"):
            item = value.get(key)
            if item:
                return _safe_error_text(item, limit)
        try:
            return json.dumps(value, ensure_ascii=False)[:limit]
        except Exception:
            return str(value)[:limit]
    return str(value or "").strip()[:limit]


def _mask_token(value: str) -> str:
    token = (value or "").strip()
    if not token:
        return ""
    if len(token) <= 12:
        return token[:2] + "***"
    return token[:6] + "..." + token[-4:]


def _split_tokens(raw: Optional[str]) -> List[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _pick_sutui_token(pool_key: str, tokens: List[str]) -> Tuple[str, str]:
    if not tokens:
        return "", ""
    with _SUTUI_TOKEN_POOL_LOCK:
        idx = _SUTUI_TOKEN_POOL_INDEX.get(pool_key, 0) % len(tokens)
        _SUTUI_TOKEN_POOL_INDEX[pool_key] = idx + 1
    return tokens[idx], f"env.server_pool.{pool_key}"


def _server_sutui_token_from_env(brand_mark: Optional[str] = None) -> Tuple[str, str]:
    brand = (brand_mark or "").strip().lower()
    brand_order: List[str] = []
    if brand in ("bihuo", "yingshi"):
        brand_order.append(brand)
    for fallback in ("bihuo", "yingshi"):
        if fallback not in brand_order:
            brand_order.append(fallback)

    for pool_key in brand_order:
        suffix = pool_key.upper()
        tokens = _split_tokens(os.environ.get(f"SUTUI_SERVER_TOKENS_{suffix}"))
        if not tokens:
            single = (os.environ.get(f"SUTUI_SERVER_TOKEN_{suffix}") or "").strip()
            tokens = [single] if single else []
        token, source = _pick_sutui_token(pool_key, tokens)
        if token:
            return token, source

    legacy_tokens = _split_tokens(os.environ.get("SUTUI_SERVER_TOKENS"))
    if not legacy_tokens:
        single = (
            getattr(settings, "sutui_server_token", None)
            or os.environ.get("SUTUI_SERVER_TOKEN")
            or ""
        ).strip()
        legacy_tokens = [single] if single else []
    token, source = _pick_sutui_token("legacy", legacy_tokens)
    if token:
        return token, source
    return "", ""


def _load_sutui_token_for_stt(db: Optional[Session], user_id: int) -> Tuple[str, str]:
    brand_mark = ""
    if db is not None:
        try:
            user = db.query(User).filter(User.id == int(user_id)).first()
            brand_mark = brand_mark_for_jwt_claim(getattr(user, "brand_mark", None) if user else None) or ""
        except Exception as exc:
            logger.warning("[cutcli-auto-caption] failed to load user brand mark: %s", exc)

    token, source = _server_sutui_token_from_env(brand_mark)
    if token:
        if brand_mark:
            source = f"{source}:{brand_mark}"
        return token, source
    raise AutoCaptionJobError("stt_token_missing", "server sutui token is not configured")


def _extract_audio_wav(*, ffmpeg: str, source: str, out_path: Path) -> None:
    _run_cmd(
        [
            ffmpeg,
            "-y",
            "-i",
            source,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(out_path),
        ],
        timeout=600,
    )
    if not out_path.exists() or out_path.stat().st_size <= 128:
        raise AutoCaptionJobError("audio_extract_failed", "extracted audio is empty")


def _upload_job_file_to_tos(path: Path, *, object_key: str, content_type: str) -> str:
    data = path.read_bytes()
    url = _upload_to_tos(data, object_key, content_type)
    if not url:
        raise AutoCaptionJobError("tos_upload_failed", f"failed to upload {path.name} to TOS")
    return url


def _stt_create_task(token: str, audio_url: str, *, job_dir: Path) -> Dict[str, Any]:
    body = {
        "model": _STT_MODEL,
        "params": {
            "audio_url": audio_url,
            "format": "wav",
            "language": "zh-CN",
            "show_utterances": True,
            "enable_punc": True,
            "enable_itn": True,
            "enable_ddc": False,
            "enable_speaker_info": False,
            "vad_segment": False,
        },
        "channel": None,
    }
    (job_dir / "stt_create_request.json").write_text(
        json.dumps({**body, "auth": "Bearer ***"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(timeout=120.0, trust_env=False) as client:
            resp = client.post(f"{_STT_API_BASE}/api/v3/tasks/create", json=body, headers=headers)
    except Exception as exc:
        raise AutoCaptionJobError("stt_request_failed", str(exc)[:1000]) from exc
    try:
        payload = resp.json()
    except Exception:
        payload = {"raw": resp.text}
    (job_dir / "stt_create_response.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if resp.status_code >= 400:
        msg = _safe_error_text(payload or resp.text)
        code = "stt_balance_insufficient" if "浣欓涓嶈冻" in msg or "balance" in msg.lower() else "stt_create_failed"
        raise AutoCaptionJobError(code, msg, detail=payload)
    if isinstance(payload, dict) and payload.get("code") not in (None, 200, "200"):
        msg = _safe_error_text(payload)
        code = "stt_balance_insufficient" if "浣欓涓嶈冻" in msg or "balance" in msg.lower() else "stt_create_failed"
        raise AutoCaptionJobError(code, msg, detail=payload)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else {}
    task_id = str(data.get("task_id") or data.get("taskId") or data.get("id") or "").strip()
    if not task_id:
        raise AutoCaptionJobError("stt_task_id_missing", "STT create did not return task_id", detail=payload)
    return {"task_id": task_id, "raw": payload, "data": data}


def _stt_poll_task(token: str, task_id: str, *, job_dir: Path, timeout_seconds: int = 900) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    deadline = time.time() + timeout_seconds
    last_payload: Dict[str, Any] = {}
    with httpx.Client(timeout=120.0, trust_env=False) as client:
        while time.time() < deadline:
            try:
                resp = client.post(
                    f"{_STT_API_BASE}/api/v3/tasks/query",
                    json={"task_id": task_id},
                    headers=headers,
                )
                try:
                    payload = resp.json()
                except Exception:
                    payload = {"raw": resp.text}
            except Exception as exc:
                payload = {"error": str(exc)[:1000]}
            last_payload = payload if isinstance(payload, dict) else {"raw": payload}
            (job_dir / "stt_result_latest.json").write_text(
                json.dumps(last_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            data = last_payload.get("data") if isinstance(last_payload.get("data"), dict) else last_payload
            status = str((data or {}).get("status") or "").strip().lower()
            if status in _STT_TERMINAL_SUCCESS or (isinstance(data, dict) and data.get("output")):
                return data if isinstance(data, dict) else last_payload
            if status in _STT_TERMINAL_FAILURE:
                raise AutoCaptionJobError("stt_task_failed", _safe_error_text(data), detail=last_payload)
            if resp.status_code >= 400:
                msg = _safe_error_text(last_payload)
                code = "stt_balance_insufficient" if "浣欓涓嶈冻" in msg or "balance" in msg.lower() else "stt_query_failed"
                raise AutoCaptionJobError(code, msg, detail=last_payload)
            time.sleep(2.5)
    raise AutoCaptionJobError("stt_timeout", f"STT task timed out: {task_id}", detail=last_payload)


_EDGE_PUNCT = " \t\r\n,.;:!?\uff0c\u3002\uff01\uff1f\uff1b\uff1a\u3001\"'\u201c\u201d\u2018\u2019\uff08\uff09()[]\u3010\u3011<>\u300a\u300b"


def _clean_caption_text(text: Any) -> str:
    s = str(text or "").replace("\u3000", " ").replace("\r\n", "\n").replace("\r", "\n").strip()
    s = re.sub(r"[ \t\f\v]+", " ", s)
    s = re.sub(r" *\n+ *", "\n", s)
    lines = [part.strip(_EDGE_PUNCT).strip() for part in s.split("\n")]
    return "\n".join(part for part in lines if part).strip()


def _caption_text_lines(text: str) -> List[str]:
    lines = [part.strip() for part in str(text or "").split("\n") if part.strip()]
    return lines or [""]


def _caption_display_len(text: str) -> int:
    total = 0
    for ch in text:
        if ch.isspace():
            continue
        total += 1
    return total


def _caption_line_display_len(text: str) -> int:
    return max((_caption_display_len(line) for line in _caption_text_lines(text)), default=0)


def _is_ascii_word_char(ch: str) -> bool:
    return bool(ch and re.match(r"[A-Za-z0-9]", ch))


def _is_cjk_char(ch: str) -> bool:
    return bool(ch and "\u4e00" <= ch <= "\u9fff")


def _caption_needs_space_between(left: str, right: str) -> bool:
    left = str(left or "").rstrip()
    right = str(right or "").lstrip()
    if not left or not right:
        return False
    a = left[-1]
    b = right[0]
    right_lower = right.lower()
    if right_lower in {"'s", "'re", "'ve", "'ll", "'d", "'m", "n't"}:
        return False
    if b in ".,!?;:%)]}\uff0c\u3002\uff01\uff1f\uff1b\uff1a\u3001":
        return False
    if a in "([{":
        return False
    if a in "-/" or b in "-/":
        return False
    if _is_ascii_word_char(a) and _is_ascii_word_char(b):
        return True
    if a in ".,!?;:" and (_is_ascii_word_char(b) or _is_cjk_char(b)):
        return True
    if (_is_ascii_word_char(a) and _is_cjk_char(b)) or (_is_cjk_char(a) and _is_ascii_word_char(b)):
        return True
    return False


def _join_caption_fragments(parts: List[str]) -> str:
    out = ""
    for raw in parts:
        piece = _clean_caption_text(raw)
        if not piece:
            continue
        if out and _caption_needs_space_between(out, piece):
            out += " "
        out += piece
    return _clean_caption_text(out)


def _caption_visual_units(text: str) -> float:
    line_units: List[float] = []
    for line in _caption_text_lines(text):
        total = 0.0
        for ch in line:
            if ch.isspace():
                total += 0.28
            elif re.match(r"[A-Za-z0-9]", ch):
                total += 0.56
            elif ch in _EDGE_PUNCT or ch in _CAPTION_HARD_BREAK_CHARS or ch in _CAPTION_SOFT_BREAK_CHARS:
                total += 0.38
            elif "\u4e00" <= ch <= "\u9fff":
                total += 1.0
            else:
                total += 0.86
        line_units.append(total)
    return max(line_units or [0.0])


def _caption_layout_scale_headroom(caption_style: Dict[str, Any]) -> float:
    layout = str(caption_style.get("ass_layout") or "")
    if layout == "dramatic_hook":
        return 1.34
    if layout == "center_burst":
        return 1.24
    if layout == "side_neon":
        return 1.08
    return 1.06


def _caption_usable_width(caption_style: Dict[str, Any], *, video_width: Optional[int] = None) -> float:
    width = max(360, int(video_width or 720))
    border = max(0, int(caption_style.get("ass_border") or 0))
    shadow = max(0, int(caption_style.get("ass_shadow") or 0))
    layout = str(caption_style.get("ass_layout") or "")
    usable_ratio = 0.84
    if layout == "center_burst":
        usable_ratio = 0.88
    elif layout == "lower_clean":
        usable_ratio = 0.88
    elif layout == "side_neon":
        usable_ratio = 0.62
    elif layout == "dramatic_hook":
        usable_ratio = 0.78
    return max(180.0, width * usable_ratio - border * 4 - shadow * 2)


def _caption_ass_font_size_value(cap: Dict[str, Any], caption_style: Dict[str, Any]) -> int:
    ass_font_size = int(caption_style.get("ass_font_size") or 86)
    base_cli_size = int(caption_style.get("font_size") or 13)
    cap_cli_size = int(cap.get("fontSize") or base_cli_size)
    return max(44, ass_font_size + (cap_cli_size - base_cli_size) * 7)


def _caption_font_unit_width(font_size: int, caption_style: Dict[str, Any]) -> float:
    return max(36.0, font_size * 0.9 * _caption_layout_scale_headroom(caption_style))


def _safe_caption_visual_units(caption_style: Dict[str, Any], *, video_width: Optional[int] = None) -> float:
    configured = max(4, int(caption_style.get("caption_max_chars") or 11))
    pattern = str(caption_style.get("font_size_pattern") or "steady")
    base_cli_size = int(caption_style.get("font_size") or 13)
    worst_cli_size = base_cli_size
    if pattern == "punch":
        worst_cli_size += 2
    elif pattern == "burst":
        worst_cli_size += 1
    ass_font_size = _caption_ass_font_size_value({"fontSize": worst_cli_size}, caption_style)
    usable_width = _caption_usable_width(caption_style, video_width=video_width)
    font_unit_width = _caption_font_unit_width(ass_font_size, caption_style)
    return max(3.0, min(float(configured), usable_width / font_unit_width))


def _caption_visual_overflows(
    cap: Dict[str, Any],
    caption_style: Dict[str, Any],
    *,
    video_width: Optional[int] = None,
) -> bool:
    text = _clean_caption_text(cap.get("text"))
    if not text:
        return False
    usable_width = _caption_usable_width(caption_style, video_width=video_width)
    fs = _caption_ass_font_size_value(cap, caption_style)
    estimated_width = _caption_visual_units(text) * _caption_font_unit_width(fs, caption_style)
    if estimated_width <= usable_width:
        return False
    if "\n" in text and _caption_fits_wrapped_visual_width(
        text,
        max_chars=max(4, int(caption_style.get("caption_max_chars") or 11)),
        max_visual_units=_safe_caption_visual_units(caption_style, video_width=video_width),
    ):
        scaled = int(fs * (usable_width / max(1.0, estimated_width)) * 0.98)
        return scaled < 44
    return True


def _caption_fits_visual_width(
    text: str,
    *,
    max_chars: int,
    max_visual_units: Optional[float],
) -> bool:
    if _caption_line_display_len(text) > max_chars:
        return False
    if max_visual_units is not None and _caption_visual_units(text) > max_visual_units:
        return False
    return True


def _caption_fits_wrapped_visual_width(
    text: str,
    *,
    max_chars: int,
    max_visual_units: Optional[float],
    max_lines: int = 2,
) -> bool:
    lines = _caption_text_lines(text)
    if len(lines) > max_lines:
        return False
    if max((_caption_display_len(line) for line in lines), default=0) > max_chars:
        return False
    multiplier = 2.05 if re.search(r"[A-Za-z]", text) else 1.78
    if max_visual_units is not None and _caption_visual_units(text) > max_visual_units * multiplier:
        return False
    return True


_CAPTION_PROTECTED_PHRASES = (
    "\u9700\u8981\u5177\u5907",
    "\u8d22\u7a0e\u987e\u95ee",
    "\u4e00\u4e2a\u4e13\u4e1a",
    "\u4e13\u4e1a\u9760\u8c31",
    "\u9760\u8c31\u7684",
    "\u5177\u5907",
    "\u9700\u8981",
    "\u8d22\u7a0e",
    "\u987e\u95ee",
    "\u4e13\u4e1a",
    "\u9760\u8c31",
    "\u5b57\u5e55",
    "\u914d\u97f3",
    "\u81ea\u52a8",
    "\u6570\u5b57\u4eba",
)
_CAPTION_BAD_LINE_END_CHARS = set("\u9700\u5177\u8d22\u987e\u4e13\u9760\u6570\u81ea\u914d\u5b57")
_CAPTION_BAD_LINE_START_CHARS = set("\u5907\u95ee\u7a0e\u4e1a\u8c31\u97f3\u5e55\u52a8")
_CAPTION_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['\u2019][A-Za-z0-9]+)?|[\u4e00-\u9fff]|[^\s]")


def _caption_tokens(text: str) -> List[str]:
    return _CAPTION_TOKEN_RE.findall(text or "")


def _caption_boundary_splits_protected(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text):
        return False
    for phrase in _CAPTION_PROTECTED_PHRASES:
        start = text.find(phrase)
        while start >= 0:
            end = start + len(phrase)
            if start < index < end:
                return True
            start = text.find(phrase, start + 1)
    return False


def _caption_boundary_score(text: str, index: int, target_units: float) -> float:
    left = text[:index]
    right = text[index:]
    left_units = _caption_visual_units(left)
    right_units = _caption_visual_units(right)
    score = abs(left_units - right_units) * 1.4 + abs(left_units - target_units) * 0.7
    if _caption_boundary_splits_protected(text, index):
        score += 100.0
    if _caption_display_len(left) < 3 or _caption_display_len(right) < 3:
        score += 35.0
    if left and left[-1] in _CAPTION_BAD_LINE_END_CHARS:
        score += 18.0
    if right and right[0] in _CAPTION_BAD_LINE_START_CHARS:
        score += 18.0
    if left and left[-1] in _CAPTION_HARD_BREAK_CHARS:
        score -= 20.0
    elif left and left[-1] in _CAPTION_SOFT_BREAK_CHARS:
        score -= 10.0
    for phrase in ("\u9700\u8981", "\u5177\u5907", "\u8d22\u7a0e", "\u987e\u95ee"):
        if right.startswith(phrase):
            score -= 6.0
    return score


def _caption_best_two_line_wrap(
    text: str,
    *,
    max_chars: int,
    max_visual_units: Optional[float],
) -> str:
    clean = _clean_caption_text(text)
    if not clean or "\n" in clean:
        return clean
    tokens = _caption_tokens(clean)
    if len(tokens) < 2:
        return clean
    target_units = _caption_visual_units(clean) / 2.0
    best: Tuple[float, str] = (float("inf"), clean)
    for index in range(1, len(tokens)):
        left = _join_caption_fragments(tokens[:index])
        right = _join_caption_fragments(tokens[index:])
        if _caption_display_len(left) < 2 or _caption_display_len(right) < 2:
            continue
        candidate = left + "\n" + right
        if not _caption_fits_wrapped_visual_width(
            candidate,
            max_chars=max_chars,
            max_visual_units=max_visual_units,
        ):
            continue
        plain_left_len = len(_join_caption_fragments(tokens[:index]))
        score = _caption_boundary_score(clean, plain_left_len, target_units)
        if score < best[0]:
            best = (score, candidate)
    return best[1]


def _split_caption_text(
    text: str,
    max_chars: int = 11,
    *,
    max_visual_units: Optional[float] = None,
) -> List[str]:
    tokens = _caption_tokens(text or "")
    expanded: List[str] = []
    for tok in tokens:
        if (
            max_visual_units is not None
            and len(tok) > 1
            and re.match(r"^[A-Za-z0-9]+$", tok)
            and _caption_visual_units(tok) > max_visual_units
        ):
            chunk = ""
            for ch in tok:
                candidate = chunk + ch
                if chunk and not _caption_fits_visual_width(
                    candidate,
                    max_chars=max_chars,
                    max_visual_units=max_visual_units,
                ):
                    expanded.append(chunk)
                    chunk = ch
                else:
                    chunk = candidate
            if chunk:
                expanded.append(chunk)
            continue
        expanded.append(tok)
    tokens = expanded
    chunks: List[str] = []
    cur = ""
    for tok in tokens:
        candidate = _join_caption_fragments([cur, tok])
        if cur and not _caption_fits_visual_width(
            candidate,
            max_chars=max_chars,
            max_visual_units=max_visual_units,
        ):
            chunks.append(_clean_caption_text(cur))
            cur = tok
        else:
            cur = candidate
    if cur:
        chunks.append(_clean_caption_text(cur))
    chunks = [x for x in chunks if x]
    fixed: List[str] = []
    idx = 0
    while idx < len(chunks):
        if idx + 1 < len(chunks):
            joined = _join_caption_fragments([chunks[idx], chunks[idx + 1]])
            wrapped = _caption_best_two_line_wrap(
                joined,
                max_chars=max_chars,
                max_visual_units=max_visual_units,
            )
            if wrapped != joined and _caption_fits_wrapped_visual_width(
                wrapped,
                max_chars=max_chars,
                max_visual_units=max_visual_units,
            ):
                fixed.append(wrapped)
                idx += 2
                continue
        fixed.append(chunks[idx])
        idx += 1
    return fixed


def _wrap_caption_text(
    text: str,
    *,
    max_chars: int,
    max_visual_units: Optional[float],
    max_lines: int = 2,
) -> str:
    clean = _clean_caption_text(text)
    if not clean:
        return ""
    if _caption_fits_visual_width(clean, max_chars=max_chars, max_visual_units=max_visual_units):
        return clean
    if re.search(r"[A-Za-z]", clean) and _caption_fits_wrapped_visual_width(
        clean,
        max_chars=max_chars,
        max_visual_units=max_visual_units,
        max_lines=1,
    ):
        return clean
    best_wrap = _caption_best_two_line_wrap(
        clean,
        max_chars=max_chars,
        max_visual_units=max_visual_units,
    )
    if best_wrap != clean:
        return best_wrap
    visual_limit = max_visual_units if max_visual_units is not None else float(max_chars)
    total_units = _caption_visual_units(clean)
    balanced_limit = (total_units / max(1, max_lines)) + 0.5
    wrap_limit = max(4.0, min(float(max_chars), max(visual_limit, balanced_limit)))
    tokens = _caption_tokens(clean)
    lines: List[str] = []
    cur = ""
    for tok in tokens:
        candidate = _join_caption_fragments([cur, tok])
        if cur and (
            _caption_display_len(candidate) > max_chars
            or _caption_visual_units(candidate) > wrap_limit
        ):
            lines.append(_clean_caption_text(cur))
            cur = tok
        else:
            cur = candidate
    if cur:
        lines.append(_clean_caption_text(cur))
    lines = [line for line in lines if line]
    if not lines or len(lines) > max_lines:
        return clean
    wrapped = "\n".join(lines)
    return (
        wrapped
        if _caption_fits_wrapped_visual_width(
            wrapped,
            max_chars=max_chars,
            max_visual_units=max_visual_units,
            max_lines=max_lines,
        )
        else clean
    )


_CAPTION_HARD_BREAK_CHARS = set(".!?;\u3002\uff01\uff1f\uff1b")
_CAPTION_SOFT_BREAK_CHARS = set(",:\u3001\uff0c\uff1a")


def _caption_word_fragments(text: Any, start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
    raw = str(text or "").replace("\u3000", " ").strip()
    raw = re.sub(r"\s+", " ", raw)
    if not raw:
        return []

    pieces: List[Tuple[str, bool, bool]] = []
    cur = ""
    for ch in raw:
        cur += ch
        if ch in _CAPTION_HARD_BREAK_CHARS:
            pieces.append((cur, True, False))
            cur = ""
        elif ch in _CAPTION_SOFT_BREAK_CHARS:
            pieces.append((cur, False, True))
            cur = ""
    if cur:
        pieces.append((cur, False, False))
    if not pieces:
        return []

    span = max(len(pieces), end_ms - start_ms)
    weights = [max(1, _caption_display_len(piece[0])) for piece in pieces]
    total_weight = max(1, sum(weights))
    cursor = start_ms
    fragments: List[Dict[str, Any]] = []
    for idx, (piece, sentence_end, soft_end) in enumerate(pieces):
        if idx == len(pieces) - 1:
            frag_end = end_ms
        else:
            frag_end = min(end_ms, cursor + max(1, int(span * weights[idx] / total_weight)))
        clean = _clean_caption_text(piece)
        if clean:
            fragments.append(
                {
                    "text": clean,
                    "start_ms": cursor,
                    "end_ms": max(cursor + 1, frag_end),
                    "sentence_end": sentence_end,
                    "soft_end": soft_end,
                }
            )
        elif fragments:
            fragments[-1]["sentence_end"] = bool(fragments[-1].get("sentence_end") or sentence_end)
            fragments[-1]["soft_end"] = bool(fragments[-1].get("soft_end") or soft_end)
        else:
            fragments.append(
                {
                    "text": "",
                    "start_ms": cursor,
                    "end_ms": max(cursor + 1, frag_end),
                    "sentence_end": sentence_end,
                    "soft_end": soft_end,
                }
            )
        cursor = max(cursor + 1, frag_end)
    return fragments


def _caption_utterance_segments(utterances: Any) -> List[Dict[str, Any]]:
    if not isinstance(utterances, list):
        return []

    segments: List[Dict[str, Any]] = []
    for utt in utterances:
        if not isinstance(utt, dict):
            continue
        utt_words: List[Dict[str, Any]] = []
        for item in utt.get("words") or []:
            if not isinstance(item, dict):
                continue
            try:
                start_ms = int(float(item.get("start_time")))
                end_ms = int(float(item.get("end_time")))
            except Exception:
                continue
            if start_ms < 0 or end_ms <= start_ms:
                continue
            utt_words.extend(_caption_word_fragments(item.get("text"), start_ms, end_ms))
        if utt_words:
            utt_words.sort(key=lambda x: (x["start_ms"], x["end_ms"]))
            segments.append(
                {
                    "words": utt_words,
                    "start_ms": int(utt_words[0]["start_ms"]),
                    "end_ms": int(utt_words[-1]["end_ms"]),
                }
            )
            continue

        text = _clean_caption_text(utt.get("text"))
        if not text:
            continue
        try:
            start_ms = int(float(utt.get("start_time") or 0))
            end_ms = int(float(utt.get("end_time") or start_ms + 1200))
        except Exception:
            start_ms, end_ms = 0, 1200
        if end_ms <= start_ms:
            end_ms = start_ms + 1200
        segments.append({"text": text, "start_ms": start_ms, "end_ms": end_ms})

    segments.sort(key=lambda x: (int(x.get("start_ms") or 0), int(x.get("end_ms") or 0)))
    return segments


def _extract_stt_output(stt_data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(stt_data, dict):
        return {}
    for key in ("output", "result"):
        value = stt_data.get(key)
        if isinstance(value, dict):
            return value
    data = stt_data.get("data")
    if isinstance(data, dict):
        return _extract_stt_output(data)
    return stt_data


def _captions_from_stt(
    stt_data: Dict[str, Any],
    *,
    video_duration_sec: float,
    caption_style: Dict[str, Any],
    video_width: Optional[int] = None,
) -> List[Dict[str, Any]]:
    output = _extract_stt_output(stt_data)
    utterances = output.get("utterances") if isinstance(output, dict) else None
    captions: List[Dict[str, Any]] = []
    video_end_us = max(100_000, int(max(video_duration_sec, 0.1) * 1_000_000))
    utterance_segments = _caption_utterance_segments(utterances)
    max_chars = int(caption_style.get("caption_max_chars") or 11)
    max_visual_units = _safe_caption_visual_units(caption_style, video_width=video_width)
    font_size_pattern = str(caption_style.get("font_size_pattern") or "steady")
    base_font_size = int(caption_style.get("font_size") or 13)

    def caption_font_size(index: int, text: str) -> int:
        display_len = _caption_display_len(text)
        wrap_adjust = 1 if "\n" in text else 0
        if re.search(r"[A-Za-z]", text) and " " in text:
            wrap_adjust += 1
        if font_size_pattern == "punch":
            return max(9, base_font_size + (2 if display_len <= 4 else 0) - wrap_adjust)
        if font_size_pattern == "burst":
            return max(9, base_font_size + (1 if index % 2 == 0 else 0) - wrap_adjust)
        if font_size_pattern == "side_neon":
            return max(9, base_font_size - (1 if display_len >= 8 else 0) - wrap_adjust)
        return max(9, base_font_size - wrap_adjust)

    def caption_position(index: int) -> Tuple[Optional[float], Optional[float]]:
        layout = str(caption_style.get("ass_layout") or "")
        if layout == "side_neon":
            return (-0.58, 0.46 if index % 2 == 0 else 0.32)
        if layout == "dramatic_hook":
            return (0.0, -0.26 if index % 2 == 0 else -0.40)
        if layout == "center_burst":
            return (0.0, -0.52 if index % 2 == 0 else -0.64)
        if layout == "lower_clean":
            return (0.0, -0.72)
        return None, None

    def add_caption(text: str, start_ms: int, end_ms: int) -> None:
        clean = _clean_caption_text(text)
        if not clean:
            return
        wrapped = _wrap_caption_text(
            clean,
            max_chars=max_chars,
            max_visual_units=max_visual_units,
        )
        if wrapped:
            clean = wrapped
        if not _caption_fits_wrapped_visual_width(
            clean,
            max_chars=max_chars,
            max_visual_units=max_visual_units,
        ):
            sub_chunks = _split_caption_text(
                clean,
                max_chars=max_chars,
                max_visual_units=max_visual_units,
            )
            if len(sub_chunks) > 1:
                span_ms = max(len(sub_chunks) * 320, end_ms - start_ms)
                step_ms = max(320, int(span_ms / len(sub_chunks)))
                for idx, chunk in enumerate(sub_chunks):
                    add_caption(chunk, start_ms + idx * step_ms, start_ms + (idx + 1) * step_ms)
                return
        start_us = max(0, int(start_ms * 1000))
        end_us = max(start_us + 450_000, int(end_ms * 1000))
        if start_us >= video_end_us:
            return
        end_us = min(video_end_us, end_us)
        if captions and start_us < int(captions[-1]["end"]):
            start_us = int(captions[-1]["end"])
            end_us = max(end_us, start_us + 350_000)
        if end_us <= start_us:
            return
        item = {
            "text": clean,
            "start": start_us,
            "end": min(video_end_us, end_us),
        }
        item["fontSize"] = caption_font_size(len(captions), clean)
        pos_x, pos_y = caption_position(len(captions))
        if pos_x is not None:
            item["transformX"] = pos_x
        if pos_y is not None:
            item["transformY"] = pos_y
        in_animation = str(caption_style.get("in_animation") or "").strip()
        if in_animation:
            item["inAnimation"] = in_animation
            item["inAnimationDuration"] = int(caption_style.get("in_animation_duration") or 0)
        loop_animation = str(caption_style.get("loop_animation") or "").strip()
        if loop_animation:
            item["loopAnimation"] = loop_animation
            item["loopAnimationDuration"] = int(caption_style.get("loop_animation_duration") or 0)
        captions.append(item)

    def add_caption_chunks(text: str, start_ms: int, end_ms: int, *, min_step_ms: int = 500) -> None:
        chunks = _split_caption_text(
            text,
            max_chars=max_chars,
            max_visual_units=max_visual_units,
        )
        if not chunks:
            return
        span = max(min_step_ms, end_ms - start_ms)
        step = max(min_step_ms, int(span / len(chunks)))
        for idx, chunk in enumerate(chunks):
            add_caption(chunk, start_ms + idx * step, start_ms + (idx + 1) * step)

    if utterance_segments:
        for entry in utterance_segments:
            segment = entry.get("words")
            if not segment:
                add_caption_chunks(
                    str(entry.get("text") or ""),
                    int(entry.get("start_ms") or 0),
                    int(entry.get("end_ms") or 0),
                    min_step_ms=500,
                )
                continue
            cur_words: List[str] = []
            cur_start: Optional[int] = None
            cur_end = 0
            for idx, word in enumerate(segment):
                text = str(word.get("text") or "")
                if not text:
                    if cur_words and word.get("sentence_end") and cur_start is not None:
                        add_caption(_join_caption_fragments(cur_words), cur_start, cur_end)
                        cur_words = []
                        cur_start = None
                    continue

                gap_ms = int(word["start_ms"] - cur_end) if cur_words else 0
                candidate_start = cur_start if cur_start is not None else int(word["start_ms"])
                candidate = _join_caption_fragments(cur_words + [text])
                candidate_display = _wrap_caption_text(
                    candidate,
                    max_chars=max_chars,
                    max_visual_units=max_visual_units,
                ) or candidate
                dur_ms = int(word["end_ms"] - candidate_start)
                if cur_words and (
                    gap_ms >= 360
                    or not _caption_fits_wrapped_visual_width(candidate_display, max_chars=max_chars, max_visual_units=max_visual_units)
                    or dur_ms > 2300
                ):
                    add_caption(_join_caption_fragments(cur_words), int(cur_start or 0), cur_end)
                    cur_words = []
                    cur_start = None

                if cur_start is None:
                    cur_start = int(word["start_ms"])
                cur_words.append(text)
                cur_end = int(word["end_ms"])

                current = _join_caption_fragments(cur_words)
                next_word = segment[idx + 1] if idx + 1 < len(segment) else None
                next_gap = int(next_word["start_ms"] - cur_end) if next_word else None
                current_ms = int(cur_end - cur_start)
                hard_after = bool(word.get("sentence_end")) or (next_gap is not None and next_gap >= 480)
                soft_after = bool(word.get("soft_end")) and (
                    _caption_display_len(current) >= 6 or current_ms >= 900
                )
                full_enough = (
                    not _caption_fits_wrapped_visual_width(
                        _wrap_caption_text(current, max_chars=max_chars, max_visual_units=max_visual_units) or current,
                        max_chars=max_chars,
                        max_visual_units=max_visual_units,
                    )
                    or current_ms >= 2300
                )
                if hard_after or soft_after or full_enough:
                    add_caption(_join_caption_fragments(cur_words), cur_start, cur_end)
                    cur_words = []
                    cur_start = None
                    cur_end = 0

            if cur_words and cur_start is not None:
                add_caption(_join_caption_fragments(cur_words), cur_start, cur_end)
    else:
        text = _clean_caption_text(output.get("text") if isinstance(output, dict) else "")
        add_caption_chunks(text, 0, int(video_end_us / 1000), min_step_ms=900)

    deduped: List[Dict[str, Any]] = []
    prev = ""
    for cap in captions:
        text = _clean_caption_text(cap.get("text"))
        if not text or text == prev:
            continue
        item = dict(cap)
        item["text"] = text
        deduped.append(item)
        prev = text
    return deduped


def _validate_caption_quality(
    captions: List[Dict[str, Any]],
    *,
    caption_style: Dict[str, Any],
    video_width: Optional[int] = None,
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    duplicate_adjacent = 0
    overlap_count = 0
    visual_overflow_count = 0
    last_text = ""
    last_end = -1
    for cap in captions:
        text = _clean_caption_text(cap.get("text"))
        start = int(cap.get("start") or 0)
        end = int(cap.get("end") or 0)
        if not text:
            errors.append("empty_caption_text")
        if end <= start:
            errors.append("invalid_caption_time")
        if _caption_visual_overflows(cap, caption_style, video_width=video_width):
            visual_overflow_count += 1
            errors.append("caption_visual_overflow")
        if last_text and text == last_text:
            duplicate_adjacent += 1
        if last_end >= 0 and start < last_end:
            overlap_count += 1
        dur = end - start
        if dur < 300_000:
            warnings.append("caption_duration_too_short")
        if dur > 4_000_000:
            warnings.append("caption_duration_too_long")
        last_text = text
        last_end = max(last_end, end)
    if not captions:
        errors.append("no_captions")
    if duplicate_adjacent:
        errors.append("duplicate_adjacent_captions")
    if overlap_count:
        errors.append("overlapping_captions")
    quality = {
        "caption_count": len(captions),
        "duplicate_adjacent_count": duplicate_adjacent,
        "overlap_count": overlap_count,
        "visual_overflow_count": visual_overflow_count,
        "safe_visual_units": round(_safe_caption_visual_units(caption_style, video_width=video_width), 2),
        "source_width": int(video_width or 0),
        "expected_caption_tracks": 1,
        "background_enabled": False,
        "text_effect": caption_style.get("text_effect") or "",
        "text_effect_id": caption_style.get("text_effect_id") or "",
        "caption_style": _caption_style_public(caption_style),
        "stt_model": _STT_MODEL,
    }
    return quality, errors, sorted(set(warnings))


def _cutcli_caption_payload(captions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    allowed = {
        "text",
        "start",
        "end",
        "keyword",
        "keywordColor",
        "fontSize",
        "inAnimation",
        "outAnimation",
        "loopAnimation",
        "inAnimationDuration",
        "outAnimationDuration",
        "loopAnimationDuration",
    }
    return [
        {key: value for key, value in cap.items() if key in allowed}
        for cap in captions
        if _clean_caption_text(cap.get("text"))
    ]


def _build_auto_caption_cutcli_draft(
    *,
    cutcli: str,
    job_dir: Path,
    source: str,
    source_info: Dict[str, Any],
    captions: List[Dict[str, Any]],
    caption_style: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], Dict[str, Any], List[str]]:
    warnings: List[str] = []
    env = _job_cutcli_env(job_dir)
    draft_name = "lobster_caption_" + job_dir.name
    draft_width = int(source_info.get("width") or 1080)
    draft_height = int(source_info.get("height") or 1920)
    created = _json_from_cmd(
        [
            cutcli,
            "draft",
            "create",
            "--name",
            draft_name,
            "--width",
            str(draft_width),
            "--height",
            str(draft_height),
            "--pretty",
        ],
        timeout=60,
        env=env,
    )
    draft_id = str(created.get("draftId") or draft_name)
    duration_us = max(100_000, int(float(source_info.get("duration") or 0.1) * 1_000_000))
    video_json = _write_json(
        job_dir / "cutcli_auto_video.json",
        [
            {
                "videoUrl": source,
                "width": int(source_info.get("width") or 1080),
                "height": int(source_info.get("height") or 1920),
                "duration": duration_us,
                "start": 0,
                "end": duration_us,
                "volume": 1,
            }
        ],
    )
    _run_cmd([cutcli, "videos", "add", draft_id, "--video-infos", video_json], timeout=180, env=env)

    captions_json = _write_json(job_dir / "cutcli_auto_captions.json", _cutcli_caption_payload(captions))
    caption_cmd = [
        cutcli,
        "captions",
        "add",
        draft_id,
        "--captions",
        captions_json,
        "--font-size",
        str(caption_style.get("font_size") or 13),
        "--bold",
        "--alignment",
        "0",
        "--text-color",
        str(caption_style.get("text_color") or "#FFFFFF"),
        "--border-color",
        str(caption_style.get("border_color") or "#061A48"),
        "--border-width",
        str(caption_style.get("border_width") or "0.08"),
        "--transform-x",
        str(caption_style.get("transform_x") or "0"),
        "--transform-y",
        str(caption_style.get("transform_y") or "-0.66"),
    ]
    if caption_style.get("has_shadow", True):
        caption_cmd.extend(["--has-shadow", "--shadow-color", str(caption_style.get("shadow_color") or "#000000")])
    text_effect = str(caption_style.get("text_effect") or "").strip()
    if text_effect:
        caption_cmd.extend(["--text-effect", text_effect])
    _run_cmd(caption_cmd, timeout=180, env=env)

    captions_list: Any = []
    try:
        captions_list = _json_value_from_cmd([cutcli, "captions", "list", draft_id], timeout=90, env=env)
        (job_dir / "captions_list.json").write_text(
            json.dumps(captions_list, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        warnings.append(f"caption_list_failed: {str(exc)[:180]}")

    actual_tracks = 0
    actual_captions = 0
    if isinstance(captions_list, list):
        actual_captions = len(captions_list)
        tracks = {str(item.get("trackId") or "") for item in captions_list if isinstance(item, dict)}
        actual_tracks = len({x for x in tracks if x})
        if actual_tracks != 1:
            raise AutoCaptionJobError("caption_track_quality_failed", f"expected 1 caption track, got {actual_tracks}")
        if actual_captions != len(captions):
            warnings.append(f"caption_count_mismatch: expected {len(captions)} got {actual_captions}")

    info = _json_from_cmd([cutcli, "draft", "info", draft_id, "--pretty"], timeout=60, env=env)
    (job_dir / "draft_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    quality = {
        "actual_caption_count": actual_captions,
        "actual_caption_tracks": actual_tracks,
        "background_enabled": False,
        "text_effect": caption_style.get("text_effect") or "",
        "text_effect_id": caption_style.get("text_effect_id") or "",
        "caption_style": _caption_style_public(caption_style),
    }
    return draft_id, info, quality, warnings


def _mirror_video_url_to_tos(url: str, *, job_id: str) -> Tuple[str, Optional[int], List[str]]:
    warnings: List[str] = []
    if not url.startswith(("http://", "https://")):
        return url, None, ["render_result_is_not_http_url"]
    try:
        with httpx.Client(timeout=300.0, follow_redirects=True, trust_env=False) as client:
            resp = client.get(url)
        if resp.status_code >= 400:
            warnings.append(f"final_download_http_{resp.status_code}")
            return url, None, warnings
        data = resp.content
        tos_url = _upload_to_tos(data, f"assets/cutcli_auto_caption/{job_id}/final.mp4", "video/mp4")
        if tos_url:
            return tos_url, len(data), warnings
        warnings.append("final_tos_upload_failed")
        return url, len(data), warnings
    except Exception as exc:
        warnings.append(f"final_mirror_failed: {str(exc)[:180]}")
        return url, None, warnings


def _save_auto_caption_asset(
    *,
    db: Session,
    user_id: int,
    job_id: str,
    template: Dict[str, Any],
    source_name: str,
    source_asset_id: Optional[str],
    final_url: str,
    file_size: Optional[int],
    draft_id: str,
    cloud_job_id: Optional[str],
    quality: Dict[str, Any],
    warnings: List[str],
    token_source: str = "",
    token_masked: str = "",
) -> str:
    out_asset_id = uuid.uuid4().hex[:12]
    asset = Asset(
        asset_id=out_asset_id,
        user_id=user_id,
        filename=f"auto_caption_{job_id}.mp4",
        media_type="video",
        file_size=file_size,
        source_url=final_url,
        prompt=f"{template.get('name') or 'auto caption template'} | {source_name}",
        model=f"cutcli:{template.get('id') or _AUTO_CAPTION_TEMPLATE_ID}",
        tags="cutcli_template,auto_caption,huazi,server_render",
        meta={
            "cutcli_template_id": template.get("id") or _AUTO_CAPTION_TEMPLATE_ID,
            "cutcli_template_name": template.get("name") or "",
            "cutcli_job_id": job_id,
            "cutcli_draft_id": draft_id,
            "cutcli_cloud_job_id": cloud_job_id,
            "source_asset_id": source_asset_id,
            "source_name": source_name,
            "stt_model": _STT_MODEL,
            "sutui_token_source": token_source,
            "sutui_token_masked": token_masked,
            "quality": quality,
            "warnings": warnings,
            "created_at": int(time.time()),
        },
    )
    db.add(asset)
    db.commit()
    return out_asset_id


def _run_auto_caption_job_sync(
    *,
    job_id: str,
    user_id: int,
    template_id: str,
    source: str,
    source_asset_id: Optional[str],
    source_name: str,
    source_info: Dict[str, Any],
    audio_url: str = "",
) -> None:
    job_dir = _JOBS_DIR / job_id
    warnings: List[str] = []
    template = _TEMPLATES.get(template_id) or _TEMPLATES[_AUTO_CAPTION_TEMPLATE_ID]
    caption_style = _caption_style_for_template(template)
    db = SessionLocal()
    ffmpeg: Optional[str] = None
    try:
        cutcli = _find_cutcli_bin()
        stt_audio_url = _clean_public_http_url(audio_url)
        if stt_audio_url:
            _update_cutcli_job(
                job_id,
                status="running",
                stage="stt_create",
                result_updates={"audio_url": stt_audio_url},
                meta_updates={
                    "stt_model": _STT_MODEL,
                    "audio_source": "client_audio_url",
                },
            )
        else:
            _update_cutcli_job(job_id, status="running", stage="extract_audio")
            ffmpeg = _find_ffmpeg_bin()
            audio_path = job_dir / "audio.wav"
            _extract_audio_wav(ffmpeg=ffmpeg, source=source, out_path=audio_path)

            _update_cutcli_job(job_id, stage="upload_audio")
            stt_audio_url = _upload_job_file_to_tos(
                audio_path,
                object_key=f"assets/cutcli_auto_caption/{job_id}/audio.wav",
                content_type="audio/wav",
            )
            _update_cutcli_job(
                job_id,
                stage="stt_create",
                result_updates={"audio_url": stt_audio_url},
                meta_updates={
                    "stt_model": _STT_MODEL,
                    "audio_source": "server_extracted_wav",
                },
            )

        token, token_source = _load_sutui_token_for_stt(db, user_id)
        token_masked = _mask_token(token)
        _update_cutcli_job(
            job_id,
            meta_updates={
                "token_source": token_source,
                "token_masked": token_masked,
            },
        )
        stt_created = _stt_create_task(token, stt_audio_url, job_dir=job_dir)
        task_id = stt_created["task_id"]
        _update_cutcli_job(
            job_id,
            stage="stt_poll",
            provider_task_id=task_id,
            result_updates={"stt_task_id": task_id},
        )

        stt_data = _stt_poll_task(token, task_id, job_dir=job_dir)
        captions = _captions_from_stt(
            stt_data,
            video_duration_sec=float(source_info.get("duration") or 0.1),
            caption_style=caption_style,
            video_width=int(source_info.get("width") or 0) or None,
        )
        (job_dir / "generated_captions.json").write_text(
            json.dumps(captions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        quality, quality_errors, quality_warnings = _validate_caption_quality(
            captions,
            caption_style=caption_style,
            video_width=int(source_info.get("width") or 0) or None,
        )
        warnings.extend(quality_warnings)
        if quality_errors:
            raise AutoCaptionJobError("caption_quality_failed", ",".join(sorted(set(quality_errors))))
        _update_cutcli_job(
            job_id,
            stage="build_draft",
            result_updates={
                "quality": quality,
                "caption_count": len(captions),
                "warnings": warnings,
            },
        )

        draft_id, draft_info, draft_quality, draft_warnings = _build_auto_caption_cutcli_draft(
            cutcli=cutcli,
            job_dir=job_dir,
            source=source,
            source_info=source_info,
            captions=captions,
            caption_style=caption_style,
        )
        quality.update(draft_quality)
        warnings.extend(draft_warnings)

        _update_cutcli_job(
            job_id,
            stage="cloud_render",
            result_updates={
                "draft_id": draft_id,
                "quality": quality,
                "warnings": warnings,
            },
        )
        cloud_result: Dict[str, Any] = {}
        cloud_job_id: Optional[str] = None
        try:
            cloud_result = _render_cutcli_cloud(cutcli, draft_id, job_dir=job_dir, api_key=token, timeout_seconds=300)
            preview_url = _extract_first_video_url(cloud_result)
            if not preview_url:
                raise AutoCaptionJobError("render_url_missing", "CutCLI cloud render did not return a video URL")
            cloud_job_id = _extract_job_id(cloud_result)

            _update_cutcli_job(
                job_id,
                stage="mirror_result",
                provider_task_id=cloud_job_id,
                result_updates={
                    "cloud_job_id": cloud_job_id,
                    "preview_url": preview_url,
                },
            )
            final_url, file_size, mirror_warnings = _mirror_video_url_to_tos(preview_url, job_id=job_id)
            warnings.extend(mirror_warnings)
            render_strategy = "cutcli_cloud"
        except AutoCaptionJobError as exc:
            if exc.code != "cloud_render_queued_timeout":
                raise
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            cloud_job_id = str(detail.get("job_id") or "") or _extract_job_id(detail) or None
            warnings.append(exc.code)
            _update_cutcli_job(
                job_id,
                stage="fallback_render",
                provider_task_id=cloud_job_id,
                result_updates={
                    "cloud_job_id": cloud_job_id,
                    "cloud_render_detail": detail,
                    "warnings": warnings,
                },
            )
            ffmpeg = ffmpeg or _find_ffmpeg_bin()
            final_url, file_size, fallback_warnings = _render_fallback_caption_video(
                ffmpeg=ffmpeg,
                job_dir=job_dir,
                source=source,
                captions=captions,
                job_id=job_id,
                caption_style=caption_style,
                source_info=source_info,
            )
            warnings.extend(fallback_warnings)
            render_strategy = "ffmpeg_ass_fallback"

        asset_id = _save_auto_caption_asset(
            db=db,
            user_id=user_id,
            job_id=job_id,
            template=template,
            source_name=source_name,
            source_asset_id=source_asset_id,
            final_url=final_url,
            file_size=file_size,
            draft_id=draft_id,
            cloud_job_id=cloud_job_id,
            quality=quality,
            warnings=warnings,
            token_source=token_source,
            token_masked=token_masked,
        )
        _update_cutcli_job(
            job_id,
            status="completed",
            stage="completed",
            error=None,
            asset_ids=[asset_id],
            result_updates={
                "draft_id": draft_id,
                "cloud_job_id": cloud_job_id,
                "preview_url": final_url,
                "open_url": final_url,
                "preview_asset_id": asset_id,
                "final_asset_id": asset_id,
                "source_asset_id": source_asset_id,
                "source_name": source_name,
                "quality": quality,
                "caption_count": len(captions),
                "warnings": warnings,
                "render_strategy": render_strategy,
                "draft_info": draft_info,
            },
            meta_updates={
                "token_source": token_source,
                "token_masked": token_masked,
            },
        )
    except AutoCaptionJobError as exc:
        logger.warning("[cutcli-auto-caption] job failed job_id=%s code=%s msg=%s", job_id, exc.code, exc.message)
        _update_cutcli_job(
            job_id,
            status="failed",
            stage="failed",
            error=exc.message,
            result_updates={
                "error_code": exc.code,
                "error_detail": exc.detail,
                "warnings": warnings,
            },
        )
    except Exception as exc:
        logger.exception("[cutcli-auto-caption] job failed job_id=%s", job_id)
        _update_cutcli_job(
            job_id,
            status="failed",
            stage="failed",
            error=str(exc)[:2000],
            result_updates={
                "error_code": "auto_caption_failed",
                "warnings": warnings,
            },
        )
    finally:
        db.close()
        _cleanup_auto_caption_workspace_and_record(job_id)


def _build_cutcli_draft(
    *,
    cutcli: str,
    job_dir: Path,
    source: str,
    source_info: Dict[str, Any],
    overlay: Optional[Path],
    duration: int,
    title: str,
    subtitle: str,
) -> Tuple[str, Dict[str, Any], List[str]]:
    warnings: List[str] = []
    env = _job_cutcli_env(job_dir)
    draft_name = "lobster_tpl_" + job_dir.name
    created = _json_from_cmd(
        [cutcli, "draft", "create", "--name", draft_name, "--width", "1080", "--height", "1920", "--pretty"],
        timeout=60,
        env=env,
    )
    draft_id = str(created.get("draftId") or draft_name)
    duration_us = int(duration * 1_000_000)

    video_json = _write_json(
        job_dir / "cutcli_video.json",
        [
            {
                "videoUrl": source,
                "width": int(source_info.get("width") or 1080),
                "height": int(source_info.get("height") or 1920),
                "duration": duration_us,
                "start": 0,
                "end": duration_us,
                "volume": 1,
            }
        ],
    )
    _run_cmd([cutcli, "videos", "add", draft_id, "--video-infos", video_json], timeout=120, env=env)

    if overlay and overlay.exists():
        overlay_json = _write_json(
            job_dir / "cutcli_overlay.json",
            [
                {
                    "imageUrl": str(overlay),
                    "width": 1080,
                    "height": 1920,
                    "start": 0,
                    "end": duration_us,
                }
            ],
        )
        try:
            _run_cmd([cutcli, "images", "add", draft_id, "--image-infos", overlay_json], timeout=120, env=env)
        except Exception as exc:
            warnings.append(f"overlay: {str(exc)[:180]}")
            logger.warning("[CutCLI妯℃澘] overlay add failed: %s", exc)

    title_json = _write_json(
        job_dir / "cutcli_title.json",
        [{"text": (title or "鍝佺墝楂樺厜鏃跺埢")[:28], "start": 200_000, "end": min(duration_us, 2_800_000)}],
    )
    _run_cmd(
        [
            cutcli,
            "captions",
            "add",
            draft_id,
            "--captions",
            title_json,
            "--font-size",
            "11",
            "--bold",
            "--text-color",
            "#FFFFFF",
            "--border-color",
            "#111827",
            "--border-width",
            "0.04",
            "--transform-y",
            "-0.62",
        ],
        timeout=90,
        env=env,
    )

    if subtitle:
        subtitle_json = _write_json(
            job_dir / "cutcli_subtitle.json",
            [{"text": subtitle[:46], "start": 2_000_000, "end": max(2_200_000, duration_us - 600_000)}],
        )
        _run_cmd(
            [
                cutcli,
                "captions",
                "add",
                draft_id,
                "--captions",
                subtitle_json,
                "--font-size",
                "7",
                "--text-color",
                "#E8ECF2",
                "--transform-y",
                "-0.72",
            ],
            timeout=90,
            env=env,
        )

    optional_steps = [
        (
            "effect",
            [
                cutcli,
                "effects",
                "add",
                draft_id,
                "--effect-infos",
                _write_json(
                    job_dir / "cutcli_effect.json",
                    [{"effectTitle": "鑳剁墖婕忓厜", "start": 0, "end": min(duration_us, 2_500_000)}],
                ),
            ],
        ),
    ]
    for label, cmd in optional_steps:
        try:
            _run_cmd(cmd, timeout=90, env=env)
        except Exception as exc:
            warnings.append(f"{label}: {str(exc)[:180]}")
            logger.warning("[CutCLI妯℃澘] optional %s failed: %s", label, exc)

    info = _json_from_cmd([cutcli, "draft", "info", draft_id, "--pretty"], timeout=60, env=env)
    (job_dir / "draft_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return draft_id, info, warnings


def _cutcli_cloud_env(api_key: str = "") -> Dict[str, str]:
    env = os.environ.copy()
    key = (api_key or "").strip()
    if key:
        env["CUTCLI_API_KEY"] = key
        env["APIZ_API_KEY"] = key
        env["XSKILL_API_KEY"] = key
    api_base_value = (getattr(settings, "sutui_api_base", None) or os.environ.get("SUTUI_API_BASE") or "").strip().rstrip("/")
    if api_base_value:
        env["CUTCLI_API_BASE"] = api_base_value
    api_base = (env.get("CUTCLI_API_BASE") or "").rstrip("/").lower()
    if api_base.endswith("/api") or "/api/cutcli" in api_base or "/mcp" in api_base:
        env["CUTCLI_API_BASE"] = "https://api.xskill.ai"
    return env


def _job_cutcli_env(job_dir: Path, env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    out = dict(env or os.environ)
    drafts_dir = job_dir / "cutcli_drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    out["CUT_DRAFTS_DIR"] = str(drafts_dir)
    return out


def _collect_urls(value: Any, *, key_hint: str = "") -> List[Tuple[int, str]]:
    urls: List[Tuple[int, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            urls.extend(_collect_urls(item, key_hint=str(key)))
        return urls
    if isinstance(value, list):
        for item in value:
            urls.extend(_collect_urls(item, key_hint=key_hint))
        return urls
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        return urls

    hint = key_hint.lower()
    lowered = value.lower()
    score = 0
    if any(token in hint for token in ("video", "render", "result", "output", "download", "url")):
        score += 5
    if any(ext in lowered for ext in (".mp4", ".mov", ".webm")):
        score += 10
    if any(ext in lowered for ext in (".zip", ".json")):
        score -= 50
    return [(score, value)]


def _extract_first_video_url(value: Dict[str, Any]) -> Optional[str]:
    urls = [
        item
        for item in _collect_urls(value)
        if any(ext in item[1].lower() for ext in (".mp4", ".mov", ".webm"))
    ]
    if not urls:
        return None
    urls.sort(key=lambda item: item[0], reverse=True)
    return urls[0][1]


def _extract_job_id(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            key_l = str(key).lower()
            if key_l in {"job_id", "jobid"} or ("job" in key_l and key_l.endswith("id")):
                if isinstance(item, (str, int)):
                    return str(item)
            nested = _extract_job_id(item)
            if nested:
                return nested
    if isinstance(value, list):
        for item in value:
            nested = _extract_job_id(item)
            if nested:
                return nested
    return None


def _configured_cutcli_api_base() -> str:
    raw = (getattr(settings, "sutui_api_base", None) or os.environ.get("SUTUI_API_BASE") or "https://api.xskill.ai").strip().rstrip("/")
    low = raw.lower()
    if low.endswith("/api") or "/api/cutcli" in low or "/mcp" in low:
        return "https://api.xskill.ai"
    return raw or "https://api.xskill.ai"


def _ensure_cutcli_uses_token(cutcli: str, *, api_key: str, job_dir: Path, env: Dict[str, str]) -> None:
    expected = (api_key or "").strip()
    if not expected:
        raise AutoCaptionJobError("cutcli_token_missing", "server sutui token is not configured for CutCLI render")
    expected_prefix = expected[:10]
    try:
        who = _json_from_cmd([cutcli, "auth", "whoami", "--pretty"], timeout=30, env=env)
        key_prefix = str(((who.get("user") or {}).get("apiKeyPrefix") or "")).replace("*", "")
        if key_prefix and expected_prefix.startswith(key_prefix[: min(len(key_prefix), len(expected_prefix))]):
            return
    except Exception:
        pass

    # Isolate CutCLI auth state for this job so server jobs never use a desktop cached key.
    isolated_home = job_dir / "cutcli_home"
    isolated_home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(isolated_home)
    env["USERPROFILE"] = str(isolated_home)
    env["CUTCLI_API_KEY"] = expected
    env["APIZ_API_KEY"] = expected
    env["XSKILL_API_KEY"] = expected
    _run_cmd(
        [
            cutcli,
            "auth",
            "set",
            "--api-key",
            expected,
            "--api-base",
            _configured_cutcli_api_base(),
        ],
        timeout=60,
        env=env,
    )
    who = _json_from_cmd([cutcli, "auth", "whoami", "--pretty"], timeout=30, env=env)
    key_prefix = str(((who.get("user") or {}).get("apiKeyPrefix") or "")).replace("*", "")
    if key_prefix and not expected_prefix.startswith(key_prefix[: min(len(key_prefix), len(expected_prefix))]):
        raise AutoCaptionJobError(
            "cutcli_token_mismatch",
            f"CutCLI token mismatch: expected {_mask_token(expected)}, got prefix {key_prefix}",
        )


def _render_job_payload(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    for key in ("renderJob", "render_job", "job", "data"):
        item = value.get(key)
        if isinstance(item, dict):
            return item
    return value


def _render_job_status(value: Any) -> str:
    payload = _render_job_payload(value)
    return str(payload.get("status") or "").strip().lower()


def _render_job_failure_reason(value: Any) -> str:
    payload = _render_job_payload(value)
    return str(
        payload.get("failure_reason")
        or payload.get("failureReason")
        or payload.get("error")
        or payload.get("message")
        or ""
    ).strip()


def _render_cutcli_cloud(
    cutcli: str,
    draft_id: str,
    *,
    job_dir: Path,
    api_key: str,
    zip_path: Optional[Path] = None,
    timeout_seconds: int = 1800,
) -> Dict[str, Any]:
    if not (api_key or "").strip():
        raise AutoCaptionJobError("cutcli_token_missing", "server sutui token is not configured for CutCLI render")
    env = _job_cutcli_env(job_dir, _cutcli_cloud_env(api_key))
    try:
        _ensure_cutcli_uses_token(cutcli, api_key=api_key, job_dir=job_dir, env=env)
    except AutoCaptionJobError:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"鏈嶅姟绔?CutCLI 浜戞覆鏌撴湭瀹屾垚鐧诲綍閰嶇疆锛屾棤娉曠敓鎴愯棰? {str(exc)[:500]}",
        ) from exc

    render_cmd = [cutcli, "cloud", "render", draft_id]
    if zip_path is not None:
        render_cmd.extend(["--zip", str(zip_path)])
    render_cmd.append("--pretty")
    render_result = _json_from_cmd(render_cmd, timeout=300, env=env)
    (job_dir / "cloud_render_submit.json").write_text(
        json.dumps(render_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    cloud_job_id = _extract_job_id(render_result)
    if not cloud_job_id:
        return render_result
    deadline = time.time() + timeout_seconds
    last_result: Dict[str, Any] = {}
    while time.time() < deadline:
        cloud_result = _json_from_cmd(
            [cutcli, "cloud", "result", cloud_job_id, "--pretty"],
            timeout=120,
            env=env,
        )
        last_result = cloud_result
        (job_dir / "cloud_result_latest.json").write_text(
            json.dumps(cloud_result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        video_url = _extract_first_video_url(cloud_result)
        status = _render_job_status(cloud_result)
        if video_url or status in {"completed", "complete", "success", "succeeded", "finished", "done"}:
            (job_dir / "cloud_result.json").write_text(
                json.dumps(cloud_result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return {"render": render_result, "result": cloud_result, "job_id": cloud_job_id}
        if status in {"failed", "error", "cancelled", "canceled", "rejected"}:
            raise AutoCaptionJobError(
                "cloud_render_failed",
                _render_job_failure_reason(cloud_result) or f"CutCLI cloud render failed: {status}",
                detail={"job_id": cloud_job_id, "status": status, "result": cloud_result},
            )
        time.sleep(15)
    raise AutoCaptionJobError(
        "cloud_render_queued_timeout",
        f"CutCLI cloud render job {cloud_job_id} did not finish in {timeout_seconds}s",
        detail={
            "job_id": cloud_job_id,
            "status": _render_job_status(last_result) or "unknown",
            "result": last_result,
        },
    )


def _ass_time(us: int) -> str:
    total_cs = max(0, int(round(us / 10_000)))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_ass_text(text: Any) -> str:
    s = str(text or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\N").strip()


def _ffmpeg_filter_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _ass_font_name() -> str:
    if Path("C:/Windows/Fonts/msyh.ttc").exists() or Path("C:/Windows/Fonts/msyhbd.ttc").exists():
        return "Microsoft YaHei"
    return "Noto Sans CJK SC"


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp_int(value: float, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, int(round(value))))


def _ass_x_from_norm(value: Any, play_width: int = 1080) -> int:
    margin = max(42, int(play_width * 0.065))
    half_span = max(1, (play_width / 2) - margin)
    return _clamp_int((play_width / 2) + _float_value(value, 0.0) * half_span, margin, play_width - margin)


def _ass_y_from_norm(value: Any, play_height: int = 1920) -> int:
    margin = max(80, int(play_height * 0.085))
    half_span = max(1, (play_height / 2) - margin)
    return _clamp_int((play_height / 2) - _float_value(value, -0.62) * half_span, margin, play_height - margin)


def _ass_caption_font_size(
    cap: Dict[str, Any],
    caption_style: Dict[str, Any],
    *,
    play_width: Optional[int] = None,
) -> int:
    fs = _caption_ass_font_size_value(cap, caption_style)
    text_units = _caption_visual_units(_clean_caption_text(cap.get("text")))
    if text_units <= 0:
        return fs
    usable_width = _caption_usable_width(caption_style, video_width=play_width)
    estimated_width = text_units * _caption_font_unit_width(fs, caption_style)
    if estimated_width <= usable_width:
        return fs
    scaled = int(fs * (usable_width / max(1.0, estimated_width)) * 0.98)
    return max(44, min(fs, scaled))


def _ass_caption_override(
    cap: Dict[str, Any],
    caption_style: Dict[str, Any],
    index: int,
    *,
    play_width: int = 1080,
    play_height: int = 1920,
) -> str:
    layout = str(caption_style.get("ass_layout") or "center_burst")
    fs = _ass_caption_font_size(cap, caption_style, play_width=play_width)
    border = int(caption_style.get("ass_border") or 7)
    shadow = int(caption_style.get("ass_shadow") or 4)

    if layout == "lower_clean":
        x = _ass_x_from_norm(cap.get("transformX", caption_style.get("transform_x", 0)), play_width)
        y = _ass_y_from_norm(cap.get("transformY", caption_style.get("transform_y", -0.72)), play_height)
        return f"{{\\an2\\pos({x},{y})\\fad(220,160)\\blur0.18\\fs{fs}\\bord{border}\\shad{shadow}\\fsp1}}"

    if layout == "side_neon":
        norm_x = _float_value(cap.get("transformX", -0.50))
        left_side = norm_x <= 0
        anchor = 7 if left_side else 9
        x = int(play_width * (0.09 if left_side else 0.91))
        y = _ass_y_from_norm(cap.get("transformY", 0.18 if left_side else 0.12), play_height)
        slide = max(36, int(play_width * 0.067))
        start_x = x - slide if left_side else x + slide
        return (
            f"{{\\an{anchor}\\move({start_x},{y},{x},{y},0,240)\\fad(70,130)"
            f"\\blur0.45\\fs{fs}\\bord{border}\\shad{shadow}\\fsp2"
            f"\\t(0,260,\\fscx108\\fscy108)\\t(260,520,\\fscx100\\fscy100)}}"
        )

    if layout == "dramatic_hook":
        x = _ass_x_from_norm(cap.get("transformX", 0), play_width)
        y = _ass_y_from_norm(cap.get("transformY", -0.30 if index % 2 == 0 else -0.42), play_height)
        angle = -2 if index % 2 == 0 else 2
        return (
            f"{{\\an5\\pos({x},{y})\\fad(35,75)\\blur0.2\\fs{fs}\\bord{border}\\shad{shadow}"
            f"\\frz{angle}\\t(0,130,\\fscx132\\fscy132)\\t(130,290,\\fscx96\\fscy96)"
            f"\\t(290,470,\\fscx106\\fscy106)}}"
        )

    x = _ass_x_from_norm(cap.get("transformX", 0), play_width)
    y = _ass_y_from_norm(cap.get("transformY", -0.56 if index % 2 == 0 else -0.64), play_height)
    return (
        f"{{\\an2\\pos({x},{y})\\fad(55,100)\\blur0.28\\fs{fs}\\bord{border}\\shad{shadow}"
        f"\\t(0,150,\\fscx122\\fscy122)\\t(150,320,\\fscx98\\fscy98)"
        f"\\t(320,520,\\fscx104\\fscy104)}}"
    )


def _ass_typewriter_override(
    cap: Dict[str, Any],
    caption_style: Dict[str, Any],
    *,
    play_width: int,
    play_height: int,
) -> str:
    fs = _ass_caption_font_size(cap, caption_style, play_width=play_width)
    border = int(caption_style.get("ass_border") or 7)
    shadow = int(caption_style.get("ass_shadow") or 4)
    norm_x = _float_value(cap.get("transformX", caption_style.get("transform_x", -0.50)))
    left_side = norm_x <= 0
    anchor = 7 if left_side else 9
    x = int(play_width * (0.09 if left_side else 0.91))
    y = _ass_y_from_norm(cap.get("transformY", caption_style.get("transform_y", 0.18)), play_height)
    return f"{{\\an{anchor}\\pos({x},{y})\\blur0.28\\fs{fs}\\bord{border}\\shad{shadow}\\fsp2}}"


def _ass_typewriter_dialogues(
    *,
    start_us: int,
    end_us: int,
    text: str,
    effect: str,
    style_name: str,
    interval_ms: int,
    min_hold_ms: int,
    cursor: str,
) -> List[str]:
    units: List[str] = []
    idx = 0
    while idx < len(text):
        if text.startswith("\\N", idx):
            units.append("\\N")
            idx += 2
            continue
        units.append(text[idx])
        idx += 1
    units = [unit for unit in units if unit]
    if len(units) <= 1 or end_us <= start_us + 160_000:
        return [f"Dialogue: 0,{_ass_time(start_us)},{_ass_time(end_us)},{style_name},,0,0,0,,{effect}{text}"]
    duration_us = max(180_000, end_us - start_us)
    hold_us = min(max(120_000, int(min_hold_ms or 420) * 1000), max(120_000, duration_us // 2))
    typing_window_us = max(120_000, duration_us - hold_us)
    interval_us = min(max(35_000, int(interval_ms or 85) * 1000), max(35_000, typing_window_us // len(units)))
    lines: List[str] = []
    for unit_idx in range(1, len(units)):
        seg_start = start_us + (unit_idx - 1) * interval_us
        seg_end = min(end_us, start_us + unit_idx * interval_us)
        if seg_start >= end_us or seg_end <= seg_start:
            break
        visible = "".join(units[:unit_idx]) + cursor
        lines.append(f"Dialogue: 0,{_ass_time(seg_start)},{_ass_time(seg_end)},{style_name},,0,0,0,,{effect}{visible}")
    final_start = min(end_us - 80_000, start_us + max(0, len(units) - 1) * interval_us)
    final_start = max(start_us, final_start)
    lines.append(f"Dialogue: 0,{_ass_time(final_start)},{_ass_time(end_us)},{style_name},,0,0,0,,{effect}{text}")
    return lines


def _write_pop_caption_ass(
    job_dir: Path,
    captions: List[Dict[str, Any]],
    *,
    caption_style: Dict[str, Any],
    play_width: int = 1080,
    play_height: int = 1920,
) -> Path:
    ass_path = job_dir / "fallback_captions.ass"
    ass_font_size = int(caption_style.get("ass_font_size") or 86)
    ass_primary = str(caption_style.get("ass_primary") or "&H0000F7FF")
    ass_outline = str(caption_style.get("ass_outline") or "&H00FF3C00")
    ass_shadow = int(caption_style.get("ass_shadow") or 4)
    ass_border = int(caption_style.get("ass_border") or 7)
    ass_alignment = int(caption_style.get("ass_alignment") or 2)
    ass_margin_v = int(caption_style.get("ass_margin_v") or 265)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_width}",
        f"PlayResY: {play_height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: PopCaption,{_ass_font_name()},{ass_font_size},{ass_primary},&H00FFFFFF,{ass_outline},&H90000000,-1,0,0,0,100,100,0,0,1,{ass_border},{ass_shadow},{ass_alignment},90,90,{ass_margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    fallback_effect = str(caption_style.get("ass_effect") or "")
    is_typewriter = str(caption_style.get("caption_motion") or "") == "typewriter"
    for idx, cap in enumerate(captions):
        text = _escape_ass_text(cap.get("text"))
        if not text:
            continue
        start = int(cap.get("start") or 0)
        end = int(cap.get("end") or start + 600_000)
        if is_typewriter:
            effect = fallback_effect or _ass_typewriter_override(
                cap,
                caption_style,
                play_width=play_width,
                play_height=play_height,
            )
            lines.extend(
                _ass_typewriter_dialogues(
                    start_us=start,
                    end_us=end,
                    text=text,
                    effect=effect,
                    style_name="PopCaption",
                    interval_ms=int(caption_style.get("typing_interval_ms") or 85),
                    min_hold_ms=int(caption_style.get("typing_min_hold_ms") or 420),
                    cursor=str(caption_style.get("typing_cursor") or ""),
                )
            )
            continue
        effect = fallback_effect or _ass_caption_override(
            cap,
            caption_style,
            idx,
            play_width=play_width,
            play_height=play_height,
        )
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},PopCaption,,0,0,0,,{effect}{text}")
    ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ass_path


def _render_fallback_caption_video(
    *,
    ffmpeg: str,
    job_dir: Path,
    source: str,
    captions: List[Dict[str, Any]],
    job_id: str,
    caption_style: Dict[str, Any],
    source_info: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[int], List[str]]:
    warnings: List[str] = ["cutcli_cloud_render_queued_timeout", "fallback_renderer_ffmpeg_ass"]
    source_info = source_info or {}
    play_width = int(source_info.get("width") or 1080)
    play_height = int(source_info.get("height") or 1920)
    ass_path = _write_pop_caption_ass(
        job_dir,
        captions,
        caption_style=caption_style,
        play_width=play_width,
        play_height=play_height,
    )
    output_path = job_dir / "fallback_render.mp4"
    _run_cmd(
        [
            ffmpeg,
            "-y",
            "-i",
            source,
            "-vf",
            f"ass='{_ffmpeg_filter_path(ass_path)}'",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        timeout=900,
    )
    if not output_path.exists() or output_path.stat().st_size <= 1024:
        raise AutoCaptionJobError("fallback_render_failed", "ffmpeg fallback output is empty")
    final_url = _upload_job_file_to_tos(
        output_path,
        object_key=f"assets/cutcli_auto_caption/{job_id}/fallback_final.mp4",
        content_type="video/mp4",
    )
    return final_url, output_path.stat().st_size, warnings


@router.post("/api/cutcli/stt/transcribe", summary="Transcribe an uploaded audio URL with server STT token")
def cutcli_stt_transcribe(
    body: CutcliSttTranscribeBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    audio_url = _clean_public_http_url(body.audio_url)
    if not audio_url:
        raise HTTPException(status_code=400, detail="audio_url must be an http(s) URL")
    job_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
    job_dir = _JOBS_DIR / "stt" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        token, token_source = _load_sutui_token_for_stt(db, current_user.id)
        created = _stt_create_task(token, audio_url, job_dir=job_dir)
        task_id = created["task_id"]
        stt_data = _stt_poll_task(token, task_id, job_dir=job_dir)
        return {
            "ok": True,
            "job_id": job_id,
            "task_id": task_id,
            "audio_url": audio_url,
            "stt_model": _STT_MODEL,
            "token_source": token_source,
            "token_masked": _mask_token(token),
            "stt_data": stt_data,
        }
    except AutoCaptionJobError as exc:
        raise HTTPException(status_code=500, detail={"code": exc.code, "message": exc.message, "detail": exc.detail}) from exc
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


@router.post("/api/cutcli/cloud/render-draft", summary="Render an already-built CutCLI draft zip with server token")
def cutcli_cloud_render_draft(
    body: CutcliCloudRenderDraftBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft_id = str(body.draft_id or "").strip()
    draft_zip_url = _clean_public_http_url(body.draft_zip_url)
    if not draft_id:
        raise HTTPException(status_code=400, detail="draft_id is required")
    if not draft_zip_url:
        raise HTTPException(status_code=400, detail="draft_zip_url must be an http(s) URL")
    job_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
    job_dir = _JOBS_DIR / "cloud_render" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    zip_path = job_dir / "draft.zip"
    try:
        try:
            with httpx.Client(timeout=300.0, follow_redirects=True, trust_env=False) as client:
                response = client.get(draft_zip_url)
            if response.status_code >= 400:
                raise AutoCaptionJobError(
                    "draft_zip_download_failed",
                    f"draft zip download failed: HTTP {response.status_code}",
                    {"draft_zip_url": draft_zip_url},
                )
            zip_path.write_bytes(response.content)
            if zip_path.stat().st_size <= 0:
                raise AutoCaptionJobError(
                    "draft_zip_empty",
                    "downloaded draft zip is empty",
                    {"draft_zip_url": draft_zip_url},
                )
        except AutoCaptionJobError:
            raise
        except Exception as exc:
            raise AutoCaptionJobError(
                "draft_zip_download_failed",
                f"draft zip download failed: {str(exc)[:500]}",
                {"draft_zip_url": draft_zip_url},
            ) from exc
        token, token_source = _load_sutui_token_for_stt(db, current_user.id)
        cutcli = _find_cutcli_bin()
        cloud_result = _render_cutcli_cloud(
            cutcli,
            draft_id,
            job_dir=job_dir,
            api_key=token,
            zip_path=zip_path,
            timeout_seconds=max(60, min(int(body.timeout_seconds or 1800), 3600)),
        )
        preview_url = _extract_first_video_url(cloud_result)
        if not preview_url:
            raise AutoCaptionJobError("render_url_missing", "CutCLI cloud render did not return a video URL")
        final_url = preview_url
        file_size: Optional[int] = None
        warnings: List[str] = []
        if body.mirror_to_tos:
            final_url, file_size, warnings = _mirror_video_url_to_tos(preview_url, job_id=job_id)
        return {
            "ok": True,
            "job_id": job_id,
            "draft_id": draft_id,
            "draft_zip_url": draft_zip_url,
            "cloud_job_id": _extract_job_id(cloud_result),
            "preview_url": final_url,
            "open_url": final_url,
            "raw_preview_url": preview_url,
            "file_size": file_size,
            "warnings": warnings,
            "token_source": token_source,
            "token_masked": _mask_token(token),
            "cloud_result": cloud_result,
        }
    except AutoCaptionJobError as exc:
        raise HTTPException(status_code=500, detail={"code": exc.code, "message": exc.message, "detail": exc.detail}) from exc
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


@router.get("/api/cutcli/templates", summary="CutCLI template list")
def list_cutcli_templates(
    _: User = Depends(get_current_user),
):
    templates: List[Dict[str, Any]] = []
    for item in _TEMPLATES.values():
        row = dict(item)
        preview_url = _template_preview_url(row["id"])
        caption_style = _caption_style_for_template(row)
        row["preview_url"] = preview_url
        row["sample_video_url"] = preview_url
        row["sample_asset_id"] = _template_sample_asset_id(row["id"])
        row["render_path"] = f"/api/cutcli/templates/{row['id']}/render"
        row["render_modes"] = ["ffmpeg", "cutcli_cloud"]
        row.setdefault("input_modes", ["upload", "asset_id", "video_url"])
        row.setdefault("preserve_source_video", True)
        row.setdefault("overlay_fields", _overlay_fields())
        row["caption_style"] = caption_style
        row["generation_strategy"] = {
            "version": 1,
            "executor": "online",
            "stt": {
                "provider": "sutui",
                "model": _STT_MODEL,
                "server_endpoint": "/api/cutcli/stt/transcribe",
                "input": "audio_url",
            },
            "render_modes": row["render_modes"],
            "cloud_render_endpoint": "/api/cutcli/cloud/render-draft",
            "caption_style": caption_style,
            "overlay_fields": row.get("overlay_fields") or [],
            "preserve_source_video": True,
        }
        templates.append(TemplateListItem(**row).model_dump())
    return {"ok": True, "templates": templates}


@router.get("/api/cutcli/templates/jobs", summary="CutCLI 妯℃澘鐢熸垚璁板綍")
def list_cutcli_template_jobs(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        max_items = max(1, min(int(limit or 50), 100))
    except Exception:
        max_items = 50
    rows = (
        db.query(CreativeGenerationJob)
        .filter(
            CreativeGenerationJob.user_id == current_user.id,
            CreativeGenerationJob.feature_type == _CUTCLI_TEMPLATE_JOB_FEATURE,
            CreativeGenerationJob.deleted_at.is_(None),
        )
        .order_by(CreativeGenerationJob.updated_at.desc(), CreativeGenerationJob.created_at.desc())
        .limit(max_items)
        .all()
    )
    return {"ok": True, "jobs": [_job_row_to_public(row) for row in rows]}


@router.get("/api/cutcli/templates/jobs/{job_id}", summary="CutCLI template job detail")
def get_cutcli_template_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    jid = (job_id or "").strip()
    if not re.match(r"^\d{14}_[0-9a-f]{8}$", jid):
        raise HTTPException(status_code=400, detail="invalid job_id")
    row = _get_cutcli_job_row(db, jid)
    if not row or int(row.user_id) != int(current_user.id):
        raise HTTPException(status_code=404, detail="job not found")
    return _job_row_to_public(row)


@router.post("/api/cutcli/templates/render", summary="Render a CutCLI template preview")
async def render_cutcli_template(
    request: Request,
    template_id: str = Form(_AUTO_CAPTION_TEMPLATE_ID),
    title: str = Form(""),
    subtitle: str = Form(""),
    duration_seconds: int = Form(0),
    asset_id: str = Form(""),
    video_url: str = Form(""),
    audio_url: str = Form(""),
    file: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tpl = _TEMPLATES.get((template_id or "").strip())
    if not tpl:
        raise HTTPException(status_code=404, detail="template not found")
    clean_audio_url = _clean_public_http_url(audio_url)
    if (audio_url or "").strip() and not clean_audio_url:
        raise HTTPException(status_code=400, detail="audio_url must be an http(s) URL")
    job_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
    job_dir = _JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    ffprobe = _find_ffprobe_bin()
    source, source_asset_id, source_name = await _resolve_source_video(
        file=file,
        asset_id=asset_id,
        video_url=video_url,
        user_id=current_user.id,
        db=db,
        job_dir=job_dir,
    )
    source_info = _probe_video(ffprobe, source)

    if tpl.get("kind") == "auto_caption":
        caption_style = _caption_style_for_template(tpl)
        quality_policy = {
            "expected_caption_tracks": 1,
            "background_enabled": False,
            "text_effect": caption_style.get("text_effect") or "",
            "text_effect_id": caption_style.get("text_effect_id") or "",
            "caption_style": _caption_style_public(caption_style),
            "stt_model": _STT_MODEL,
        }
        row = _create_cutcli_job(
            db,
            job_id=job_id,
            user_id=current_user.id,
            template=tpl,
            source_asset_id=source_asset_id,
            source_name=source_name,
            source_info=source_info,
            quality_policy=quality_policy,
            audio_url=clean_audio_url,
        )
        asyncio.create_task(
            asyncio.to_thread(
                _run_auto_caption_job_sync,
                job_id=job_id,
                user_id=current_user.id,
                template_id=tpl["id"],
                source=source,
                source_asset_id=source_asset_id,
                source_name=source_name,
                source_info=source_info,
                audio_url=clean_audio_url,
            )
        )
        payload = _job_row_to_public(row)
        payload["preserve_source_video"] = bool(tpl.get("preserve_source_video", True))
        return payload

    cutcli = _find_cutcli_bin()
    duration = max(4, min(int(duration_seconds or tpl.get("default_duration") or 8), 20))
    overlay = job_dir / "overlay.png"
    _make_overlay_png(overlay, title=title, subtitle=subtitle, duration=duration)

    draft_id, draft_info, warnings = _build_cutcli_draft(
        cutcli=cutcli,
        job_dir=job_dir,
        source=source,
        source_info=source_info,
        overlay=overlay,
        duration=duration,
        title=title,
        subtitle=subtitle,
    )

    legacy_token, legacy_token_source = _load_sutui_token_for_stt(db, current_user.id)
    cloud_result = _render_cutcli_cloud(cutcli, draft_id, job_dir=job_dir, api_key=legacy_token)
    preview_url = _extract_first_video_url(cloud_result)
    if not preview_url:
        raise HTTPException(status_code=500, detail="CutCLI cloud render did not return a usable video URL")
    cloud_job_id = _extract_job_id(cloud_result)

    out_asset_id = uuid.uuid4().hex[:12]
    asset = Asset(
        asset_id=out_asset_id,
        user_id=current_user.id,
        filename=f"cutcli_template_{job_id}.mp4",
        media_type="video",
        file_size=None,
        source_url=preview_url,
        prompt=f"{tpl['name']} | {title} | {subtitle}",
        model="cutcli:brand_cinematic_opener",
        tags="cutcli_template,template_video,brand_cinematic_opener",
        meta={
            "cutcli_template_id": tpl["id"],
            "cutcli_template_name": tpl["name"],
            "cutcli_job_id": job_id,
            "cutcli_draft_id": draft_id,
            "cutcli_cloud_job_id": cloud_job_id,
            "source_asset_id": source_asset_id,
            "source_name": source_name,
            "preview_renderer": "cutcli_cloud",
            "sutui_token_source": legacy_token_source,
            "sutui_token_masked": _mask_token(legacy_token),
            "created_at": int(time.time()),
        },
    )
    db.add(asset)
    db.commit()
    local_workspace_cleanup = _cleanup_auto_caption_workspace(job_id)

    return {
        "ok": True,
        "job_id": job_id,
        "template": tpl,
        "draft_id": draft_id,
        "cloud_job_id": cloud_job_id,
        "draft_info": draft_info,
        "source_asset_id": source_asset_id,
        "preview_asset_id": out_asset_id,
        "preview_url": preview_url,
        "open_url": preview_url,
        "warnings": warnings,
        "local_workspace_cleanup": local_workspace_cleanup,
    }


@router.post("/api/cutcli/templates/{template_id}/render", summary="鎸夋ā鏉?ID 濂楃敤 CutCLI 瑙嗛妯℃澘")
async def render_cutcli_template_by_id(
    request: Request,
    template_id: str,
    asset_id: str = Form(""),
    video_url: str = Form(""),
    audio_url: str = Form(""),
    file: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await render_cutcli_template(
        request=request,
        template_id=template_id,
        title="",
        subtitle="",
        duration_seconds=0,
        asset_id=asset_id,
        video_url=video_url,
        audio_url=audio_url,
        file=file,
        current_user=current_user,
        db=db,
    )
