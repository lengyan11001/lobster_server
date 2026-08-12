from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import math
import mimetypes
import os
import re
import shutil
import threading
import time
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
from ..services.workload_guard import WorkloadQueueFull, background_heavy_slot
from .auth import get_current_user
from .cutcli_templates import (
    _extract_stt_output,
    _find_ffmpeg_bin,
    _find_ffprobe_bin,
    _load_sutui_token_for_stt,
    _run_cmd,
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
logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 200 * 1024 * 1024
DATA_ROOT = Path(__file__).resolve().parents[3] / "data" / "recorder_audio"
UPLOAD_AUDIO_SUFFIXES = AUDIO_SUFFIXES | {".opus", ".webm"}


def _bounded_env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


DEFAULT_STT_CHUNK_SECONDS = 2 * 60
STT_CHUNK_SECONDS = _bounded_env_int(
    "RECORDER_STT_CHUNK_SECONDS",
    DEFAULT_STT_CHUNK_SECONDS,
    minimum=2 * 60,
    maximum=30 * 60,
)
STT_MIN_SPLIT_SECONDS = _bounded_env_int(
    "RECORDER_STT_MIN_SPLIT_SECONDS",
    90,
    minimum=30,
    maximum=5 * 60,
)
STT_MAX_AUDIO_SECONDS = _bounded_env_int(
    "RECORDER_STT_MAX_AUDIO_SECONDS",
    6 * 60 * 60,
    minimum=30 * 60,
    maximum=24 * 60 * 60,
)
STT_MAX_PARALLEL_CHUNKS = _bounded_env_int(
    "RECORDER_STT_MAX_PARALLEL_CHUNKS",
    3,
    minimum=1,
    maximum=6,
)
STT_GLOBAL_MAX_CONCURRENT_CHUNKS = _bounded_env_int(
    "RECORDER_STT_GLOBAL_MAX_CONCURRENT_CHUNKS",
    6,
    minimum=1,
    maximum=24,
)
_STT_GLOBAL_CHUNK_GATE = threading.BoundedSemaphore(STT_GLOBAL_MAX_CONCURRENT_CHUNKS)
SOURCE_LABELS = {
    "device": "录音设备",
    "local": "本地音频",
    "memory": "记忆文件",
    "personal": "个人 IP 资料",
    "live_executor": "现场执行台",
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
    speaker_id: str | None = Field(default=None, max_length=128)
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
    keys = ("speaker_id", "speaker", "speaker_label", "spk", "spk_id", "channel_id")

    def pick(source: Any) -> str:
        if not isinstance(source, dict):
            return ""
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    direct = pick(item)
    if direct:
        return direct
    # The speech gateway puts diarization on utterances[].additions.speaker.
    for container_key in ("additions", "addition", "metadata", "meta", "speaker_info", "speakerInfo"):
        nested = pick(item.get(container_key))
        if nested:
            return nested
    return ""


def _segments(output: dict[str, Any], speaker_labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    rows = output.get("utterances") or output.get("sentences") or output.get("segments") or []
    if not isinstance(rows, list):
        rows = []
    labels = speaker_labels if speaker_labels is not None else {}
    result: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("transcript") or "").strip()
        if not text:
            continue
        raw_speaker = _speaker_value(item)
        if raw_speaker and raw_speaker not in labels:
            labels[raw_speaker] = chr(ord("A") + min(len(labels), 25))
        result.append({
            "speaker": labels.get(raw_speaker, "未知"),
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


def _recorder_tos_chunk_object_key(user_id: int, record_id: int, chunk_key: str) -> str:
    return f"assets/recorder/{int(user_id)}/{int(record_id)}/chunks/{chunk_key}.wav"


def _audio_duration_seconds(source: Path) -> float:
    raw = _run_cmd(
        [
            _find_ffprobe_bin(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ],
        timeout=60,
    )
    try:
        duration = float(str(raw or "").strip().splitlines()[0])
    except (IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("无法读取音频时长，请确认音频文件可以正常播放") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("音频时长无效，请确认音频文件可以正常播放")
    if duration > STT_MAX_AUDIO_SECONDS:
        max_hours = STT_MAX_AUDIO_SECONDS / 3600
        raise RuntimeError(f"单个音频不能超过 {max_hours:g} 小时，请拆分后再转写")
    return duration


def _stt_chunk_plan(duration_seconds: float, chunk_seconds: int | None = None) -> list[tuple[float, float]]:
    duration = max(0.001, float(duration_seconds or 0))
    chunk = max(1, int(chunk_seconds or STT_CHUNK_SECONDS))
    count = max(1, int(math.ceil(duration / chunk)))
    return [
        (float(index * chunk), min(float(chunk), max(0.001, duration - index * chunk)))
        for index in range(count)
    ]


def _extract_audio_wav_chunk(
    *,
    ffmpeg: str,
    source: Path,
    out_path: Path,
    start_seconds: float,
    duration_seconds: float,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run_cmd(
        [
            ffmpeg,
            "-y",
            "-ss",
            f"{max(0.0, float(start_seconds)):.3f}",
            "-i",
            str(source),
            "-t",
            f"{max(0.1, float(duration_seconds)):.3f}",
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
        raise RuntimeError("切分后的音频为空，请确认原始音频可以正常播放")


def _chunk_cache_key(start_seconds: float, duration_seconds: float) -> str:
    return f"{round(max(0.0, start_seconds) * 1000):012d}_{round(max(0.1, duration_seconds) * 1000):010d}"


def _read_cached_stt_chunk(chunk_dir: Path) -> tuple[dict[str, Any], str] | None:
    complete_path = chunk_dir / "stt_complete.json"
    if complete_path.is_file():
        try:
            value = json.loads(complete_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = None
        if isinstance(value, dict) and isinstance(value.get("stt_data"), dict):
            return value["stt_data"], str(value.get("task_id") or "").strip()

    latest_path = chunk_dir / "stt_result_latest.json"
    if not latest_path.is_file():
        return None
    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(latest, dict):
        return None
    data = latest.get("data") if isinstance(latest.get("data"), dict) else latest
    status = str(data.get("status") or "").strip().lower() if isinstance(data, dict) else ""
    if status not in {"completed", "complete", "success", "succeeded", "finished", "done"}:
        return None
    return data, _created_stt_task_id(chunk_dir)


def _created_stt_task_id(chunk_dir: Path) -> str:
    path = chunk_dir / "stt_create_response.json"
    if not path.is_file():
        return ""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(value, dict):
        return ""
    data = value.get("data") if isinstance(value.get("data"), dict) else value
    return str(data.get("task_id") or data.get("taskId") or data.get("id") or "").strip()


def _resumable_stt_task_id(chunk_dir: Path) -> str:
    task_id = _created_stt_task_id(chunk_dir)
    if not task_id:
        return ""
    path = chunk_dir / "stt_result_latest.json"
    if not path.is_file():
        return task_id
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return task_id
    data = value.get("data") if isinstance(value, dict) and isinstance(value.get("data"), dict) else value
    status = str(data.get("status") or "").strip().lower() if isinstance(data, dict) else ""
    if status in {"failed", "error", "cancelled", "canceled", "timeout", "rejected"}:
        return ""
    return task_id


def _write_cached_stt_chunk(chunk_dir: Path, stt_data: dict[str, Any], task_id: str) -> None:
    path = chunk_dir / "stt_complete.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"task_id": task_id, "stt_data": stt_data}, ensure_ascii=False),
        encoding="utf-8",
    )
    temp.replace(path)


def _stt_text(output: dict[str, Any]) -> str:
    for key in ("text", "transcript", "content", "result_text"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _merge_stt_parts(parts: list[tuple[float, dict[str, Any]]]) -> tuple[str, list[dict[str, Any]]]:
    texts: list[str] = []
    merged_segments: list[dict[str, Any]] = []
    speaker_labels: dict[str, str] = {}
    for start_seconds, output in sorted(parts, key=lambda item: item[0]):
        chunk_segments = _segments(output, speaker_labels)
        offset_ms = round(max(0.0, float(start_seconds)) * 1000)
        for segment in chunk_segments:
            item = dict(segment)
            item["start_ms"] = offset_ms + int(item.get("start_ms") or 0)
            item["end_ms"] = offset_ms + int(item.get("end_ms") or 0)
            merged_segments.append(item)
        text_value = _stt_text(output)
        if not text_value and chunk_segments:
            text_value = "\n".join(
                f"{item['speaker']}：{item['text']}" if item["speaker"] != "未知" else item["text"]
                for item in chunk_segments
            )
        if text_value:
            texts.append(text_value)
    merged_segments.sort(key=lambda item: (int(item.get("start_ms") or 0), int(item.get("end_ms") or 0)))
    return "\n".join(texts).strip(), merged_segments


def _stt_task_reference(task_ids: list[str]) -> str:
    unique = list(dict.fromkeys(task_id for task_id in task_ids if task_id))
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0][:128]
    return f"batch:{len(unique)}:{unique[0][:36]}:{unique[-1][:36]}"[:128]


def _transcribe_stt_range(
    *,
    token: str,
    ffmpeg: str,
    source: Path,
    job_dir: Path,
    user_id: int,
    record_id: int,
    start_seconds: float,
    duration_seconds: float,
) -> tuple[list[tuple[float, dict[str, Any]]], list[str]]:
    chunk_key = _chunk_cache_key(start_seconds, duration_seconds)
    chunk_dir = job_dir / "chunks" / chunk_key
    chunk_dir.mkdir(parents=True, exist_ok=True)
    cached = _read_cached_stt_chunk(chunk_dir)
    if cached:
        stt_data, task_id = cached
    else:
        task_id = _resumable_stt_task_id(chunk_dir)
        if not task_id:
            wav = chunk_dir / "audio.wav"
            _extract_audio_wav_chunk(
                ffmpeg=ffmpeg,
                source=source,
                out_path=wav,
                start_seconds=start_seconds,
                duration_seconds=duration_seconds,
            )
            try:
                audio_url = _upload_job_file_to_tos(
                    wav,
                    object_key=_recorder_tos_chunk_object_key(user_id, record_id, chunk_key),
                    content_type="audio/wav",
                )
                created = _stt_create_task(token, audio_url, job_dir=chunk_dir, enable_speaker_info=True)
                task_id = created["task_id"]
            finally:
                wav.unlink(missing_ok=True)
        stt_data = _stt_poll_task(token, task_id, job_dir=chunk_dir, timeout_seconds=1800)
        _write_cached_stt_chunk(chunk_dir, stt_data, task_id)

    output = _extract_stt_output(stt_data)
    if output.get("_stt_partial"):
        if duration_seconds <= STT_MIN_SPLIT_SECONDS:
            raise RuntimeError("语音服务返回结果被截断，请稍后重新处理该音频")
        first_duration = duration_seconds / 2
        second_duration = duration_seconds - first_duration
        first_parts, first_tasks = _transcribe_stt_range(
            token=token,
            ffmpeg=ffmpeg,
            source=source,
            job_dir=job_dir,
            user_id=user_id,
            record_id=record_id,
            start_seconds=start_seconds,
            duration_seconds=first_duration,
        )
        second_parts, second_tasks = _transcribe_stt_range(
            token=token,
            ffmpeg=ffmpeg,
            source=source,
            job_dir=job_dir,
            user_id=user_id,
            record_id=record_id,
            start_seconds=start_seconds + first_duration,
            duration_seconds=second_duration,
        )
        return first_parts + second_parts, [task_id] + first_tasks + second_tasks
    return [(start_seconds, output)], [task_id] if task_id else []


def _transcribe_stt_plan_range(**kwargs: Any) -> tuple[
    list[tuple[float, dict[str, Any]]],
    list[str],
    int,
    int,
]:
    queued_at = time.monotonic()
    with _STT_GLOBAL_CHUNK_GATE:
        started_at = time.monotonic()
        parts, task_ids = _transcribe_stt_range(**kwargs)
    finished_at = time.monotonic()
    return (
        parts,
        task_ids,
        max(0, round((started_at - queued_at) * 1000)),
        max(0, round((finished_at - started_at) * 1000)),
    )


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
        source = Path(row.audio_path)
        if not source.is_file():
            raise RuntimeError("原始录音文件已不存在，请重新上传")
        user_id = int(row.user_id)
        stored_record_id = int(row.id)
        db.commit()
        job_dir = source.parent / "stt"
        job_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg = _find_ffmpeg_bin()
        duration_seconds = _audio_duration_seconds(source)
        plan = _stt_chunk_plan(duration_seconds)
        token, _ = _load_sutui_token_for_stt(db, user_id)
        total_chunks = len(plan)
        worker_count = min(STT_MAX_PARALLEL_CHUNKS, total_chunks)
        db.query(RecorderAudioRecord).filter(RecorderAudioRecord.id == stored_record_id).update(
            {"process_stage": f"transcribing:0/{total_chunks}"},
            synchronize_session=False,
        )
        db.commit()
        transcription_started_at = time.monotonic()
        results_by_index: dict[int, tuple[list[tuple[float, dict[str, Any]]], list[str]]] = {}
        completed_task_ids: list[str] = []
        executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="recorder-stt")
        futures = {}
        try:
            futures = {
                executor.submit(
                    _transcribe_stt_plan_range,
                    token=token,
                    ffmpeg=ffmpeg,
                    source=source,
                    job_dir=job_dir,
                    user_id=user_id,
                    record_id=stored_record_id,
                    start_seconds=start_seconds,
                    duration_seconds=chunk_duration,
                ): (index, start_seconds, chunk_duration)
                for index, (start_seconds, chunk_duration) in enumerate(plan)
            }
            for completed_count, future in enumerate(as_completed(futures), start=1):
                index, start_seconds, chunk_duration = futures[future]
                chunk_parts, chunk_task_ids, global_wait_ms, elapsed_ms = future.result()
                results_by_index[index] = (chunk_parts, chunk_task_ids)
                completed_task_ids.extend(chunk_task_ids)
                task_reference = _stt_task_reference(completed_task_ids)
                values: dict[str, Any] = {
                    "process_stage": f"transcribing:{completed_count}/{total_chunks}",
                }
                if task_reference:
                    values["stt_task_id"] = task_reference
                db.query(RecorderAudioRecord).filter(RecorderAudioRecord.id == stored_record_id).update(
                    values,
                    synchronize_session=False,
                )
                db.commit()
                logger.info(
                    "recorder STT chunk completed record_id=%s chunk=%s/%s start=%.3f duration=%.3f "
                    "tasks=%s parts=%s global_wait_ms=%s elapsed_ms=%s",
                    stored_record_id,
                    completed_count,
                    total_chunks,
                    start_seconds,
                    chunk_duration,
                    len(chunk_task_ids),
                    len(chunk_parts),
                    global_wait_ms,
                    elapsed_ms,
                )
        except BaseException:
            for future in futures:
                future.cancel()
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        parts: list[tuple[float, dict[str, Any]]] = []
        task_ids: list[str] = []
        for index in range(total_chunks):
            chunk_parts, chunk_task_ids = results_by_index[index]
            parts.extend(chunk_parts)
            task_ids.extend(chunk_task_ids)
        transcription_elapsed_seconds = round(time.monotonic() - transcription_started_at, 3)

        text, segments = _merge_stt_parts(parts)
        if not text.strip():
            raise RuntimeError("录音未识别到有效语音，请确认录音内容清晰后重试")
        task_reference = _stt_task_reference(task_ids)
        final_values: dict[str, Any] = {"process_stage": f"transcribing:{total_chunks}/{total_chunks}"}
        if task_reference:
            final_values["stt_task_id"] = task_reference
        db.query(RecorderAudioRecord).filter(RecorderAudioRecord.id == stored_record_id).update(
            final_values,
            synchronize_session=False,
        )
        db.commit()
        (job_dir / "stt_manifest.json").write_text(
            json.dumps(
                {
                    "duration_seconds": duration_seconds,
                    "chunk_seconds": STT_CHUNK_SECONDS,
                    "planned_chunks": len(plan),
                    "parallel_chunks": worker_count,
                    "elapsed_seconds": transcription_elapsed_seconds,
                    "completed_parts": len(parts),
                    "task_ids": list(dict.fromkeys(task_ids)),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info(
            "recorder STT completed record_id=%s duration_seconds=%.3f planned_chunks=%s "
            "completed_parts=%s task_count=%s parallel_chunks=%s elapsed_seconds=%.3f",
            stored_record_id,
            duration_seconds,
            total_chunks,
            len(parts),
            len(list(dict.fromkeys(task_ids))),
            worker_count,
            transcription_elapsed_seconds,
        )
        return text, segments, task_reference
    finally:
        db.close()


async def _process(record_id: int, request: Request, installation_id: str) -> None:
    try:
        async with background_heavy_slot("recorder_transcription"):
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
                row.error_message = (
                    "当前转写任务较多，排队已满，请稍后点击重新处理"
                    if isinstance(exc, WorkloadQueueFull)
                    else str(exc)[:2000]
                )
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
    user_id = int(current_user.id)
    db.commit()
    target_dir = DATA_ROOT / str(user_id) / uuid.uuid4().hex
    await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)
    target = target_dir / name
    size = 0
    out = await asyncio.to_thread(target.open, "wb")
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_AUDIO_BYTES:
                raise HTTPException(status_code=413, detail="录音文件不能超过 200MB")
            await asyncio.to_thread(out.write, chunk)
    except BaseException:
        await asyncio.to_thread(out.close)
        await asyncio.to_thread(shutil.rmtree, target_dir, ignore_errors=True)
        raise
    else:
        await asyncio.to_thread(out.close)
    if size <= 0:
        await asyncio.to_thread(shutil.rmtree, target_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="音频文件为空")
    row = _new_audio_record(
        db,
        user_id=user_id,
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
    filename = Path(str(source.get("filename") or row.filename or "memory-audio.wav")).name[:255]
    source_name = row.title or "记忆文件"
    # The source download can be slow; do not retain the lookup transaction.
    db.commit()
    try:
        audio_data = await _download_media_url(source_url, max_bytes=MAX_AUDIO_BYTES)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"原始音频下载失败：{str(exc)[:300]}") from exc
    if not audio_data:
        raise HTTPException(status_code=410, detail="原始音频为空，请重新上传")
    if Path(filename).suffix.lower() not in UPLOAD_AUDIO_SUFFIXES:
        filename = f"{Path(filename).stem or 'memory-audio'}.wav"
    target_dir = DATA_ROOT / str(current_user.id) / uuid.uuid4().hex
    target = target_dir / filename
    await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(target.write_bytes, audio_data)
    record = _new_audio_record(
        db,
        user_id=current_user.id,
        name=filename,
        source=target,
        size=len(audio_data),
        source_type="memory",
        source_name=source_name,
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
    source_id = (body.speaker_id or "").strip()
    display_name = body.display_name.strip()
    segments = [dict(item) for item in (row.transcript_segments or []) if isinstance(item, dict)]
    matched = 0
    for item in segments:
        item_id = str(item.get("speaker_id") or "").strip()
        if source_id:
            matches = item_id == source_id
        else:
            matches = item_id == "" and str(item.get("speaker") or "").strip() == source_name
        if matches:
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
