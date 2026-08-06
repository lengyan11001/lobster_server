from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import OpenClawMemoryDocument, RecorderAudioRecord, User
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
from .h5_personal_settings import (
    AUDIO_SUFFIXES,
    _call_llm,
    _create_document,
    _download_media_url,
    _installation_id,
    _memory_summary,
    _owner_user,
)

router = APIRouter()
MAX_AUDIO_BYTES = 200 * 1024 * 1024
DATA_ROOT = Path(__file__).resolve().parents[3] / "data" / "recorder_audio"
UPLOAD_AUDIO_SUFFIXES = AUDIO_SUFFIXES | {".opus", ".webm"}
SOURCE_LABELS = {
    "device": "录音设备",
    "local": "本地音频",
    "memory": "记忆文件",
    "personal": "个人 IP 资料",
}


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class RecorderRenameBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)


class RecorderSpeakerRenameBody(BaseModel):
    speaker: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=64)


def _recorded_at(file_name: str) -> datetime | None:
    match = re.search(r"(?<!\d)(\d{14})(?!\d)", file_name or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _remove_recording_files(audio_path: str) -> None:
    if not audio_path:
        return
    try:
        root = DATA_ROOT.resolve()
        source = Path(audio_path).resolve()
        recording_dir = source.parent
        if recording_dir != root and root in recording_dir.parents:
            shutil.rmtree(recording_dir, ignore_errors=True)
    except (OSError, RuntimeError):
        return


def _serialize(row: RecorderAudioRecord, detail: bool = False) -> dict[str, Any]:
    source_type = str(row.source_type or "device").strip().lower()
    data = {
        "id": row.id,
        "file_name": row.file_name,
        "display_name": row.display_name or row.file_name,
        "device_name": row.device_name,
        "source_type": source_type,
        "source_label": SOURCE_LABELS.get(source_type, row.device_name or "音频文件"),
        "source_doc_id": row.source_doc_id or "",
        "file_size": row.file_size,
        "status": row.status,
        "process_stage": row.process_stage,
        "error_message": row.error_message,
        "summary_text": row.summary_text,
        "key_points": row.key_points or [],
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }
    if detail:
        data["transcript_text"] = row.transcript_text
        data["segments"] = row.transcript_segments or []
    return data


def _memory_audio_sources(row: OpenClawMemoryDocument) -> list[dict[str, Any]]:
    meta = row.meta if isinstance(row.meta, dict) else {}
    sources: list[dict[str, Any]] = []
    raw_sources = meta.get("source_audio_files") if isinstance(meta.get("source_audio_files"), list) else []
    for item in raw_sources:
        if not isinstance(item, dict):
            continue
        source_url = str(item.get("source_url") or item.get("url") or "").strip()
        record_id = _as_int(item.get("recorder_record_id"))
        if source_url or record_id:
            sources.append({
                "filename": str(item.get("filename") or row.filename or row.title or "音频文件").strip(),
                "source_url": source_url,
                "file_size": int(item.get("file_size") or 0),
                "recorder_record_id": record_id,
            })
    source_audio = meta.get("source_audio") if isinstance(meta.get("source_audio"), dict) else {}
    record_id = _as_int(source_audio.get("recorder_record_id") or meta.get("recorder_record_id"))
    source_url = str(source_audio.get("source_url") or source_audio.get("url") or "").strip()
    if (record_id or source_url) and not sources:
        sources.append({
            "filename": str(source_audio.get("filename") or row.filename or row.title or "音频文件").strip(),
            "source_url": source_url,
            "file_size": int(source_audio.get("file_size") or 0),
            "recorder_record_id": record_id,
        })
    return sources


def _source_doc_key(doc_id: str, source_index: int) -> str:
    return f"{str(doc_id or '').strip()}:{max(0, int(source_index))}"[:64]


def _validate_audio_upload(file: UploadFile, name: str, source_type: str) -> None:
    if source_type == "device":
        return
    suffix = Path(name).suffix.lower()
    content_type = str(file.content_type or "").lower()
    if suffix not in UPLOAD_AUDIO_SUFFIXES and not content_type.startswith("audio/"):
        raise HTTPException(status_code=400, detail="请选择 MP3、WAV、M4A、AAC、OGG、FLAC、AMR、WMA、OPUS 或 WebM 音频文件")


def _new_audio_record(
    db: Session,
    *,
    user_id: int,
    name: str,
    source: Path,
    size: int,
    source_type: str,
    source_name: str,
    source_doc_id: str = "",
) -> RecorderAudioRecord:
    row = RecorderAudioRecord(
        user_id=user_id,
        file_name=name,
        display_name=name,
        device_name=(source_name or SOURCE_LABELS.get(source_type) or "音频文件")[:128],
        source_type=source_type,
        source_doc_id=source_doc_id or None,
        file_size=size,
        audio_path=str(source),
        status="processing",
        process_stage="uploaded",
        recorded_at=_recorded_at(name) if source_type == "device" else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _speaker_value(item: dict[str, Any]) -> str:
    for key in ("speaker_id", "speaker", "speaker_label", "spk", "spk_id", "channel_id"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _segments(output: dict[str, Any]) -> list[dict[str, Any]]:
    rows = output.get("utterances") or output.get("sentences") or output.get("segments") or []
    if not isinstance(rows, list):
        rows = []
    speaker_labels: dict[str, str] = {}
    result: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("transcript") or "").strip()
        if not text:
            continue
        raw_speaker = _speaker_value(item)
        if raw_speaker and raw_speaker not in speaker_labels:
            speaker_labels[raw_speaker] = chr(ord("A") + min(len(speaker_labels), 25))
        result.append({
            "speaker": speaker_labels.get(raw_speaker, "未知"),
            "speaker_id": raw_speaker,
            "text": text,
            "start_ms": int(float(item.get("start_time") or item.get("start_ms") or 0)),
            "end_ms": int(float(item.get("end_time") or item.get("end_ms") or 0)),
        })
    return result


def _json_object(text: str) -> dict[str, Any]:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.I)
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {"summary": str(text or "").strip(), "key_points": []}


def _recorder_tos_object_key(user_id: int, record_id: int) -> str:
    return f"assets/recorder/{int(user_id)}/{int(record_id)}.wav"


def mark_interrupted_recordings_failed() -> int:
    """Make records interrupted by an H5 restart explicitly retryable."""
    db = SessionLocal()
    try:
        result = db.execute(
            text(
                "UPDATE recorder_audio_records "
                "SET status = 'failed', process_stage = 'failed', "
                "error_message = '服务更新中断了本次处理，请点击重新处理' "
                "WHERE status = 'processing'"
            )
        )
        count = max(0, int(result.rowcount or 0))
        if count:
            db.commit()
        return count
    except SQLAlchemyError:
        db.rollback()
        return 0
    finally:
        db.close()


def _run_stt(record_id: int) -> tuple[str, list[dict[str, Any]], str]:
    db = SessionLocal()
    try:
        row = db.query(RecorderAudioRecord).filter(RecorderAudioRecord.id == record_id).first()
        if not row:
            raise RuntimeError("录音记录不存在")
        row.process_stage = "transcribing"
        db.commit()
        source = Path(row.audio_path)
        job_dir = source.parent / "stt"
        job_dir.mkdir(parents=True, exist_ok=True)
        wav = job_dir / "audio.wav"
        _extract_audio_wav(ffmpeg=_find_ffmpeg_bin(), source=str(source), out_path=wav)
        audio_url = _upload_job_file_to_tos(
            wav,
            object_key=_recorder_tos_object_key(row.user_id, row.id),
            content_type="audio/wav",
        )
        token, _ = _load_sutui_token_for_stt(db, row.user_id)
        created = _stt_create_task(token, audio_url, job_dir=job_dir, enable_speaker_info=True)
        row.stt_task_id = created["task_id"]
        db.commit()
        polled = _stt_poll_task(token, created["task_id"], job_dir=job_dir, timeout_seconds=1800)
        output = _extract_stt_output(polled)
        segments = _segments(output)
        text = str(output.get("text") or output.get("transcript") or "").strip()
        if not text:
            text = "\n".join((f"{x['speaker']}：{x['text']}" if x["speaker"] != "未知" else x["text"]) for x in segments)
        if not text.strip():
            raise RuntimeError("录音未识别到有效语音，请确认录音内容清晰后重试")
        return text, segments, created["task_id"]
    finally:
        db.close()


async def _process(record_id: int, request: Request, installation_id: str) -> None:
    try:
        text, segments, task_id = await asyncio.to_thread(_run_stt, record_id)
        db = SessionLocal()
        try:
            row = db.query(RecorderAudioRecord).filter(RecorderAudioRecord.id == record_id).first()
            if row:
                row.process_stage = "summarizing"
                db.commit()
        finally:
            db.close()
        dialogue = "\n".join(f"{x['speaker']}：{x['text']}" for x in segments) or text
        answer = await _call_llm(request, installation_id, [
            {
                "role": "system",
                "content": (
                    "你是会议录音整理助手。只依据转写内容输出严格 JSON："
                    "summary 为一段明确结论，key_points 为去重后的关键事实、决定或待办数组。"
                    "不要重复改写同一句话，不要编造人物身份、背景、决定或待办。"
                    "如果录音只是测试、操作说明或内容很短，要在 summary 中直接说明录音实际包含什么，"
                    "以及未包含业务结论或待办；key_points 只保留确实存在的信息。"
                ),
            },
            {"role": "user", "content": f"请总结以下录音转写：\n\n{dialogue[:120000]}"},
        ], timeout_seconds=300)
        parsed = _json_object(answer)
        db = SessionLocal()
        try:
            row = db.query(RecorderAudioRecord).filter(RecorderAudioRecord.id == record_id).first()
            if row:
                row.transcript_text = text
                row.transcript_segments = segments
                row.summary_text = str(parsed.get("summary") or "").strip()
                row.key_points = parsed.get("key_points") if isinstance(parsed.get("key_points"), list) else []
                row.stt_task_id = task_id
                row.status = "completed"
                row.process_stage = "completed"
                db.commit()
        finally:
            db.close()
    except Exception as exc:
        db = SessionLocal()
        try:
            row = db.query(RecorderAudioRecord).filter(RecorderAudioRecord.id == record_id).first()
            if row:
                row.status = "failed"
                row.process_stage = "failed"
                row.error_message = str(exc)[:2000]
                db.commit()
        finally:
            db.close()


@router.post("/api/h5/recorder/files")
async def upload_recording(
    request: Request,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    device_name: str = Form(default=""),
    source_type: str = Form(default="device"),
    source_name: str = Form(default=""),
    installation_id: str = Form(default=""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    name = Path(file.filename or "recording.opus").name[:255]
    source_type = str(source_type or "device").strip().lower()
    if source_type not in SOURCE_LABELS:
        source_type = "local"
    _validate_audio_upload(file, name, source_type)
    if source_type == "device":
        existing = db.query(RecorderAudioRecord).filter(
            RecorderAudioRecord.user_id == current_user.id,
            RecorderAudioRecord.file_name == name,
            RecorderAudioRecord.source_type == "device",
        ).first()
        if existing:
            return {"ok": True, "duplicate": True, "record": _serialize(existing, detail=True)}
    target_dir = DATA_ROOT / str(current_user.id) / uuid.uuid4().hex
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    size = 0
    with target.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_AUDIO_BYTES:
                out.close()
                shutil.rmtree(target_dir, ignore_errors=True)
                raise HTTPException(status_code=413, detail="录音文件不能超过 200MB")
            out.write(chunk)
    if size <= 0:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="音频文件为空")
    row = _new_audio_record(
        db,
        user_id=current_user.id,
        name=name,
        source=target,
        size=size,
        source_type=source_type,
        source_name=source_name.strip() or device_name.strip(),
    )
    iid = installation_id.strip() or request.headers.get("X-Installation-Id", "").strip()
    background.add_task(_process, row.id, request, iid)
    return {"ok": True, "record": _serialize(row, detail=True)}


@router.get("/api/h5/recorder/memory-files")
def list_memory_audio_files(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    installation_id = _installation_id(request)
    target_user = _owner_user(db, current_user)
    rows = (
        db.query(OpenClawMemoryDocument)
        .filter(
            OpenClawMemoryDocument.target_user_id == target_user.id,
            OpenClawMemoryDocument.installation_id == installation_id,
            OpenClawMemoryDocument.status == "active",
        )
        .order_by(OpenClawMemoryDocument.updated_at.desc(), OpenClawMemoryDocument.id.desc())
        .limit(200)
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        for source_index, source in enumerate(_memory_audio_sources(row)):
            record_id = _as_int(source.get("recorder_record_id"))
            existing = None
            if record_id:
                existing = db.query(RecorderAudioRecord).filter(
                    RecorderAudioRecord.id == record_id,
                    RecorderAudioRecord.user_id == current_user.id,
                ).first()
                if not existing:
                    continue
            else:
                existing = db.query(RecorderAudioRecord).filter(
                    RecorderAudioRecord.user_id == current_user.id,
                    RecorderAudioRecord.source_doc_id == _source_doc_key(row.doc_id, source_index),
                ).order_by(RecorderAudioRecord.id.desc()).first()
            items.append({
                "doc_id": row.doc_id,
                "source_index": source_index,
                "title": row.title,
                "filename": source.get("filename") or row.filename,
                "file_size": int(source.get("file_size") or 0),
                "existing_record_id": existing.id if existing else None,
                "existing_status": existing.status if existing else "",
                "updated_at": row.updated_at.isoformat() if row.updated_at else "",
            })
    return {"items": items}


@router.post("/api/h5/recorder/memory-files/{doc_id}/transcribe")
async def transcribe_memory_audio_file(
    doc_id: str,
    request: Request,
    background: BackgroundTasks,
    source_index: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    installation_id = _installation_id(request)
    target_user = _owner_user(db, current_user)
    row = db.query(OpenClawMemoryDocument).filter(
        OpenClawMemoryDocument.doc_id == doc_id,
        OpenClawMemoryDocument.target_user_id == target_user.id,
        OpenClawMemoryDocument.installation_id == installation_id,
        OpenClawMemoryDocument.status == "active",
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="记忆文件不存在或不属于当前账号")
    sources = _memory_audio_sources(row)
    if source_index >= len(sources):
        raise HTTPException(status_code=404, detail="该记忆文件没有可用的原始音频")
    source = sources[source_index]
    source_record_id = _as_int(source.get("recorder_record_id"))
    if source_record_id:
        existing = db.query(RecorderAudioRecord).filter(
            RecorderAudioRecord.id == source_record_id,
            RecorderAudioRecord.user_id == current_user.id,
        ).first()
        if not existing:
            raise HTTPException(status_code=404, detail="原始转写记录已删除")
        return {"ok": True, "existing": True, "record": _serialize(existing, detail=True)}

    source_key = _source_doc_key(row.doc_id, source_index)
    existing = db.query(RecorderAudioRecord).filter(
        RecorderAudioRecord.user_id == current_user.id,
        RecorderAudioRecord.source_doc_id == source_key,
    ).order_by(RecorderAudioRecord.id.desc()).first()
    if existing:
        return {"ok": True, "existing": True, "record": _serialize(existing, detail=True)}
    source_url = str(source.get("source_url") or "").strip()
    if not source_url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=410, detail="原始音频已不可用，请重新上传")
    try:
        audio_data = await _download_media_url(source_url, max_bytes=MAX_AUDIO_BYTES)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"原始音频下载失败：{str(exc)[:300]}") from exc
    if not audio_data:
        raise HTTPException(status_code=410, detail="原始音频为空，请重新上传")
    filename = Path(str(source.get("filename") or row.filename or "memory-audio.wav")).name[:255]
    if Path(filename).suffix.lower() not in UPLOAD_AUDIO_SUFFIXES:
        filename = f"{Path(filename).stem or 'memory-audio'}.wav"
    target_dir = DATA_ROOT / str(current_user.id) / uuid.uuid4().hex
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    target.write_bytes(audio_data)
    record = _new_audio_record(
        db,
        user_id=current_user.id,
        name=filename,
        source=target,
        size=len(audio_data),
        source_type="memory",
        source_name=row.title or "记忆文件",
        source_doc_id=source_key,
    )
    background.add_task(_process, record.id, request, installation_id)
    return {"ok": True, "record": _serialize(record, detail=True)}


@router.get("/api/h5/recorder/files")
def list_recordings(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    source_type: str = Query(default=""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(RecorderAudioRecord).filter(RecorderAudioRecord.user_id == current_user.id)
    clean_source_type = str(source_type or "").strip().lower()
    if clean_source_type in SOURCE_LABELS:
        query = query.filter(RecorderAudioRecord.source_type == clean_source_type)
    total = query.count()
    rows = query.order_by(RecorderAudioRecord.recorded_at.desc().nullslast(), RecorderAudioRecord.created_at.desc(), RecorderAudioRecord.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [_serialize(row) for row in rows], "page": page, "page_size": page_size, "total": total, "has_next": page * page_size < total}


@router.get("/api/h5/recorder/known-names")
def recorder_known_names(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(RecorderAudioRecord.file_name).filter(
        RecorderAudioRecord.user_id == current_user.id,
        RecorderAudioRecord.source_type == "device",
    ).all()
    return {"items": [row[0] for row in rows]}


@router.patch("/api/h5/recorder/files/{record_id}")
def rename_recording(record_id: int, body: RecorderRenameBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(RecorderAudioRecord).filter(RecorderAudioRecord.id == record_id, RecorderAudioRecord.user_id == current_user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="录音不存在")
    row.display_name = body.display_name.strip()
    db.commit()
    db.refresh(row)
    return {"ok": True, "record": _serialize(row)}


@router.patch("/api/h5/recorder/files/{record_id}/speakers")
def rename_recording_speaker(
    record_id: int,
    body: RecorderSpeakerRenameBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(RecorderAudioRecord).filter(
        RecorderAudioRecord.id == record_id,
        RecorderAudioRecord.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="录音不存在")
    source_name = body.speaker.strip()
    display_name = body.display_name.strip()
    segments = [dict(item) for item in (row.transcript_segments or []) if isinstance(item, dict)]
    matched = 0
    for item in segments:
        if str(item.get("speaker") or "").strip() == source_name:
            item["speaker"] = display_name
            matched += 1
    if not matched:
        raise HTTPException(status_code=404, detail="该说话人已不存在，请刷新后重试")
    row.transcript_segments = segments
    db.commit()
    db.refresh(row)
    return {"ok": True, "updated_segments": matched, "record": _serialize(row, detail=True)}


@router.get("/api/h5/recorder/files/{record_id}")
def recording_detail(record_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(RecorderAudioRecord).filter(RecorderAudioRecord.id == record_id, RecorderAudioRecord.user_id == current_user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="录音不存在")
    return _serialize(row, detail=True)


@router.post("/api/h5/recorder/files/{record_id}/memory")
def save_recording_as_memory(
    record_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(RecorderAudioRecord).filter(
        RecorderAudioRecord.id == record_id,
        RecorderAudioRecord.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="转写记录不存在")
    if row.status != "completed" or not row.transcript_text.strip():
        raise HTTPException(status_code=409, detail="音频转写完成后才能用于生成百问百答")
    installation_id = _installation_id(request)
    target_user = _owner_user(db, current_user)
    existing_rows = db.query(OpenClawMemoryDocument).filter(
        OpenClawMemoryDocument.target_user_id == target_user.id,
        OpenClawMemoryDocument.installation_id == installation_id,
        OpenClawMemoryDocument.status == "active",
    ).order_by(OpenClawMemoryDocument.updated_at.desc()).limit(300).all()
    for existing in existing_rows:
        meta = existing.meta if isinstance(existing.meta, dict) else {}
        if _as_int(meta.get("recorder_record_id")) == row.id:
            return {"ok": True, "existing": True, "document": _memory_summary(existing, include_content=True, source="own")}

    parts = []
    if row.summary_text.strip():
        parts.append(f"【音频摘要】\n{row.summary_text.strip()}")
    if row.key_points:
        points = "\n".join(f"- {str(item).strip()}" for item in row.key_points if str(item).strip())
        if points:
            parts.append(f"【重点事项】\n{points}")
    parts.append(f"【完整转写】\n{row.transcript_text.strip()}")
    title = f"{(row.display_name or row.file_name or '音频')[:120]}转写"
    memory = _create_document(
        db,
        target_user=target_user,
        uploader_user=current_user,
        installation_id=installation_id,
        title=title,
        filename=f"{title}.txt",
        notes="音频转写与摘要，可用于生成百问百答",
        content_text="\n\n".join(parts),
        meta={
            "save_mode": "new",
            "uploaded": True,
            "audio_transcription": True,
            "recorder_record_id": row.id,
            "source_audio": {
                "recorder_record_id": row.id,
                "filename": row.file_name,
                "file_size": row.file_size,
            },
        },
    )
    return {"ok": True, "document": _memory_summary(memory, include_content=True, source="own")}


@router.get("/api/h5/recorder/files/{record_id}/audio")
def recording_audio(record_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(RecorderAudioRecord).filter(
        RecorderAudioRecord.id == record_id,
        RecorderAudioRecord.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="录音不存在")
    source = Path(row.audio_path or "")
    if not source.is_file():
        raise HTTPException(status_code=410, detail="原始录音文件已不存在")
    media_type = mimetypes.guess_type(source.name)[0] or ("audio/ogg" if source.suffix.lower() == ".opus" else "application/octet-stream")
    return FileResponse(str(source), media_type=media_type)


@router.post("/api/h5/recorder/files/{record_id}/retry")
async def retry_recording(
    record_id: int,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(RecorderAudioRecord).filter(
        RecorderAudioRecord.id == record_id,
        RecorderAudioRecord.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="录音不存在")
    if row.status == "processing":
        return {"ok": True, "already_processing": True, "record": _serialize(row, detail=True)}
    if not row.audio_path or not Path(row.audio_path).is_file():
        raise HTTPException(status_code=410, detail="原始录音文件已不存在，请重新同步")

    row.status = "processing"
    row.process_stage = "uploaded"
    row.error_message = ""
    row.stt_task_id = ""
    row.transcript_text = ""
    row.transcript_segments = []
    row.summary_text = ""
    row.key_points = []
    db.commit()
    db.refresh(row)
    iid = request.headers.get("X-Installation-Id", "").strip()
    background.add_task(_process, row.id, request, iid)
    return {"ok": True, "record": _serialize(row, detail=True)}


@router.delete("/api/h5/recorder/files/{record_id}")
def delete_recording(record_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(RecorderAudioRecord).filter(
        RecorderAudioRecord.id == record_id,
        RecorderAudioRecord.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="录音不存在")
    audio_path = row.audio_path
    db.delete(row)
    db.commit()
    _remove_recording_files(audio_path)
    return {"ok": True, "id": record_id}
