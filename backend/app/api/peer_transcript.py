"""数字人「同行」：抖音 / 视频号链接 → 音频 → STT → AI 标题 + 口播文案。

流程（异步任务，避免小程序请求超时）：
1. POST /api/digital-human/peer-jobs      {url}  → {job_id}
2. GET  /api/digital-human/peer-jobs/{id}          → {status, transcript, error}
3. POST /api/digital-human/peer-script    {transcript} → {title, script}

抖音走 TikHub 解析作品 → 播放地址；视频号走 wechat_channels_video_detail_v2（支持分享链接）。
拿到视频地址后复用视频号转写那套：下载 → ffmpeg 抽音频 → 传 TOS → 速推 STT。
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import CreativeGenerationJob, User
from ..services.douyin_imitation_video import extract_douyin_item_id, resolve_source_video
from ..services.workload_guard import WorkloadQueueFull, background_heavy_slot, spawn_tracked_task
from .auth import create_access_token, access_token_claims, get_current_user
from .cutcli_templates import _load_sutui_token_for_stt
from .h5_recorder_extract import _extract_json_object
from .ip_content_studio import _execute_query_with_retry
from .wechat_channels_transcript import _normalize_video, _process_one_video

router = APIRouter()

_FEATURE_TYPE = "digital_peer_transcript"
_JOBS_DIR = Path(__file__).resolve().parents[3] / "data" / "digital_peer_transcript"
_JOBS_DIR.mkdir(parents=True, exist_ok=True)
_TERMINAL = {"completed", "failed"}


class PeerJobBody(BaseModel):
    url: str = Field(min_length=3, max_length=4000)


class PeerScriptBody(BaseModel):
    transcript: str = Field(min_length=1, max_length=40000)


def _first_url(value: str) -> str:
    text = unquote((value or "").strip())
    for prefix in ("https://", "http://"):
        idx = text.find(prefix)
        if idx >= 0:
            candidate = text[idx:].split()[0].strip(" ,.;)]}>\"'")
            if candidate:
                return candidate
    return text


def _is_douyin(url: str) -> bool:
    low = (url or "").lower()
    return "douyin.com" in low or "iesdouyin.com" in low


def _is_channels(url: str) -> bool:
    low = (url or "").lower()
    return any(m in low for m in ("channels.weixin.qq.com", "finder.video.qq.com", "weixin.qq.com/sph", "weixin.qq.com/sph/"))


def _job_payload(row: CreativeGenerationJob, transcript: str = "", source: str = "") -> dict[str, Any]:
    meta = row.meta if isinstance(row.meta, dict) else {}
    return {
        "job_id": row.job_id,
        "status": row.status or "",
        "stage": row.stage or "",
        "progress": int(row.progress or 0),
        "transcript": str(meta.get("transcript") or transcript or ""),
        "source": str(meta.get("source") or source or ""),
        "error": row.error or "",
    }


async def _resolve_video_url(db: Session, current_user: User, raw_url: str) -> tuple[str, str, dict[str, Any]]:
    url = _first_url(raw_url)
    if _is_douyin(url):
        item_id = await extract_douyin_item_id(url)
        if not item_id:
            raise RuntimeError("链接里没解析到作品 ID，请复制作品页链接再试")
        src = await resolve_source_video(item_id)
        if not src.get("ok"):
            raise RuntimeError(str(src.get("error") or "作品解析失败")[:300])
        return str(src.get("url") or ""), "douyin", {"item_id": item_id}
    if _is_channels(url):
        result = await _execute_query_with_retry(
            db=db,
            current_user=current_user,
            query_type="wechat_channels_video_detail_v2",
            params={},
            body={"share_url": url, "raw": False},
            save_items=False,
            meta={"source": "digital_peer_transcript", "input": url[:200]},
            attempts=2,
            include_raw_response=True,
        )
        raw = result.get("raw_response") if isinstance(result.get("raw_response"), dict) else {}
        video = _normalize_video(raw, 0) if raw else {}
        play = str((video or {}).get("video_url") or (video or {}).get("public_url") or "").strip()
        if not play:
            raise RuntimeError("链接没解析出可下载的视频地址")
        return play, "wechat_channels", {}
    raise RuntimeError("只支持作品链接")


async def _run_peer_job(job_id: str) -> None:
    try:
        async with background_heavy_slot("digital_peer_transcript"):
            await _run_peer_job_inner(job_id)
    except WorkloadQueueFull:
        with SessionLocal() as db:
            row = (
                db.query(CreativeGenerationJob)
                .filter(CreativeGenerationJob.job_id == job_id,
                        CreativeGenerationJob.feature_type == _FEATURE_TYPE)
                .first()
            )
            if row and row.status not in _TERMINAL:
                row.status = "failed"
                row.stage = "failed"
                row.error = "当前转写任务较多，排队已满，请稍后再试"
                db.commit()


async def _run_peer_job_inner(job_id: str) -> None:
    job_dir = _JOBS_DIR / job_id
    with SessionLocal() as db:
        row = (
            db.query(CreativeGenerationJob)
            .filter(CreativeGenerationJob.job_id == job_id,
                    CreativeGenerationJob.feature_type == _FEATURE_TYPE)
            .first()
        )
        if not row:
            return
        user = db.query(User).filter(User.id == row.user_id).first()
        if not user:
            row.status = "failed"
            row.stage = "failed"
            row.error = "用户不存在"
            db.commit()
            return
        try:
            row.status = "running"
            row.stage = "resolving"
            row.progress = 5
            db.commit()
            payload = row.request_payload if isinstance(row.request_payload, dict) else {}
            video_url, source, extra = await _resolve_video_url(db, user, str(payload.get("url") or ""))
            if not video_url:
                raise RuntimeError("拿不到可下载的视频地址")
            row.stage = "transcribing"
            row.progress = 25
            meta = dict(row.meta or {})
            meta.update({"source": source, "video_url": video_url, **extra})
            row.meta = meta
            db.commit()

            token, token_source = _load_sutui_token_for_stt(db, int(user.id))
            db.commit()
            job_dir.mkdir(parents=True, exist_ok=True)
            item = await asyncio.to_thread(
                _process_one_video,
                job_id=job_id,
                item={"video_url": video_url, "source": source},
                index=0,
                token=token,
                token_source=token_source,
                job_dir=job_dir,
            )
            text = str((item or {}).get("transcript") or "").strip()
            if not text:
                raise RuntimeError("转写结果为空，换一条链接再试")
            meta = dict(row.meta or {})
            meta.update({"transcript": text, "audio_url": (item or {}).get("audio_url") or ""})
            row.meta = meta
            row.status = "completed"
            row.stage = "completed"
            row.progress = 100
            row.error = ""
            db.commit()
        except Exception as exc:  # noqa: BLE001
            row.status = "failed"
            row.stage = "failed"
            row.error = str(exc)[:800]
            db.commit()
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)


@router.post("/api/digital-human/peer-jobs", summary="同行链接解析 + 语音转写（异步）")
async def create_peer_job(
    body: PeerJobBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    raw = str(body.url or "").strip()
    if not (_is_douyin(raw) or _is_channels(raw)):
        raise HTTPException(status_code=400, detail="只支持作品链接")
    job_id = "peer_" + uuid.uuid4().hex[:24]
    row = CreativeGenerationJob(
        job_id=job_id,
        user_id=int(current_user.id),
        feature_type=_FEATURE_TYPE,
        provider="tikhub+stt",
        status="queued",
        stage="queued",
        progress=0,
        title="同行文案提取",
        prompt=raw[:500],
        request_payload={"url": raw},
        result_payload={},
        meta={},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    spawn_tracked_task(_run_peer_job(job_id), name=f"digital-peer-{job_id}")
    return {"ok": True, "job_id": job_id}


@router.get("/api/digital-human/peer-jobs/{job_id}", summary="同行链接解析进度")
async def get_peer_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = (
        db.query(CreativeGenerationJob)
        .filter(CreativeGenerationJob.job_id == job_id,
                CreativeGenerationJob.user_id == int(current_user.id),
                CreativeGenerationJob.feature_type == _FEATURE_TYPE)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, **_job_payload(row)}


def _model_name() -> str:
    return (
        (os.environ.get("LOBSTER_ORCHESTRATION_SUTUI_CHAT_MODEL") or "").strip()
        or (os.environ.get("LOBSTER_DEFAULT_SUTUI_CHAT_MODEL") or "").strip()
        or "openai/gpt-4.1-mini"
    )


@router.post("/api/digital-human/peer-script", summary="按同行转写文本写标题和口播文案")
async def peer_script(
    body: PeerScriptBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    text = str(body.transcript or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="没有转写文本")
    try:
        owner_token = create_access_token(access_token_claims(current_user))
    except Exception:
        owner_token = ""
    base = (os.environ.get("LOBSTER_INTERNAL_API_BASE") or "").strip().rstrip("/") or "http://127.0.0.1:8000"
    system_prompt = (
        "你是中文短视频编导。下面是一段同行口播的转写文本，请改写成本账号可以直接开拍的数字人口播稿。"
        "只输出严格 JSON：{\"title\":\"12-20字标题\",\"script\":\"口播文案\"}。"
        "要求：script 用第一人称、口语化，保留原文的核心观点/信息/案例，去掉原作者的自我称呼和无关寒暄，"
        "开头 3 秒抓人，句子短、有节奏，不要 Markdown、不要表情符号、不要分镜说明、不要编造原文没有的数据；"
        "script 长度 180-400 字。"
    )
    user_prompt = "【同行转写文本】\n" + text[:20000]
    headers = {"Content-Type": "application/json"}
    if owner_token:
        headers["Authorization"] = "Bearer " + owner_token
    payload = {
        "model": _model_name(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.5,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
            resp = await client.post(base + "/api/sutui-chat/completions", json=payload, headers=headers)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="AI 生成失败：" + str(exc)[:200]) from exc
    if resp.status_code >= 400:
        detail = ""
        try:
            raw = resp.json()
            detail = str(raw.get("detail") or raw.get("message") or "")
        except Exception:
            detail = (resp.text or "")[:200]
        raise HTTPException(status_code=502, detail="AI 生成失败：" + (detail or str(resp.status_code)))
    data = resp.json() if resp.content else {}
    content = ""
    try:
        content = str(data["choices"][0]["message"]["content"] or "")
    except Exception:
        content = str(data.get("text") or "")
    parsed = _extract_json_object(content)
    title = str(parsed.get("title") or "").strip()[:40]
    script = str(parsed.get("script") or "").strip()[:4000]
    if not script:
        raise HTTPException(status_code=502, detail="AI 没有返回口播文案")
    return {"ok": True, "title": title, "script": script}
