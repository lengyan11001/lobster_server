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
from ..models import RecorderAudioRecord, User
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
from .h5_personal_settings import _call_llm

router = APIRouter()
MAX_AUDIO_BYTES = 200 * 1024 * 1024
DATA_ROOT = Path(__file__).resolve().parents[3] / "data" / "recorder_audio"


class RecorderRenameBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)


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
    data = {
        "id": row.id,
        "file_name": row.file_name,
        "display_name": row.display_name or row.file_name,
        "device_name": row.device_name,
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
    installation_id: str = Form(default=""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    name = Path(file.filename or "recording.opus").name[:255]
    existing = db.query(RecorderAudioRecord).filter(
        RecorderAudioRecord.user_id == current_user.id,
        RecorderAudioRecord.file_name == name,
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
    row = RecorderAudioRecord(user_id=current_user.id, file_name=name, display_name=name, device_name=device_name[:128], file_size=size, audio_path=str(target), status="processing", process_stage="uploaded", recorded_at=_recorded_at(name))
    db.add(row)
    db.commit()
    db.refresh(row)
    iid = installation_id.strip() or request.headers.get("X-Installation-Id", "").strip()
    background.add_task(_process, row.id, request, iid)
    return {"ok": True, "record": _serialize(row, detail=True)}


@router.get("/api/h5/recorder/files")
def list_recordings(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(RecorderAudioRecord).filter(RecorderAudioRecord.user_id == current_user.id)
    total = query.count()
    rows = query.order_by(RecorderAudioRecord.recorded_at.desc().nullslast(), RecorderAudioRecord.created_at.desc(), RecorderAudioRecord.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [_serialize(row) for row in rows], "page": page, "page_size": page_size, "total": total, "has_next": page * page_size < total}


@router.get("/api/h5/recorder/known-names")
def recorder_known_names(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(RecorderAudioRecord.file_name).filter(RecorderAudioRecord.user_id == current_user.id).all()
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


@router.get("/api/h5/recorder/files/{record_id}")
def recording_detail(record_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(RecorderAudioRecord).filter(RecorderAudioRecord.id == record_id, RecorderAudioRecord.user_id == current_user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="录音不存在")
    return _serialize(row, detail=True)


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
