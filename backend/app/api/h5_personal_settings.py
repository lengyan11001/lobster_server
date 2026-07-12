from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from ..models import H5AgentMemoryGrant, OpenClawMemoryDocument, User
from .auth import get_current_user
from .installation_slots import ensure_installation_slot, optional_installation_id_from_request
from .mobile_identity import online_user_for_mobile_user
from .openclaw_memory_cloud import (
    _MAX_UPLOAD_BYTES,
    _decode_text_payload,
    _doc_id_for,
    _doc_summary,
    _limit_text,
    _sanitize_doc_id,
    _short_title,
)

router = APIRouter()

DOC_TYPE_LABELS: dict[str, str] = {
    "brand_product_intro": "产品介绍",
    "product_service_faq": "百问百答",
    "short_video_scripts": "短视频口播稿",
    "custom_memory": "自定义参考文档",
}
PRESET_DOC_TYPES = {"brand_product_intro", "product_service_faq", "short_video_scripts"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_VIDEO_BYTES = 80 * 1024 * 1024
MAX_VISUAL_BLOCKS = 8
URL_RE = re.compile(r"https?://[^\s,，]+", re.I)


class RawMemorySaveBody(BaseModel):
    title: str = ""
    notes: str = ""
    content: str = Field(default="", max_length=900_000)
    mode: str = "new"
    target_doc_id: str = ""


class GeneratedMemorySaveBody(BaseModel):
    title: str = ""
    notes: str = ""
    documents: dict[str, str] = Field(default_factory=dict)


def _internal_api_base() -> str:
    raw = (os.environ.get("LOBSTER_INTERNAL_API_BASE") or "").strip().rstrip("/")
    if raw:
        return raw
    return f"http://127.0.0.1:{int(getattr(settings, 'port', 8000) or 8000)}"


def _llm_model() -> str:
    return (
        (os.environ.get("LOBSTER_ORCHESTRATION_SUTUI_CHAT_MODEL") or "").strip()
        or (os.environ.get("LOBSTER_DEFAULT_SUTUI_CHAT_MODEL") or "").strip()
        or (getattr(settings, "lobster_orchestration_sutui_chat_model", None) or "").strip()
        or (getattr(settings, "lobster_default_sutui_chat_model", None) or "").strip()
        or "openai/gpt-4.1-mini"
    )


def _owner_user(db: Session, current_user: User) -> User:
    return online_user_for_mobile_user(db, current_user)


def _installation_id(request: Request) -> str:
    iid = optional_installation_id_from_request(request)
    if not iid:
        raise HTTPException(status_code=400, detail="请先选择在线设备。")
    return iid


def _doc_query(db: Session, user_id: int, installation_id: str):
    return db.query(OpenClawMemoryDocument).filter(
        OpenClawMemoryDocument.target_user_id == user_id,
        OpenClawMemoryDocument.installation_id == installation_id,
        OpenClawMemoryDocument.status == "active",
    )


def _memory_row(db: Session, user_id: int, installation_id: str, doc_id: str) -> OpenClawMemoryDocument:
    clean = _sanitize_doc_id(doc_id)
    if not clean:
        raise HTTPException(status_code=404, detail="记忆文件不存在。")
    row = _doc_query(db, user_id, installation_id).filter(OpenClawMemoryDocument.doc_id == clean).first()
    if not row:
        raise HTTPException(status_code=404, detail="记忆文件不存在。")
    return row


def _agent_granted_memory_rows(db: Session, target_user: User, doc_ids: Optional[list[str]] = None) -> list[OpenClawMemoryDocument]:
    parent_id = int(getattr(target_user, "parent_user_id", 0) or 0)
    if not parent_id:
        return []
    parent = db.query(User).filter(User.id == parent_id, User.is_agent == True).first()  # noqa: E712
    if not parent:
        return []
    grant_query = db.query(H5AgentMemoryGrant).filter(
        H5AgentMemoryGrant.owner_user_id == parent.id,
        H5AgentMemoryGrant.target_user_id == target_user.id,
        H5AgentMemoryGrant.status == "active",
    )
    if doc_ids:
        grant_query = grant_query.filter(H5AgentMemoryGrant.memory_doc_id.in_(doc_ids))
    granted_ids = [str(row.memory_doc_id or "") for row in grant_query.all() if str(row.memory_doc_id or "").strip()]
    if not granted_ids:
        return []
    return (
        db.query(OpenClawMemoryDocument)
        .filter(
            OpenClawMemoryDocument.target_user_id == parent.id,
            OpenClawMemoryDocument.doc_id.in_(granted_ids),
            OpenClawMemoryDocument.status == "active",
        )
        .order_by(OpenClawMemoryDocument.updated_at.desc(), OpenClawMemoryDocument.id.desc())
        .all()
    )


def _memory_summary(row: OpenClawMemoryDocument, *, include_content: bool, source: str) -> dict[str, Any]:
    data = _doc_summary(row, include_content=include_content)
    data["source"] = source
    data["read_only"] = source != "own"
    if source != "own":
        data["memory_layer"] = "agent"
    return data


def _accessible_memory_row(
    db: Session,
    target_user: User,
    installation_id: str,
    doc_id: str,
) -> tuple[OpenClawMemoryDocument, str]:
    clean = _sanitize_doc_id(doc_id)
    if not clean:
        raise HTTPException(status_code=404, detail="记忆文件不存在。")
    own = _doc_query(db, target_user.id, installation_id).filter(OpenClawMemoryDocument.doc_id == clean).first()
    if own:
        return own, "own"
    for row in _agent_granted_memory_rows(db, target_user, [clean]):
        if row.doc_id == clean:
            return row, "agent"
    raise HTTPException(status_code=404, detail="记忆文件不存在。")


def _authorization_headers(request: Request, installation_id: str) -> dict[str, str]:
    headers: dict[str, str] = {"X-Installation-Id": installation_id}
    auth = (request.headers.get("authorization") or request.headers.get("Authorization") or "").strip()
    if auth:
        headers["Authorization"] = auth
    return headers


def _response_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                return "\n".join(parts).strip()
        text = choices[0].get("text") if isinstance(choices[0], dict) else ""
        if isinstance(text, str):
            return text.strip()
    for key in ("content", "text", "message"):
        val = data.get(key)
        if isinstance(val, str):
            return val.strip()
    return ""


async def _call_llm(
    request: Request,
    installation_id: str,
    messages: list[dict[str, Any]],
    *,
    timeout_seconds: float = 240.0,
) -> str:
    payload = {
        "model": _llm_model(),
        "messages": messages,
        "temperature": 0.25,
        "stream": False,
    }
    timeout = httpx.Timeout(timeout_seconds, connect=15.0, read=timeout_seconds, write=30.0, pool=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.post(
                f"{_internal_api_base()}/api/sutui-chat/completions",
                json=payload,
                headers=_authorization_headers(request, installation_id),
            )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise HTTPException(status_code=502, detail=f"AI 理解失败：{exc}") from exc
    data: Any
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {"text": resp.text[:20000]}
    if resp.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else None
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("error") or json.dumps(detail, ensure_ascii=False)
        raise HTTPException(status_code=resp.status_code, detail=str(detail or "AI 理解失败"))
    text = _response_text(data)
    if not text:
        raise HTTPException(status_code=502, detail="AI 理解没有返回内容。")
    return text


def _doc_type_list(raw: str, doc_type: str, has_custom_reference: bool) -> list[str]:
    items: list[str] = []
    raw = (raw or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                items.extend(str(x).strip() for x in parsed)
        except Exception:
            items.extend(x.strip() for x in raw.split(","))
    if doc_type:
        items.append(doc_type.strip())
    out: list[str] = []
    for item in items:
        if item in DOC_TYPE_LABELS and item not in out:
            out.append(item)
    if not out and has_custom_reference:
        out.append("custom_memory")
    if not out:
        raise HTTPException(status_code=400, detail="请选择生成类型，或上传自定义参考文档。")
    return out[:4]


async def _read_upload(file: UploadFile) -> tuple[str, str, bytes]:
    filename = os.path.basename(file.filename or "upload")
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    data = await file.read()
    limit = MAX_VIDEO_BYTES if suffix in VIDEO_SUFFIXES else _MAX_UPLOAD_BYTES
    if len(data) > limit:
        limit_mb = max(1, limit // (1024 * 1024))
        raise HTTPException(status_code=413, detail=f"{filename} 超过 {limit_mb}MB。")
    return filename, suffix, data


def _media_text(filename: str, suffix: str, data: bytes) -> str:
    kind = "图片" if suffix in IMAGE_SUFFIXES else "视频"
    size_kb = max(1, len(data) // 1024)
    return f"{kind}文件：{filename}，大小 {size_kb}KB。"


def _ffmpeg_path() -> str:
    base = Path(__file__).resolve().parent.parent.parent.parent
    candidates = [
        base / "deps" / "ffmpeg" / "ffmpeg.exe",
        base / "skills" / "comfly_veo3_daihuo_video" / "tools" / "ffmpeg" / "windows" / "ffmpeg.exe",
    ]
    for item in candidates:
        if item.is_file():
            return str(item)
    return "ffmpeg"


def _extract_video_frames(data: bytes, filename: str, max_frames: int = 3) -> list[tuple[str, bytes]]:
    suffix = Path(filename or "").suffix.lower() or ".mp4"
    frames: list[tuple[str, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="lobster-h5-video-") as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / ("source" + suffix)
        src.write_bytes(data)
        out_pattern = tmp_dir / "frame_%02d.jpg"
        cmd = [
            _ffmpeg_path(),
            "-y",
            "-i",
            str(src),
            "-vf",
            "fps=1/5,scale='min(1280,iw)':-2",
            "-frames:v",
            str(max_frames),
            "-q:v",
            "4",
            str(out_pattern),
        ]
        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=45,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        except Exception:
            return []
        for frame in sorted(tmp_dir.glob("frame_*.jpg"))[:max_frames]:
            try:
                frames.append((frame.name, frame.read_bytes()))
            except OSError:
                continue
    return frames


def _split_urls(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in URL_RE.finditer(text or ""):
        url = match.group(0).strip().rstrip("。；;，,)")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= 20:
            break
    return out


def _looks_like_image_url(url: str) -> bool:
    clean = (url or "").split("?", 1)[0].split("#", 1)[0].lower()
    return any(clean.endswith(suffix) for suffix in IMAGE_SUFFIXES)


def _looks_like_video_url(url: str) -> bool:
    clean = (url or "").split("?", 1)[0].split("#", 1)[0].lower()
    return any(clean.endswith(suffix) for suffix in VIDEO_SUFFIXES)


async def _download_media_url(url: str, *, max_bytes: int) -> bytes:
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True, trust_env=False) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("media too large")
                chunks.append(chunk)
    return b"".join(chunks)


def _file_to_text(filename: str, suffix: str, data: bytes) -> str:
    if suffix in IMAGE_SUFFIXES or suffix in VIDEO_SUFFIXES:
        return _media_text(filename, suffix, data)
    return _decode_text_payload(data, filename)


def _data_url(filename: str, data: bytes) -> str:
    content_type = mimetypes.guess_type(filename)[0] or "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _append_visual_block(visual_blocks: list[dict[str, Any]], filename: str, data: bytes) -> bool:
    if len(visual_blocks) >= MAX_VISUAL_BLOCKS or len(data) > MAX_IMAGE_BYTES:
        return False
    visual_blocks.append({"type": "image_url", "image_url": {"url": _data_url(filename, data)}})
    return True


async def _collect_sources(
    request: Request,
    installation_id: str,
    files: Optional[list[UploadFile]],
    raw_text: str,
    urls: str,
) -> tuple[str, list[dict[str, Any]]]:
    parts: list[str] = []
    visual_blocks: list[dict[str, Any]] = []
    if raw_text.strip():
        parts.append("【粘贴资料】\n" + raw_text.strip())
    if urls.strip():
        parts.append("【链接资料】\n" + urls.strip())
        for url in _split_urls(urls):
            if len(visual_blocks) >= MAX_VISUAL_BLOCKS:
                break
            if _looks_like_image_url(url):
                try:
                    data = await _download_media_url(url, max_bytes=MAX_IMAGE_BYTES)
                    if _append_visual_block(visual_blocks, Path(url).name or "image-url", data):
                        parts.append(f"【图片链接】{url}")
                except Exception as exc:
                    parts.append(f"【图片链接】{url}\n下载或理解失败：{exc}")
            elif _looks_like_video_url(url):
                try:
                    data = await _download_media_url(url, max_bytes=MAX_VIDEO_BYTES)
                    frames = _extract_video_frames(data, Path(url).name or "video-url.mp4", max_frames=3)
                    if frames:
                        parts.append(f"【视频链接】{url}\n已抽取 {len(frames)} 张关键帧用于理解。")
                        for frame_name, frame_data in frames:
                            _append_visual_block(visual_blocks, frame_name, frame_data)
                    else:
                        parts.append(f"【视频链接】{url}\n关键帧抽取失败。")
                except Exception as exc:
                    parts.append(f"【视频链接】{url}\n下载或抽帧失败：{exc}")
    for file in files or []:
        if not file or not file.filename:
            continue
        filename, suffix, data = await _read_upload(file)
        if suffix in IMAGE_SUFFIXES and _append_visual_block(visual_blocks, filename, data):
            parts.append(f"【图片】{filename}")
            continue
        if suffix in VIDEO_SUFFIXES:
            frames = _extract_video_frames(data, filename, max_frames=3) if len(data) <= MAX_VIDEO_BYTES else []
            if frames:
                parts.append(f"【视频】{filename}\n已抽取 {len(frames)} 张关键帧用于理解。")
                for frame_name, frame_data in frames:
                    _append_visual_block(visual_blocks, f"{filename}-{frame_name}", frame_data)
            else:
                parts.append(_media_text(filename, suffix, data) + "\n关键帧抽取失败。")
            continue
        text = _file_to_text(filename, suffix, data)
        parts.append(f"【文件：{filename}】\n{text}")
    merged = _limit_text("\n\n".join(part for part in parts if part.strip()))
    if not merged and not visual_blocks:
        raise HTTPException(status_code=400, detail="请上传资料、填写链接或粘贴资料内容。")
    return merged, visual_blocks


def _limit_local(text: str, max_chars: int = 120_000) -> str:
    text = str(text or "")
    return text[:max_chars]


async def _read_reference(custom_reference_file: Optional[UploadFile]) -> str:
    if not custom_reference_file or not custom_reference_file.filename:
        return ""
    filename, suffix, data = await _read_upload(custom_reference_file)
    if suffix in IMAGE_SUFFIXES or suffix in VIDEO_SUFFIXES:
        raise HTTPException(status_code=400, detail="自定义参考文档请上传可解析的文档文件。")
    return _limit_local(_decode_text_payload(data, filename))


def _generation_prompt(source_text: str, doc_types: list[str], reference_text: str) -> str:
    labels = [DOC_TYPE_LABELS.get(key, key) for key in doc_types]
    markers = "\n".join(f"<<<{key}>>>" for key in doc_types)
    type_rules = {
        "brand_product_intro": "产品介绍：提炼定位、目标客户、产品/服务模块、优势、交付流程、可复用表达。",
        "product_service_faq": "百问百答：输出问答对，问题具体，回答可直接给客服或内容技能复用。",
        "short_video_scripts": "短视频口播稿：输出多条口播素材，包含标题、适用场景、逐字稿。",
        "custom_memory": "自定义参考文档：学习参考文档的栏目结构、颗粒度和表达方式，按同类结构生成。",
    }
    rules = "\n".join(f"- {type_rules.get(key, DOC_TYPE_LABELS.get(key, key))}" for key in doc_types)
    reference_block = f"\n\n【自定义参考文档】\n{reference_text}" if reference_text else ""
    return f"""你要把用户上传的资料整理成可长期复用的个人记忆文档。

要求：
- 不要编造没有依据的公司名、品牌名、人名。
- 不要加入导流反问、销售口号或无依据承诺。
- 根据资料原文提炼，信息不足的位置留空或写成可替换字段。
- 输出必须按下面 marker 分段，每个 marker 后只写该文档正文，不要输出 JSON。

生成类型：{", ".join(labels)}
分段 marker：
{markers}

生成规则：
{rules}

【业务资料】
{source_text or "资料包含图片，请结合图片内容理解。"}{reference_block}
"""


def _parse_generated(text: str, doc_types: list[str]) -> dict[str, str]:
    documents: dict[str, str] = {}
    pattern = re.compile(r"^<<<([a-z0-9_]+)>>>\s*(.*?)(?=^<<<[a-z0-9_]+>>>\s*|\Z)", re.S | re.M)
    for key, body in pattern.findall(text or ""):
        if key in doc_types:
            cleaned = body.strip()
            if cleaned:
                documents[key] = cleaned
    if not documents:
        if len(doc_types) == 1:
            documents[doc_types[0]] = text.strip()
        else:
            for key in doc_types:
                marker = f"<<<{key}>>>"
                if marker in text:
                    documents[key] = text.split(marker, 1)[-1].strip()
    return {k: v for k, v in documents.items() if v}


def _document_title(base: str, doc_type: str, multi: bool) -> str:
    label = DOC_TYPE_LABELS.get(doc_type, "记忆")
    title = _short_title(base, label)
    return f"{title}-{label}" if multi and label not in title else title


def _create_document(
    db: Session,
    *,
    target_user: User,
    uploader_user: User,
    installation_id: str,
    title: str,
    filename: str,
    notes: str,
    content_text: str,
    meta: dict[str, Any],
) -> OpenClawMemoryDocument:
    now = datetime.utcnow()
    content = _limit_text(content_text or "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="没有可保存的记忆内容。")
    sha = hashlib.sha256(content.encode("utf-8", "ignore")).hexdigest()
    row = OpenClawMemoryDocument(
        doc_id=_doc_id_for(target_user.id, installation_id, filename, sha, now.isoformat(), scope="h5_personal_settings"),
        target_user_id=target_user.id,
        installation_id=installation_id,
        origin="user",
        uploader_user_id=uploader_user.id,
        uploader_role="user",
        title=_short_title(title, filename),
        filename=filename,
        notes=notes or "",
        content_text=content,
        size=len(content.encode("utf-8", "ignore")),
        sha256=sha,
        status="active",
        meta={**(meta or {}), "source": "h5_personal_settings"},
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _overwrite_document(
    db: Session,
    row: OpenClawMemoryDocument,
    *,
    content_text: str,
    notes: str,
    meta: dict[str, Any],
) -> OpenClawMemoryDocument:
    content = _limit_text(content_text or "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="没有可保存的记忆内容。")
    row.content_text = content
    row.notes = notes or row.notes or ""
    row.size = len(content.encode("utf-8", "ignore"))
    row.sha256 = hashlib.sha256(content.encode("utf-8", "ignore")).hexdigest()
    row.meta = {**(row.meta or {}), **(meta or {}), "source": "h5_personal_settings"}
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


@router.get("/api/personal-settings/memory-documents/list")
async def list_memory_documents(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    installation_id = _installation_id(request)
    target_user = _owner_user(db, current_user)
    ensure_installation_slot(db, target_user.id, installation_id)
    rows = _doc_query(db, target_user.id, installation_id).order_by(OpenClawMemoryDocument.updated_at.desc()).limit(200).all()
    agent_rows = _agent_granted_memory_rows(db, target_user)
    documents = [_memory_summary(row, include_content=True, source="own") for row in rows]
    documents.extend(_memory_summary(row, include_content=True, source="agent") for row in agent_rows)
    documents.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return {"ok": True, "documents": documents[:300]}


@router.get("/api/personal-settings/memory-documents/{doc_id}/preview")
async def preview_memory_document(
    doc_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    installation_id = _installation_id(request)
    target_user = _owner_user(db, current_user)
    row, source = _accessible_memory_row(db, target_user, installation_id, doc_id)
    data = _memory_summary(row, include_content=True, source=source)
    return {"ok": True, "document": data, "content_text": data.get("content_text") or ""}


@router.delete("/api/personal-settings/memory-documents/{doc_id}")
async def delete_memory_document(
    doc_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    installation_id = _installation_id(request)
    target_user = _owner_user(db, current_user)
    row = _memory_row(db, target_user.id, installation_id, doc_id)
    row.status = "deleted"
    row.deleted_at = datetime.utcnow()
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/api/personal-settings/memory-documents/save-raw")
async def save_raw_memory_document(
    body: RawMemorySaveBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    installation_id = _installation_id(request)
    target_user = _owner_user(db, current_user)
    ensure_installation_slot(db, target_user.id, installation_id)
    mode = (body.mode or "new").strip().lower()
    if mode == "overwrite":
        row = _memory_row(db, target_user.id, installation_id, body.target_doc_id)
        row = _overwrite_document(
            db,
            row,
            content_text=body.content,
            notes=body.notes or "个人设置保存",
            meta={"save_mode": "overwrite"},
        )
    else:
        title = _short_title(body.title, "个人记忆")
        row = _create_document(
            db,
            target_user=target_user,
            uploader_user=current_user,
            installation_id=installation_id,
            title=title,
            filename=f"{title}.txt",
            notes=body.notes or "个人设置保存",
            content_text=body.content,
            meta={"save_mode": "new"},
        )
    return {"ok": True, "document": _doc_summary(row, include_content=True), "documents": [_doc_summary(row, include_content=True)]}


@router.post("/api/personal-settings/memory-documents/save")
async def save_generated_memory_documents(
    body: GeneratedMemorySaveBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    installation_id = _installation_id(request)
    target_user = _owner_user(db, current_user)
    ensure_installation_slot(db, target_user.id, installation_id)
    docs = {k: str(v or "").strip() for k, v in (body.documents or {}).items() if str(v or "").strip()}
    if not docs:
        raise HTTPException(status_code=400, detail="没有可保存的记忆内容。")
    created: list[OpenClawMemoryDocument] = []
    multi = len(docs) > 1
    for key, text in docs.items():
        title = _document_title(body.title, key, multi)
        row = _create_document(
            db,
            target_user=target_user,
            uploader_user=current_user,
            installation_id=installation_id,
            title=title,
            filename=f"{title}.txt",
            notes=body.notes or "个人设置 AI 理解",
            content_text=text,
            meta={"doc_type": key, "doc_type_label": DOC_TYPE_LABELS.get(key, key)},
        )
        created.append(row)
    return {"ok": True, "documents": [_doc_summary(row, include_content=True) for row in created]}


@router.post("/api/personal-settings/memory-documents/save-upload")
async def save_uploaded_memory_document(
    request: Request,
    files: list[UploadFile] = File(default=[]),
    title: str = Form(default=""),
    notes: str = Form(default=""),
    raw_text: str = Form(default=""),
    urls: str = Form(default=""),
    mode: str = Form(default="new"),
    target_doc_id: str = Form(default=""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    installation_id = _installation_id(request)
    target_user = _owner_user(db, current_user)
    ensure_installation_slot(db, target_user.id, installation_id)
    source_text, _visual_blocks = await _collect_sources(request, installation_id, files, raw_text, urls)
    mode = (mode or "new").strip().lower()
    if mode == "overwrite":
        row = _memory_row(db, target_user.id, installation_id, target_doc_id)
        row = _overwrite_document(db, row, content_text=source_text, notes=notes or "个人设置上传资料", meta={"save_mode": "overwrite"})
    else:
        clean_title = _short_title(title, "个人记忆")
        row = _create_document(
            db,
            target_user=target_user,
            uploader_user=current_user,
            installation_id=installation_id,
            title=clean_title,
            filename=f"{clean_title}.txt",
            notes=notes or "个人设置上传资料",
            content_text=source_text,
            meta={"save_mode": "new"},
        )
    return {
        "ok": True,
        "content_text": source_text,
        "document": _doc_summary(row, include_content=True),
        "documents": [_doc_summary(row, include_content=True)],
    }


@router.post("/api/personal-settings/memory-documents/generate")
async def generate_memory_documents(
    request: Request,
    files: list[UploadFile] = File(default=[]),
    urls: str = Form(default=""),
    direct_intro: str = Form(default=""),
    direct_faq: str = Form(default=""),
    direct_scripts: str = Form(default=""),
    doc_type: str = Form(default=""),
    doc_types: str = Form(default=""),
    custom_reference_file: Optional[UploadFile] = File(default=None),
    reference_doc_ids: str = Form(default=""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    installation_id = _installation_id(request)
    target_user = _owner_user(db, current_user)
    ensure_installation_slot(db, target_user.id, installation_id)
    reference_text = await _read_reference(custom_reference_file)
    selected_doc_types = _doc_type_list(doc_types, doc_type, bool(reference_text))

    raw_parts = [direct_intro.strip(), direct_faq.strip(), direct_scripts.strip()]
    raw_text = "\n\n".join(part for part in raw_parts if part)
    source_text, visual_blocks = await _collect_sources(request, installation_id, files, raw_text, urls)

    ref_ids = [_sanitize_doc_id(x) for x in (reference_doc_ids or "").split(",") if _sanitize_doc_id(x)]
    if ref_ids:
        rows = (
            _doc_query(db, target_user.id, installation_id)
            .filter(OpenClawMemoryDocument.doc_id.in_(ref_ids[:8]))
            .order_by(OpenClawMemoryDocument.updated_at.desc())
            .all()
        )
        own_ids = {row.doc_id for row in rows}
        rows.extend(row for row in _agent_granted_memory_rows(db, target_user, ref_ids[:8]) if row.doc_id not in own_ids)
        if rows:
            reference_text = _limit_local(
                reference_text
                + "\n\n"
                + "\n\n".join(f"【参考记忆：{row.title}】\n{row.content_text}" for row in rows),
            )

    prompt = _generation_prompt(source_text, selected_doc_types, reference_text)
    content: Any = [{"type": "text", "text": prompt}]
    content.extend(visual_blocks)
    messages = [
        {"role": "system", "content": "你是企业资料整理助手，只根据用户给的资料整理记忆文档。"},
        {"role": "user", "content": content},
    ]
    text = await _call_llm(request, installation_id, messages)
    documents = _parse_generated(text, selected_doc_types)
    if not documents:
        raise HTTPException(status_code=502, detail="AI 理解没有生成可保存内容。")
    return {"ok": True, "documents": documents, "doc_types": selected_doc_types, "raw_text": text}
