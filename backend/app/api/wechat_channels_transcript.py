from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..db import SessionLocal, get_db
from ..models import CreativeGenerationJob, User
from .auth import get_current_user
from .cutcli_templates import (
    _extract_audio_wav,
    _extract_stt_output,
    _find_ffmpeg_bin,
    _load_sutui_token_for_stt,
    _stt_create_task,
    _stt_poll_task,
    _upload_job_file_to_tos,
)
from .ip_content_studio import (
    _clean_long_text,
    _clean_text,
    _collect_items,
    _execute_query_with_retry,
    _first,
    _jsonable,
    _normalize_wechat_channels_users_from_payload,
    _public_url,
    _stable_hash,
    _utcnow,
)

router = APIRouter()

_FEATURE_TYPE = "wechat_channels_transcript"
_JOBS_DIR = Path(__file__).resolve().parents[3] / "data" / "wechat_channels_transcript"
_JOBS_DIR.mkdir(parents=True, exist_ok=True)
_MAX_VIDEOS_PER_JOB = 50
_MAX_VIDEO_BYTES = 350 * 1024 * 1024
_TERMINAL_STATUS = {"completed", "failed", "canceled"}


class VideoFetchBody(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    max_pages: int = Field(default=5, ge=1, le=20)
    page_size: int = Field(default=20, ge=1, le=50)


class TranscriptJobBody(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    videos: list[dict[str, Any]] = Field(default_factory=list, max_length=_MAX_VIDEOS_PER_JOB)


def _job_payload(row: CreativeGenerationJob) -> dict[str, Any]:
    meta = dict(row.meta or {})
    return {
        "job_id": row.job_id,
        "status": row.status,
        "stage": row.stage or "",
        "progress": int(row.progress or 0),
        "title": row.title or "",
        "request_payload": row.request_payload or {},
        "result_payload": row.result_payload or {},
        "error": row.error or "",
        "meta": meta,
        "items": meta.get("items") if isinstance(meta.get("items"), list) else [],
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "completed_at": row.completed_at.isoformat() if row.completed_at else "",
    }


def _clean_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw.startswith(("http://", "https://")):
        return ""
    return _clean_long_text(raw, 4096)


def _append_url_token(url: str, token: Any) -> str:
    cleaned = _clean_url(url)
    token_text = str(token or "").strip()
    if not cleaned or not token_text or "token=" in cleaned:
        return cleaned
    if token_text.startswith("&"):
        suffix = token_text if "?" in cleaned else "?" + token_text.lstrip("&")
    elif token_text.startswith("?"):
        suffix = token_text if "?" not in cleaned else "&" + token_text.lstrip("?")
    elif token_text.startswith("token="):
        suffix = ("&" if "?" in cleaned else "?") + token_text
    else:
        return cleaned
    return _clean_url(cleaned + suffix)


def _pick_media_url(raw: Any) -> str:
    token_pairs = [
        ("object_desc.media.0.url", "object_desc.media.0.url_token"),
        ("objectDesc.media.0.url", "objectDesc.media.0.url_token"),
        ("objectDesc.mediaList.0.url", "objectDesc.mediaList.0.url_token"),
        ("media.0.url", "media.0.url_token"),
        ("mediaList.0.url", "mediaList.0.url_token"),
    ]
    for url_path, token_path in token_pairs:
        value = _append_url_token(_first(raw, [url_path]), _first(raw, [token_path]))
        if value:
            return value

    candidates = [
        "video_url",
        "download_url",
        "play_url",
        "media_url",
        "url",
        "object_desc.media.0.video_url",
        "object_desc.media.0.full_url",
        "object_desc.media.0.url",
        "object_desc.media.0.media_url",
        "objectDesc.media.0.video_url",
        "objectDesc.media.0.full_url",
        "objectDesc.media.0.url",
        "objectDesc.media.0.media_url",
        "objectDesc.mediaList.0.video_url",
        "objectDesc.mediaList.0.full_url",
        "objectDesc.mediaList.0.url",
        "objectDesc.mediaList.0.urlList.0",
        "objectDesc.mediaList.0.videoUrl",
        "objectDesc.mediaList.0.mediaUrl",
        "media.0.video_url",
        "media.0.full_url",
        "media.0.url",
        "mediaList.0.video_url",
        "mediaList.0.full_url",
        "mediaList.0.url",
        "mediaList.0.urlList.0",
        "mediaList.0.videoUrl",
        "mediaList.0.mediaUrl",
        "video.play_addr.url_list.0",
        "video.download_addr.url_list.0",
        "video.url",
        "video.playAddr.urlList.0",
        "video.downloadAddr.urlList.0",
        "video.urlList.0",
        "video_url.url",
        "video_url.url_list.0",
        "videoUrl.url",
        "videoUrl.urlList.0",
        "full_url",
    ]
    value = _first(raw, candidates)
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = _first(value, ["url", "full_url", "video_url", "videoUrl", "media_url", "mediaUrl", "url_list.0", "urlList.0", "download_addr.url_list.0", "play_addr.url_list.0", "downloadAddr.urlList.0", "playAddr.urlList.0"])
    return _clean_url(value)


def _extract_last_buffer(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    value = _first(
        payload,
        [
            "data.last_buffer",
            "data.lastBuffer",
            "data.object.last_buffer",
            "data.object.lastBuffer",
            "last_buffer",
            "lastBuffer",
            "next_buffer",
            "nextBuffer",
        ],
    )
    if isinstance(value, dict):
        value = _first(value, ["buffer", "last_buffer", "lastBuffer"])
    return _clean_long_text(value, 4096)


def _normalize_video(raw: Any, idx: int) -> Optional[dict[str, Any]]:
    item = raw if isinstance(raw, dict) else {"value": raw}
    title = _first(
        item,
        [
            "title",
            "desc",
            "description",
            "object_desc.description",
            "object_desc.title",
            "objectDesc.description",
            "objectDesc.title",
            "feed_desc",
            "content",
            "nickname",
        ],
    )
    publish_time = _first(item, ["publish_time", "create_time", "createtime", "timestamp", "object_desc.create_time", "objectDesc.create_time", "objectDesc.createTime"])
    video_url = _pick_media_url(item)
    public_url = _public_url(item) or video_url
    cover_url = _first(
        item,
        [
            "cover_url",
            "thumb_url",
            "thumbUrl",
            "object_desc.media.0.cover_url",
            "object_desc.media.0.thumb_url",
            "object_desc.media.0.url",
            "objectDesc.media.0.cover_url",
            "objectDesc.media.0.thumb_url",
            "objectDesc.media.0.url",
            "objectDesc.mediaList.0.cover_url",
            "objectDesc.mediaList.0.thumb_url",
            "objectDesc.mediaList.0.coverUrl",
            "objectDesc.mediaList.0.thumbUrl",
            "objectDesc.mediaList.0.url",
            "mediaList.0.cover_url",
            "mediaList.0.thumb_url",
            "mediaList.0.coverUrl",
            "mediaList.0.thumbUrl",
            "mediaList.0.url",
            "cover.url",
            "cover.url_list.0",
            "cover.urlList.0",
        ],
    )
    if isinstance(cover_url, list):
        cover_url = cover_url[0] if cover_url else ""
    if isinstance(cover_url, dict):
        cover_url = _first(cover_url, ["url", "url_list.0", "uri"])
    item_key = (
        _first(item, ["object_id", "objectId", "feed_id", "feedId", "id", "export_id", "exportId", "object_desc.object_id", "objectDesc.object_id", "objectDesc.objectId", "nonce_id", "nonceId"])
        or _stable_hash(item, 24)
    )
    if not video_url and not public_url:
        return None
    return {
        "item_key": _clean_text(item_key, 128) or f"video_{idx}",
        "title": _clean_long_text(title, 1000) or f"视频 {idx + 1}",
        "publish_time": _clean_text(publish_time, 64),
        "video_url": video_url,
        "public_url": _clean_url(public_url),
        "cover_url": _clean_url(cover_url),
        "metrics": {
            "like_count": _first(item, ["like_count", "like_cnt", "fav_count"]),
            "comment_count": _first(item, ["comment_count", "comment_cnt"]),
            "forward_count": _first(item, ["forward_count", "share_count", "share_cnt"]),
            "play_count": _first(item, ["play_count", "read_count", "view_count"]),
        },
        "raw_index": idx,
        "raw": _jsonable(item),
    }


def _normalize_videos_from_payload(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, raw in enumerate(_collect_items(payload or {})):
        item = _normalize_video(raw, idx)
        if not item:
            continue
        key = str(item.get("item_key") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _transcript_text(stt_data: dict[str, Any]) -> str:
    output = _extract_stt_output(stt_data)
    for key in ("text", "transcript", "content", "result_text"):
        value = output.get(key) if isinstance(output, dict) else None
        if isinstance(value, str) and value.strip():
            return _clean_long_text(value, 100000)
    utterances = output.get("utterances") if isinstance(output, dict) else None
    if isinstance(utterances, list):
        parts = []
        for item in utterances:
            if isinstance(item, dict):
                text = item.get("text") or item.get("words")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        if parts:
            return _clean_long_text("\n".join(parts), 100000)
    return ""


def _save_job(row: CreativeGenerationJob, *, items: Optional[list[dict[str, Any]]] = None, stage: Optional[str] = None, progress: Optional[int] = None, error: str = "") -> None:
    meta = dict(row.meta or {})
    if items is not None:
        meta["items"] = items
    row.meta = meta
    flag_modified(row, "meta")
    if stage is not None:
        row.stage = stage
    if progress is not None:
        row.progress = max(0, min(100, int(progress)))
    if error:
        row.error = error[:2000]
    row.updated_at = _utcnow()


def _item_resume_key(item: dict[str, Any]) -> str:
    return str(item.get("item_key") or _stable_hash(item.get("raw") or item, 24))


def _build_resume_items(videos: list[dict[str, Any]], existing_items: Any) -> list[dict[str, Any]]:
    completed_by_key: dict[str, dict[str, Any]] = {}
    if isinstance(existing_items, list):
        for item in existing_items:
            if not isinstance(item, dict):
                continue
            key = _item_resume_key(item)
            if key and item.get("status") == "completed" and not item.get("error"):
                completed_by_key[key] = item

    items: list[dict[str, Any]] = []
    for video in videos[:_MAX_VIDEOS_PER_JOB]:
        key = _item_resume_key(video)
        previous = completed_by_key.get(key)
        if previous:
            items.append({**video, **previous, "status": "completed", "error": previous.get("error") or ""})
        else:
            items.append({**video, "status": "pending", "transcript": "", "error": ""})
    return items


def _download_video(video_url: str, target: Path) -> None:
    with httpx.Client(timeout=300.0, follow_redirects=True, trust_env=False) as client:
        with client.stream("GET", video_url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            if resp.status_code >= 400:
                retmsg = resp.headers.get("x-retmsg") or resp.headers.get("X-retmsg") or ""
                errno = resp.headers.get("x-errno") or resp.headers.get("X-Errno") or resp.headers.get("x-videoerrno") or ""
                detail = f": {retmsg}" if retmsg else ""
                if errno:
                    detail = f"{detail} ({errno})"
                raise RuntimeError(f"video download failed HTTP {resp.status_code}{detail}")
            total = 0
            with target.open("wb") as fh:
                for chunk in resp.iter_bytes(1024 * 512):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _MAX_VIDEO_BYTES:
                        raise RuntimeError("video file is too large")
                    fh.write(chunk)
    if not target.exists() or target.stat().st_size <= 0:
        raise RuntimeError("downloaded video is empty")


def _resolve_video_url(item: dict[str, Any]) -> str:
    video_url = _clean_url(item.get("video_url")) or _clean_url(item.get("public_url"))
    if video_url and ("finder.video.qq.com" not in video_url or "token=" in video_url):
        return video_url
    raw = item.get("raw")
    if isinstance(raw, dict):
        normalized = _normalize_video(raw, int(item.get("raw_index") or 0))
        if normalized:
            candidate = _clean_url(normalized.get("video_url")) or _clean_url(normalized.get("public_url"))
            if candidate:
                item["video_url"] = candidate
                item["public_url"] = _clean_url(normalized.get("public_url")) or candidate
                return candidate
    return video_url


def _process_one_video(db: Session, row: CreativeGenerationJob, user: User, item: dict[str, Any], index: int, total: int, job_dir: Path) -> dict[str, Any]:
    video_url = _resolve_video_url(item)
    if not video_url:
        raise RuntimeError("视频没有可下载链接")
    item_dir = job_dir / f"item_{index + 1:03d}"
    item_dir.mkdir(parents=True, exist_ok=True)
    video_path = item_dir / "source.mp4"
    audio_path = item_dir / "audio.wav"
    _download_video(video_url, video_path)
    ffmpeg = _find_ffmpeg_bin()
    _extract_audio_wav(ffmpeg=ffmpeg, source=str(video_path), out_path=audio_path)
    audio_url = _upload_job_file_to_tos(
        audio_path,
        object_key=f"assets/wechat_channels_transcript/{row.job_id}/{index + 1:03d}.wav",
        content_type="audio/wav",
    )
    token, token_source = _load_sutui_token_for_stt(db, user.id)
    created = _stt_create_task(token, audio_url, job_dir=item_dir)
    stt_data = _stt_poll_task(token, created["task_id"], job_dir=item_dir)
    text = _transcript_text(stt_data)
    if not text:
        text = ""
    result = {
        **item,
        "status": "completed",
        "audio_url": audio_url,
        "stt_task_id": created["task_id"],
        "token_source": token_source,
        "transcript": text,
        "error": "",
    }
    return result


async def _run_transcript_job(job_id: str) -> None:
    await asyncio.sleep(0.1)
    with SessionLocal() as db:
        row = (
            db.query(CreativeGenerationJob)
            .filter(CreativeGenerationJob.job_id == job_id, CreativeGenerationJob.feature_type == _FEATURE_TYPE)
            .first()
        )
        if not row:
            return
        user = db.query(User).filter(User.id == row.user_id).first()
        if not user:
            row.status = "failed"
            _save_job(row, stage="failed", progress=100, error="用户不存在")
            db.commit()
            return
        request = row.request_payload or {}
        request_videos = request.get("videos") if isinstance(request.get("videos"), list) else []
        videos = []
        for idx, video in enumerate(request_videos):
            if not isinstance(video, dict):
                continue
            normalized = _normalize_video(video.get("raw") or video, idx)
            videos.append(normalized or video)
        existing_meta = row.meta or {}
        items = _build_resume_items(videos, existing_meta.get("items") if isinstance(existing_meta, dict) else [])
        job_dir = _JOBS_DIR / row.job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        row.status = "running"
        _save_job(row, items=items, stage="starting", progress=2)
        db.commit()
        for index, item in enumerate(items):
            if row.status in _TERMINAL_STATUS:
                return
            if item.get("status") == "completed":
                _save_job(row, items=items, stage=f"skip_{index + 1}_{len(items)}", progress=int((index + 1) / max(len(items), 1) * 90) + 5)
                db.commit()
                continue
            try:
                item["status"] = "running"
                _save_job(row, items=items, stage=f"processing_{index + 1}_{len(items)}", progress=int(index / max(len(items), 1) * 90) + 5)
                db.commit()
                result = await asyncio.to_thread(_process_one_video, db, row, user, item, index, len(items), job_dir)
                items[index] = result
            except Exception as exc:
                items[index] = {**item, "status": "failed", "error": str(getattr(exc, "message", "") or exc)[:2000]}
            _save_job(row, items=items, progress=int((index + 1) / max(len(items), 1) * 90) + 5)
            db.commit()
        failed = [x for x in items if x.get("status") == "failed"]
        row.status = "completed" if len(failed) < len(items) else "failed"
        row.completed_at = _utcnow()
        row.result_payload = {
            "count": len(items),
            "completed_count": len([x for x in items if x.get("status") == "completed"]),
            "failed_count": len(failed),
        }
        _save_job(row, items=items, stage=row.status, progress=100, error="全部转写失败" if row.status == "failed" else "")
        db.commit()
        shutil.rmtree(job_dir, ignore_errors=True)


@router.get("/api/wechat-channels-transcript/users/search", summary="搜索视频号账号")
async def search_users(
    q: str = Query("", min_length=1, max_length=80),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = await _execute_query_with_retry(
        db=db,
        current_user=current_user,
        query_type="wechat_channels_user_search_v2",
        params={"keywords": q.strip(), "page": 1},
        body={},
        save_items=False,
        meta={"source": "wechat_channels_transcript_user_search", "keyword": q.strip()},
        attempts=3,
        include_raw_response=True,
    )
    users, raw_count = _normalize_wechat_channels_users_from_payload(result.get("raw_response") or {})
    return {"ok": True, "items": users, "raw_count": raw_count, "query": result.get("query") or {}}


@router.post("/api/wechat-channels-transcript/videos", summary="拉取视频号作品")
async def fetch_videos(
    body: VideoFetchBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    username = body.username.strip()
    videos: list[dict[str, Any]] = []
    seen: set[str] = set()
    last_buffer = ""
    queries: list[dict[str, Any]] = []
    for page in range(max(1, int(body.max_pages or 1))):
        req_body = {"username": username}
        if last_buffer:
            req_body["last_buffer"] = last_buffer
        result = await _execute_query_with_retry(
            db=db,
            current_user=current_user,
            query_type="wechat_channels_home_page",
            params={},
            body=req_body,
            save_items=False,
            meta={"source": "wechat_channels_transcript_videos", "username": username, "page": page + 1},
            attempts=3,
            include_raw_response=True,
        )
        queries.append(result.get("query") or {})
        page_videos = _normalize_videos_from_payload(result.get("raw_response") or {})
        for item in page_videos:
            key = str(item.get("item_key") or "")
            if key in seen:
                continue
            seen.add(key)
            videos.append(item)
            if len(videos) >= int(body.max_pages or 5) * int(body.page_size or 20):
                break
        next_buffer = _extract_last_buffer(result.get("raw_response") or {})
        if not next_buffer or next_buffer == last_buffer:
            break
        last_buffer = next_buffer
        if len(videos) >= int(body.max_pages or 5) * int(body.page_size or 20):
            break
    return {"ok": True, "items": videos, "count": len(videos), "queries": queries, "last_buffer": last_buffer}


@router.post("/api/wechat-channels-transcript/jobs", summary="创建视频号作品批量转写任务")
async def create_transcript_job(
    body: TranscriptJobBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    videos = []
    for raw in body.videos[:_MAX_VIDEOS_PER_JOB]:
        item = _normalize_video(raw.get("raw") or raw, len(videos)) if isinstance(raw, dict) else None
        if not item and isinstance(raw, dict):
            item = {
                "item_key": _clean_text(raw.get("item_key") or _stable_hash(raw, 24), 128),
                "title": _clean_long_text(raw.get("title"), 1000) or "未命名视频",
                "publish_time": _clean_text(raw.get("publish_time"), 64),
                "video_url": _clean_url(raw.get("video_url")),
                "public_url": _clean_url(raw.get("public_url")),
                "cover_url": _clean_url(raw.get("cover_url")),
                "metrics": raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {},
                "raw": _jsonable(raw.get("raw") or raw),
            }
        if item and (_clean_url(item.get("video_url")) or _clean_url(item.get("public_url"))):
            videos.append(item)
    if not videos:
        raise HTTPException(status_code=400, detail="请选择至少一个包含视频链接的作品")
    job_id = "wct_" + uuid.uuid4().hex[:24]
    row = CreativeGenerationJob(
        job_id=job_id,
        user_id=current_user.id,
        feature_type=_FEATURE_TYPE,
        provider="tikhub+stt",
        status="queued",
        stage="queued",
        progress=0,
        title=f"视频号文案提取：{body.username.strip()}",
        prompt=body.username.strip(),
        request_payload={"username": body.username.strip(), "videos": videos},
        result_payload={},
        meta={"items": [{**v, "status": "pending", "transcript": "", "error": ""} for v in videos]},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    asyncio.create_task(_run_transcript_job(job_id))
    return {"ok": True, "job": _job_payload(row)}


@router.get("/api/wechat-channels-transcript/jobs", summary="视频号文案提取任务列表")
def list_jobs(
    limit: int = Query(30, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(CreativeGenerationJob)
        .filter(
            CreativeGenerationJob.user_id == current_user.id,
            CreativeGenerationJob.feature_type == _FEATURE_TYPE,
            CreativeGenerationJob.deleted_at.is_(None),
        )
        .order_by(CreativeGenerationJob.created_at.desc(), CreativeGenerationJob.id.desc())
        .limit(limit)
        .all()
    )
    return {"ok": True, "items": [_job_payload(row) for row in rows]}


@router.get("/api/wechat-channels-transcript/jobs/{job_id}", summary="视频号文案提取任务详情")
def get_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(CreativeGenerationJob)
        .filter(
            CreativeGenerationJob.user_id == current_user.id,
            CreativeGenerationJob.feature_type == _FEATURE_TYPE,
            CreativeGenerationJob.job_id == job_id.strip(),
            CreativeGenerationJob.deleted_at.is_(None),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "job": _job_payload(row)}


@router.post("/api/wechat-channels-transcript/jobs/{job_id}/resume", summary="继续视频号文案提取任务")
async def resume_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(CreativeGenerationJob)
        .filter(
            CreativeGenerationJob.user_id == current_user.id,
            CreativeGenerationJob.feature_type == _FEATURE_TYPE,
            CreativeGenerationJob.job_id == job_id.strip(),
            CreativeGenerationJob.deleted_at.is_(None),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    if row.status == "running":
        return {"ok": True, "job": _job_payload(row)}
    row.status = "queued"
    row.stage = "queued"
    row.progress = min(int(row.progress or 0), 95)
    db.commit()
    asyncio.create_task(_run_transcript_job(row.job_id))
    return {"ok": True, "job": _job_payload(row)}
