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
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import Asset, User
from .assets import ASSETS_DIR, _upload_to_tos
from .auth import brand_mark_for_jwt_claim, get_current_user
from ..core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_ROOT_DIR = Path(__file__).resolve().parents[3]
_JOBS_DIR = _ROOT_DIR / "data" / "cutcli_templates"
_JOBS_DIR.mkdir(parents=True, exist_ok=True)
_PREVIEW_CATALOG_FILE = _JOBS_DIR / "template_previews.json"
_STATIC_PREVIEW_DIR = _ROOT_DIR / "static" / "media" / "cutcli_templates"
_STATIC_PREVIEW_PUBLIC_PREFIX = "/static/media/cutcli_templates"
_AUTO_CAPTION_TEMPLATE_ID = "auto_caption_pop_huazi_v1"
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
_STT_TERMINAL_SUCCESS = {"completed", "complete", "success", "succeeded", "finished", "done"}
_STT_TERMINAL_FAILURE = {"failed", "error", "cancelled", "canceled", "timeout", "rejected"}
_STT_RUNNING = {"pending", "queued", "running", "processing", "waiting", "created"}
_SUTUI_TOKEN_POOL_LOCK = threading.Lock()
_SUTUI_TOKEN_POOL_INDEX: Dict[str, int] = {}

_TEMPLATES: Dict[str, Dict[str, Any]] = {
    _AUTO_CAPTION_TEMPLATE_ID: {
        "id": _AUTO_CAPTION_TEMPLATE_ID,
        "name": "爆款花字自动字幕",
        "description": "用户上传视频，服务器用火山录音文件识别 2.0 转写，并生成单轨黄字蓝边花字字幕，完成后自动入库。",
        "aspect_ratio": "9:16",
        "default_duration": 30,
        "tags": ["auto_caption", "subtitle", "huazi", "server"],
    },
    "brand_cinematic_opener": {
        "id": "brand_cinematic_opener",
        "name": "高级品牌快剪",
        "description": "把一条视频包装成 9:16 品牌片头风格，叠加高级暗色遮罩、金色线条、标题字幕和轻电影调色。",
        "aspect_ratio": "9:16",
        "default_duration": 8,
        "tags": ["brand", "cinematic", "video"],
    }
}


class TemplateListItem(BaseModel):
    id: str
    name: str
    description: str
    aspect_ratio: str = "9:16"
    default_duration: int = 8
    tags: List[str] = []
    preview_url: str = ""
    sample_video_url: str = ""
    sample_asset_id: str = ""


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
        detail="服务端未找到 cutcli，请先安装 CutCLI 或设置 CUTCLI_BIN。",
    )


def _load_preview_catalog() -> Dict[str, Any]:
    if not _PREVIEW_CATALOG_FILE.exists():
        return {}
    try:
        data = json.loads(_PREVIEW_CATALOG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("[CutCLI模板] failed to read preview catalog: %s", exc)
        return {}


def _template_preview_url(template_id: str) -> str:
    catalog = _load_preview_catalog()
    item = catalog.get(template_id)
    if isinstance(item, dict):
        url = str(item.get("preview_url") or item.get("sample_video_url") or "").strip()
        if url:
            return url
    elif isinstance(item, str) and item.strip():
        return item.strip()

    for ext in (".mp4", ".mov", ".webm"):
        sample = _STATIC_PREVIEW_DIR / f"{template_id}{ext}"
        if sample.exists():
            return f"{_STATIC_PREVIEW_PUBLIC_PREFIX}/{sample.name}"
    return ""


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
        raise HTTPException(status_code=500, detail="服务端未找到 ffprobe，无法读取视频信息。")
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
    logger.info("[CutCLI模板] run: %s", " ".join(str(x) for x in args[:4]))
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
        raise RuntimeError(f"命令返回不是 JSON: {out[:500]}") from exc
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
            raise HTTPException(status_code=400, detail="上传的视频文件为空。")
        ext = _safe_ext(file.filename)
        src_path = job_dir / f"source{ext}"
        src_path.write_bytes(data)
        return str(src_path), None, file.filename

    aid = (asset_id or "").strip()
    if aid:
        row = db.query(Asset).filter(Asset.asset_id == aid, Asset.user_id == user_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="没有找到这个素材 ID。")
        if (row.media_type or "").lower() != "video":
            raise HTTPException(status_code=400, detail="这个素材不是视频，请选择视频素材。")
        local = _asset_local_path(row)
        if local:
            return str(local), row.asset_id, row.filename
        url = (row.source_url or "").strip()
        if url.startswith(("http://", "https://")):
            return url, row.asset_id, row.filename
        raise HTTPException(status_code=400, detail="这个素材没有可用的视频文件或公网地址。")

    url = (video_url or "").strip()
    if url.startswith(("http://", "https://")):
        return url, None, Path(url.split("?")[0]).name or "remote-video.mp4"

    raise HTTPException(status_code=400, detail="请上传视频、选择素材库视频，或填写视频素材 ID。")


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
        raise HTTPException(status_code=400, detail="无法读取视频信息，请确认视频文件可播放。") from exc
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
        raise HTTPException(status_code=500, detail="服务端缺少 Pillow，无法生成模板包装层。") from exc

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

    title = (title or "品牌高光时刻").strip()[:28]
    subtitle = (subtitle or "用一条视频生成高级宣传片").strip()[:46]
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


def _job_manifest_path(job_id: str) -> Path:
    return _JOBS_DIR / job_id / "manifest.json"


def _read_job_manifest(job_id: str) -> Dict[str, Any]:
    path = _job_manifest_path(job_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_job_manifest(job_id: str, data: Dict[str, Any]) -> None:
    job_dir = _JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = int(time.time())
    _job_manifest_path(job_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _update_job_manifest(job_id: str, **fields: Any) -> Dict[str, Any]:
    cur = _read_job_manifest(job_id)
    cur.update(fields)
    _write_job_manifest(job_id, cur)
    return cur


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
        code = "stt_balance_insufficient" if "余额不足" in msg or "balance" in msg.lower() else "stt_create_failed"
        raise AutoCaptionJobError(code, msg, detail=payload)
    if isinstance(payload, dict) and payload.get("code") not in (None, 200, "200"):
        msg = _safe_error_text(payload)
        code = "stt_balance_insufficient" if "余额不足" in msg or "balance" in msg.lower() else "stt_create_failed"
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
                code = "stt_balance_insufficient" if "余额不足" in msg or "balance" in msg.lower() else "stt_query_failed"
                raise AutoCaptionJobError(code, msg, detail=last_payload)
            time.sleep(2.5)
    raise AutoCaptionJobError("stt_timeout", f"STT task timed out: {task_id}", detail=last_payload)


_EDGE_PUNCT = " \t\r\n,.;:!?，。！？；：、\"'“”‘’（）()[]【】<>《》"


def _clean_caption_text(text: Any) -> str:
    s = str(text or "").replace("\u3000", " ").strip()
    s = re.sub(r"\s+", " ", s)
    s = s.strip(_EDGE_PUNCT)
    return s


def _caption_display_len(text: str) -> int:
    total = 0
    for ch in text:
        if ch.isspace():
            continue
        total += 1
    return total


def _split_caption_text(text: str, max_chars: int = 11) -> List[str]:
    tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]|[^\s]", text or "")
    chunks: List[str] = []
    cur = ""
    for tok in tokens:
        candidate = (cur + (" " if cur and re.match(r"^[A-Za-z0-9]+$", tok) else "") + tok).strip()
        if cur and _caption_display_len(candidate) > max_chars:
            chunks.append(_clean_caption_text(cur))
            cur = tok
        else:
            cur = candidate
    if cur:
        chunks.append(_clean_caption_text(cur))
    return [x for x in chunks if x]


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


def _captions_from_stt(stt_data: Dict[str, Any], *, video_duration_sec: float) -> List[Dict[str, Any]]:
    output = _extract_stt_output(stt_data)
    utterances = output.get("utterances") if isinstance(output, dict) else None
    captions: List[Dict[str, Any]] = []
    video_end_us = max(100_000, int(max(video_duration_sec, 0.1) * 1_000_000))

    words: List[Dict[str, Any]] = []
    if isinstance(utterances, list):
        for utt in utterances:
            if not isinstance(utt, dict):
                continue
            for item in utt.get("words") or []:
                if not isinstance(item, dict):
                    continue
                text = _clean_caption_text(item.get("text"))
                if not text:
                    continue
                try:
                    start_ms = int(float(item.get("start_time")))
                    end_ms = int(float(item.get("end_time")))
                except Exception:
                    continue
                if start_ms < 0 or end_ms <= start_ms:
                    continue
                words.append({"text": text, "start_ms": start_ms, "end_ms": end_ms})
    words.sort(key=lambda x: (x["start_ms"], x["end_ms"]))

    def add_caption(text: str, start_ms: int, end_ms: int) -> None:
        clean = _clean_caption_text(text)
        if not clean:
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
        captions.append(
            {
                "text": clean,
                "start": start_us,
                "end": min(video_end_us, end_us),
                "inAnimation": _CAPTION_IN_ANIMATION,
                "inAnimationDuration": 280_000,
                "loopAnimation": _CAPTION_LOOP_ANIMATION,
                "loopAnimationDuration": 500_000,
            }
        )

    if words:
        cur_words: List[str] = []
        cur_start = words[0]["start_ms"]
        cur_end = words[0]["end_ms"]
        for word in words:
            candidate = _clean_caption_text("".join(cur_words) + word["text"])
            dur_ms = int(word["end_ms"] - cur_start)
            if cur_words and (_caption_display_len(candidate) > 11 or dur_ms > 2300):
                add_caption("".join(cur_words), cur_start, cur_end)
                cur_words = [word["text"]]
                cur_start = word["start_ms"]
            else:
                cur_words.append(word["text"])
            cur_end = word["end_ms"]
        if cur_words:
            add_caption("".join(cur_words), cur_start, cur_end)
    elif isinstance(utterances, list):
        for utt in utterances:
            if not isinstance(utt, dict):
                continue
            text = _clean_caption_text(utt.get("text"))
            if not text:
                continue
            try:
                start_ms = int(float(utt.get("start_time") or 0))
                end_ms = int(float(utt.get("end_time") or start_ms + 1200))
            except Exception:
                start_ms, end_ms = 0, 1200
            chunks = _split_caption_text(text, max_chars=11)
            if not chunks:
                continue
            span = max(700, end_ms - start_ms)
            step = max(500, int(span / len(chunks)))
            for idx, chunk in enumerate(chunks):
                add_caption(chunk, start_ms + idx * step, start_ms + (idx + 1) * step)
    else:
        text = _clean_caption_text(output.get("text") if isinstance(output, dict) else "")
        chunks = _split_caption_text(text, max_chars=11)
        if chunks:
            step = max(900, int((video_end_us / 1000) / len(chunks)))
            for idx, chunk in enumerate(chunks):
                add_caption(chunk, idx * step, (idx + 1) * step)

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


def _validate_caption_quality(captions: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    duplicate_adjacent = 0
    overlap_count = 0
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
        "expected_caption_tracks": 1,
        "background_enabled": False,
        "text_effect": _CAPTION_HUAZI_NAME,
        "text_effect_id": _CAPTION_HUAZI_EFFECT_ID,
        "stt_model": _STT_MODEL,
    }
    return quality, errors, sorted(set(warnings))


def _build_auto_caption_cutcli_draft(
    *,
    cutcli: str,
    job_dir: Path,
    source: str,
    source_info: Dict[str, Any],
    captions: List[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any], Dict[str, Any], List[str]]:
    warnings: List[str] = []
    draft_name = "lobster_caption_" + job_dir.name
    created = _json_from_cmd(
        [cutcli, "draft", "create", "--name", draft_name, "--width", "1080", "--height", "1920", "--pretty"],
        timeout=60,
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
    _run_cmd([cutcli, "videos", "add", draft_id, "--video-infos", video_json], timeout=180)

    captions_json = _write_json(job_dir / "cutcli_auto_captions.json", captions)
    _run_cmd(
        [
            cutcli,
            "captions",
            "add",
            draft_id,
            "--captions",
            captions_json,
            "--font-size",
            "13",
            "--bold",
            "--alignment",
            "0",
            "--text-color",
            "#FFFFFF",
            "--border-color",
            "#061A48",
            "--border-width",
            "0.08",
            "--has-shadow",
            "--shadow-color",
            "#000000",
            "--text-effect",
            _CAPTION_HUAZI_NAME,
            "--transform-y",
            "-0.66",
        ],
        timeout=180,
    )

    captions_list: Any = []
    try:
        captions_list = _json_value_from_cmd([cutcli, "captions", "list", draft_id], timeout=90)
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

    info = _json_from_cmd([cutcli, "draft", "info", draft_id, "--pretty"], timeout=60)
    (job_dir / "draft_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    quality = {
        "actual_caption_count": actual_captions,
        "actual_caption_tracks": actual_tracks,
        "background_enabled": False,
        "text_effect": _CAPTION_HUAZI_NAME,
        "text_effect_id": _CAPTION_HUAZI_EFFECT_ID,
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
        prompt=f"auto caption template | {source_name}",
        model=f"cutcli:{_AUTO_CAPTION_TEMPLATE_ID}",
        tags="cutcli_template,auto_caption,huazi,server_render",
        meta={
            "cutcli_template_id": _AUTO_CAPTION_TEMPLATE_ID,
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
    source: str,
    source_asset_id: Optional[str],
    source_name: str,
    source_info: Dict[str, Any],
) -> None:
    job_dir = _JOBS_DIR / job_id
    warnings: List[str] = []
    db = SessionLocal()
    try:
        _update_job_manifest(job_id, status="running", stage="extract_audio")
        ffmpeg = _find_ffmpeg_bin()
        cutcli = _find_cutcli_bin()
        audio_path = job_dir / "audio.wav"
        _extract_audio_wav(ffmpeg=ffmpeg, source=source, out_path=audio_path)

        _update_job_manifest(job_id, stage="upload_audio")
        audio_url = _upload_job_file_to_tos(
            audio_path,
            object_key=f"assets/cutcli_auto_caption/{job_id}/audio.wav",
            content_type="audio/wav",
        )
        _update_job_manifest(job_id, stage="stt_create", audio_url=audio_url, stt_model=_STT_MODEL)

        token, token_source = _load_sutui_token_for_stt(db, user_id)
        token_masked = _mask_token(token)
        _update_job_manifest(
            job_id,
            token_source=token_source,
            token_masked=token_masked,
        )
        stt_created = _stt_create_task(token, audio_url, job_dir=job_dir)
        task_id = stt_created["task_id"]
        _update_job_manifest(job_id, stage="stt_poll", stt_task_id=task_id)

        stt_data = _stt_poll_task(token, task_id, job_dir=job_dir)
        captions = _captions_from_stt(stt_data, video_duration_sec=float(source_info.get("duration") or 0.1))
        (job_dir / "generated_captions.json").write_text(
            json.dumps(captions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        quality, quality_errors, quality_warnings = _validate_caption_quality(captions)
        warnings.extend(quality_warnings)
        if quality_errors:
            raise AutoCaptionJobError("caption_quality_failed", ",".join(sorted(set(quality_errors))))
        _update_job_manifest(job_id, stage="build_draft", quality=quality, caption_count=len(captions))

        draft_id, draft_info, draft_quality, draft_warnings = _build_auto_caption_cutcli_draft(
            cutcli=cutcli,
            job_dir=job_dir,
            source=source,
            source_info=source_info,
            captions=captions,
        )
        quality.update(draft_quality)
        warnings.extend(draft_warnings)

        _update_job_manifest(job_id, stage="cloud_render", draft_id=draft_id, quality=quality, warnings=warnings)
        cloud_result: Dict[str, Any] = {}
        cloud_job_id: Optional[str] = None
        try:
            cloud_result = _render_cutcli_cloud(cutcli, draft_id, job_dir=job_dir, api_key=token, timeout_seconds=300)
            preview_url = _extract_first_video_url(cloud_result)
            if not preview_url:
                raise AutoCaptionJobError("render_url_missing", "CutCLI cloud render did not return a video URL")
            cloud_job_id = _extract_job_id(cloud_result)

            _update_job_manifest(job_id, stage="mirror_result", cloud_job_id=cloud_job_id, preview_url=preview_url)
            final_url, file_size, mirror_warnings = _mirror_video_url_to_tos(preview_url, job_id=job_id)
            warnings.extend(mirror_warnings)
            render_strategy = "cutcli_cloud"
        except AutoCaptionJobError as exc:
            if exc.code != "cloud_render_queued_timeout":
                raise
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            cloud_job_id = str(detail.get("job_id") or "") or _extract_job_id(detail) or None
            warnings.append(exc.code)
            _update_job_manifest(
                job_id,
                stage="fallback_render",
                cloud_job_id=cloud_job_id,
                cloud_render_detail=detail,
                warnings=warnings,
            )
            final_url, file_size, fallback_warnings = _render_fallback_caption_video(
                ffmpeg=ffmpeg,
                job_dir=job_dir,
                source=source,
                captions=captions,
                job_id=job_id,
            )
            warnings.extend(fallback_warnings)
            render_strategy = "ffmpeg_ass_fallback"

        asset_id = _save_auto_caption_asset(
            db=db,
            user_id=user_id,
            job_id=job_id,
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
        _update_job_manifest(
            job_id,
            status="completed",
            stage="completed",
            error=None,
            draft_id=draft_id,
            cloud_job_id=cloud_job_id,
            preview_url=final_url,
            open_url=final_url,
            preview_asset_id=asset_id,
            final_asset_id=asset_id,
            source_asset_id=source_asset_id,
            source_name=source_name,
            quality=quality,
            warnings=warnings,
            token_source=token_source,
            token_masked=token_masked,
            render_strategy=render_strategy,
            draft_info=draft_info,
        )
    except AutoCaptionJobError as exc:
        logger.warning("[cutcli-auto-caption] job failed job_id=%s code=%s msg=%s", job_id, exc.code, exc.message)
        _update_job_manifest(
            job_id,
            status="failed",
            stage="failed",
            error_code=exc.code,
            error=exc.message,
            error_detail=exc.detail,
            warnings=warnings,
        )
    except Exception as exc:
        logger.exception("[cutcli-auto-caption] job failed job_id=%s", job_id)
        _update_job_manifest(
            job_id,
            status="failed",
            stage="failed",
            error_code="auto_caption_failed",
            error=str(exc)[:2000],
            warnings=warnings,
        )
    finally:
        db.close()


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
    draft_name = "lobster_tpl_" + job_dir.name
    created = _json_from_cmd(
        [cutcli, "draft", "create", "--name", draft_name, "--width", "1080", "--height", "1920", "--pretty"],
        timeout=60,
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
    _run_cmd([cutcli, "videos", "add", draft_id, "--video-infos", video_json], timeout=120)

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
            _run_cmd([cutcli, "images", "add", draft_id, "--image-infos", overlay_json], timeout=120)
        except Exception as exc:
            warnings.append(f"overlay: {str(exc)[:180]}")
            logger.warning("[CutCLI模板] overlay add failed: %s", exc)

    title_json = _write_json(
        job_dir / "cutcli_title.json",
        [{"text": (title or "品牌高光时刻")[:28], "start": 200_000, "end": min(duration_us, 2_800_000)}],
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
                    [{"effectTitle": "胶片漏光", "start": 0, "end": min(duration_us, 2_500_000)}],
                ),
            ],
        ),
    ]
    for label, cmd in optional_steps:
        try:
            _run_cmd(cmd, timeout=90)
        except Exception as exc:
            warnings.append(f"{label}: {str(exc)[:180]}")
            logger.warning("[CutCLI模板] optional %s failed: %s", label, exc)

    info = _json_from_cmd([cutcli, "draft", "info", draft_id, "--pretty"], timeout=60)
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
    timeout_seconds: int = 1800,
) -> Dict[str, Any]:
    if not (api_key or "").strip():
        raise AutoCaptionJobError("cutcli_token_missing", "server sutui token is not configured for CutCLI render")
    env = _cutcli_cloud_env(api_key)
    try:
        _ensure_cutcli_uses_token(cutcli, api_key=api_key, job_dir=job_dir, env=env)
    except AutoCaptionJobError:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"服务端 CutCLI 云渲染未完成登录配置，无法生成视频: {str(exc)[:500]}",
        ) from exc

    render_result = _json_from_cmd(
        [cutcli, "cloud", "render", draft_id, "--pretty"],
        timeout=300,
        env=env,
    )
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
    s = str(text or "").replace("\\", "\\\\").replace("{", "｛").replace("}", "｝")
    return s.replace("\r", " ").replace("\n", " ").strip()


def _ffmpeg_filter_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _write_pop_caption_ass(job_dir: Path, captions: List[Dict[str, Any]]) -> Path:
    ass_path = job_dir / "fallback_captions.ass"
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: PopCaption,Noto Sans CJK SC,86,&H0000F7FF,&H00FFFFFF,&H00FF3C00,&H90000000,-1,0,0,0,100,100,0,0,1,7,4,2,90,90,265,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    effect = r"{\fad(80,120)\blur0.35\t(0,220,\fscx112\fscy112)\t(220,520,\fscx100\fscy100)}"
    for cap in captions:
        text = _escape_ass_text(cap.get("text"))
        if not text:
            continue
        start = int(cap.get("start") or 0)
        end = int(cap.get("end") or start + 600_000)
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
) -> Tuple[str, Optional[int], List[str]]:
    warnings: List[str] = ["cutcli_cloud_render_queued_timeout", "fallback_renderer_ffmpeg_ass"]
    ass_path = _write_pop_caption_ass(job_dir, captions)
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


@router.get("/api/cutcli/templates", summary="CutCLI 视频模板列表")
def list_cutcli_templates(
    _: User = Depends(get_current_user),
):
    templates: List[Dict[str, Any]] = []
    for item in _TEMPLATES.values():
        row = dict(item)
        preview_url = _template_preview_url(row["id"])
        row["preview_url"] = preview_url
        row["sample_video_url"] = preview_url
        row["sample_asset_id"] = _template_sample_asset_id(row["id"])
        templates.append(TemplateListItem(**row).model_dump())
    return {"ok": True, "templates": templates}


@router.get("/api/cutcli/templates/jobs/{job_id}", summary="CutCLI 模板任务状态")
def get_cutcli_template_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    jid = (job_id or "").strip()
    if not re.match(r"^\d{14}_[0-9a-f]{8}$", jid):
        raise HTTPException(status_code=400, detail="invalid job_id")
    job = _read_job_manifest(jid)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if int(job.get("user_id") or 0) != int(current_user.id):
        raise HTTPException(status_code=404, detail="job not found")
    return {"ok": True, **job}


@router.post("/api/cutcli/templates/render", summary="套用 CutCLI 视频模板并生成预览")
async def render_cutcli_template(
    request: Request,
    template_id: str = Form(_AUTO_CAPTION_TEMPLATE_ID),
    title: str = Form("品牌高光时刻"),
    subtitle: str = Form("用一条视频生成高级宣传片"),
    duration_seconds: int = Form(8),
    asset_id: str = Form(""),
    video_url: str = Form(""),
    file: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tpl = _TEMPLATES.get((template_id or "").strip())
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在。")
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

    if tpl["id"] == _AUTO_CAPTION_TEMPLATE_ID:
        quality_policy = {
            "expected_caption_tracks": 1,
            "background_enabled": False,
            "text_effect": _CAPTION_HUAZI_NAME,
            "text_effect_id": _CAPTION_HUAZI_EFFECT_ID,
            "stt_model": _STT_MODEL,
        }
        _write_job_manifest(
            job_id,
            {
                "ok": True,
                "async": True,
                "job_id": job_id,
                "status": "running",
                "stage": "queued",
                "user_id": current_user.id,
                "template_id": tpl["id"],
                "template": tpl,
                "source_asset_id": source_asset_id,
                "source_name": source_name,
                "source_info": source_info,
                "stt_model": _STT_MODEL,
                "quality_policy": quality_policy,
                "created_at": int(time.time()),
            },
        )
        asyncio.create_task(
            asyncio.to_thread(
                _run_auto_caption_job_sync,
                job_id=job_id,
                user_id=current_user.id,
                source=source,
                source_asset_id=source_asset_id,
                source_name=source_name,
                source_info=source_info,
            )
        )
        return {
            "ok": True,
            "async": True,
            "job_id": job_id,
            "status": "running",
            "stage": "queued",
            "poll_path": f"/api/cutcli/templates/jobs/{job_id}",
            "template": tpl,
            "source_asset_id": source_asset_id,
            "source_name": source_name,
            "stt_model": _STT_MODEL,
            "quality_policy": quality_policy,
        }

    cutcli = _find_cutcli_bin()
    duration = max(4, min(int(duration_seconds or tpl["default_duration"]), 20))
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
        raise HTTPException(status_code=500, detail="CutCLI 云渲染完成但没有返回可用视频 URL。")
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
    }
