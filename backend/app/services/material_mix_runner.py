"""服务端执行「素材混剪」(shanjian_material_mix)。

只处理 progress.executor == "server" 的 run —— 由 services/exec_routing 保证：
设备在线一律走客户端，只有白名单能力 + 无在线设备才会标记成 server。
开关：LOBSTER_SERVER_EXEC_ACTIONS 含 shanjian_material_mix 才启动本循环。
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from ..db import SessionLocal
from ..models import Asset, ScheduledTaskRun, User
from .exec_routing import is_server_exec_enabled

logger = logging.getLogger(__name__)

ACTION = "shanjian_material_mix"
_TICK_SECONDS = 5
_POLL_SECONDS = 10
_MAX_WAIT_SECONDS = 30 * 60
_EXECUTOR_TAG = "server"


def _internal_base() -> str:
    return (os.environ.get("LOBSTER_INTERNAL_API_BASE") or "").strip().rstrip("/") or "http://127.0.0.1:8000"


def _user_token(user: User) -> str:
    from ..api.auth import access_token_claims, create_access_token

    return create_access_token(access_token_claims(user))


def _asset_url(db: Any, asset_id: str) -> str:
    key = str(asset_id or "").strip()
    if not key:
        return ""
    try:
        row = db.query(Asset).filter(Asset.asset_id == key).first()
        return str(getattr(row, "source_url", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _pick(params: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _mix_materials(db: Any, params: Dict[str, Any]) -> List[Dict[str, str]]:
    raw = params.get("materials")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or item.get("media_type") or "").strip().lower()
        url = str(item.get("fileUrl") or item.get("file_url") or item.get("url") or "").strip()
        if not url:
            url = _asset_url(db, str(item.get("asset_id") or ""))
        if not url or kind not in {"image", "video"}:
            continue
        out.append({"type": kind, "fileUrl": url, "soundSwitch": bool(item.get("soundSwitch") or item.get("sound_switch"))})
    return out


def build_mix_body(db: Any, run: ScheduledTaskRun) -> Dict[str, Any]:
    """把 run.payload 映射成 /api/shanjian-smart-clip/submit 的请求体。"""
    payload = run.payload if isinstance(run.payload, dict) else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    style_id = _pick(params, "style_id", "template_style_id", "styleId")
    if not style_id:
        raise RuntimeError("素材混剪缺少模板 style_id（先在个人IP设置里选好模板）")

    audio_url = _pick(params, "audio_url", "audioUrl")
    if not audio_url:
        audio_url = _asset_url(db, _pick(params, "audio_asset_id", "audio_assetId"))
    content = _pick(params, "content", "script", "text", "prompt")
    speaker = _pick(params, "voice", "speaker_id", "speakerId")

    if not audio_url and not (content and speaker):
        raise RuntimeError("素材混剪缺少驱动：既没有音频，也没有文案+声音")

    body: Dict[str, Any] = {
        "scene": "oralMixCutting",
        "style_id": style_id,
        "title": str(run.title or params.get("title") or "素材混剪")[:80],
        "language": _pick(params, "language") or "zh-CN",
        "materials": _mix_materials(db, params),
        "material_sound_switch": bool(params.get("material_sound_switch")),
        "header_switch": bool(params.get("header_switch", True)),
        "material_switch": bool(params.get("material_switch", True)),
        "subtitle_switch": bool(params.get("subtitle_switch", True)),
        "keyword_switch": bool(params.get("keyword_switch", True)),
        "watermark_show": bool(params.get("watermark_show", False)),
        "material_match_way": _pick(params, "material_match_way") or "fuzzyMatch",
        "resource_preprocess_method": _pick(params, "resource_preprocess_method") or "roughCut",
        "material_composition": _pick(params, "material_composition") or "random",
        "introduce_name": _pick(params, "introduce_name"),
        "introduce_description": _pick(params, "introduce_description"),
    }
    if audio_url:
        body["audio_url"] = audio_url
    else:
        body["content"] = content
        body["speaker_id"] = speaker
    return body


def _set_progress(db: Any, run: ScheduledTaskRun, **fields: Any) -> None:
    progress = dict(run.progress or {}) if isinstance(run.progress, dict) else {}
    progress.update(fields)
    run.progress = progress
    run.updated_at = datetime.utcnow()
    db.add(run)
    db.commit()


async def _execute_run(db: Any, run: ScheduledTaskRun) -> None:
    user = db.query(User).filter(User.id == int(run.user_id)).first()
    if user is None:
        run.status = "failed"
        run.error = "服务端执行失败：找不到账号"
        db.commit()
        return
    headers = {"Authorization": "Bearer " + _user_token(user), "Content-Type": "application/json"}
    base = _internal_base()
    try:
        body = build_mix_body(db, run)
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = str(exc)[:500]
        _set_progress(db, run, stage="prepare_failed", text=str(exc)[:200])
        return

    _set_progress(db, run, stage="submitted", text="已提交闪剪混剪")
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        resp = await client.post(f"{base}/api/shanjian-smart-clip/submit", json=body, headers=headers)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or not data.get("task_id"):
            run.status = "failed"
            run.error = ("提交闪剪失败：" + str((data or {}).get("detail") or resp.text[:200]))[:500]
            _set_progress(db, run, stage="submit_failed")
            return
        task_id = str(data["task_id"])
        _set_progress(db, run, stage="processing", upstream_task_id=task_id)

        waited = 0
        while waited < _MAX_WAIT_SECONDS:
            await asyncio.sleep(_POLL_SECONDS)
            waited += _POLL_SECONDS
            try:
                info = await client.post(
                    f"{base}/api/shanjian-smart-clip/task",
                    json={"task_id": task_id},
                    headers=headers,
                )
                payload_info = info.json() if info.content else {}
            except Exception as exc:  # noqa: BLE001
                logger.info("[mix-runner] poll error run=%s err=%s", run.id, exc)
                _set_progress(db, run, text="等待闪剪返回…")
                continue
            status = str((payload_info or {}).get("status") or "").lower()
            if status == "succeed":
                video_url = str((payload_info or {}).get("video_url") or "")
                cover_url = str((payload_info or {}).get("cover_url") or "")
                run.status = "completed"
                run.result_text = "素材混剪完成"
                run.result_payload = {
                    "video_url": video_url,
                    "cover_url": cover_url,
                    "duration": (payload_info or {}).get("duration"),
                    "upstream_task_id": task_id,
                    "executor": _EXECUTOR_TAG,
                }
                run.finished_at = datetime.utcnow()
                _set_progress(db, run, stage="completed", text="已完成")
                try:
                    from ..api.canvas_hub import register_content_record

                    register_content_record(
                        db,
                        int(run.user_id),
                        video_url,
                        media_type="video",
                        title=str(run.title or "素材混剪"),
                        task_id=task_id,
                        model="shanjian-mixcut",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.info("[mix-runner] register content record skipped: %s", exc)
                return
            if status == "failed":
                run.status = "failed"
                run.error = str((payload_info or {}).get("message") or "闪剪返回失败")[:500]
                run.finished_at = datetime.utcnow()
                _set_progress(db, run, stage="failed", text=run.error[:200])
                return
            _set_progress(db, run, text="闪剪处理中…")

    run.status = "failed"
    run.error = "素材混剪超时未完成（30 分钟）"
    run.finished_at = datetime.utcnow()
    _set_progress(db, run, stage="timeout")


async def run_once() -> int:
    """领一条 executor=server 的素材混剪任务并执行；没有就返回 0。"""
    if not is_server_exec_enabled(ACTION):
        return 0
    db = SessionLocal()
    try:
        rows = (
            db.query(ScheduledTaskRun)
            .filter(
                ScheduledTaskRun.status == "pending",
                ScheduledTaskRun.task_kind == "client_workflow",
            )
            .order_by(ScheduledTaskRun.created_at.asc())
            .limit(20)
            .all()
        )
        target: Optional[ScheduledTaskRun] = None
        for row in rows:
            progress = row.progress if isinstance(row.progress, dict) else {}
            payload = row.payload if isinstance(row.payload, dict) else {}
            if str(progress.get("executor") or "") != _EXECUTOR_TAG:
                continue
            if str(payload.get("action") or "") != ACTION:
                continue
            target = row
            break
        if target is None:
            return 0
        now = datetime.utcnow()
        target.status = "processing"
        target.claimed_by_installation_id = _EXECUTOR_TAG
        target.claimed_at = now
        target.started_at = now
        target.updated_at = now
        _set_progress(db, target, stage="claimed", text="服务端开始执行")
        await _execute_run(db, target)
        return 1
    finally:
        db.close()


async def background_loop() -> None:
    logger.info("[mix-runner] 服务端素材混剪执行器已启动（每 %ss 检查一次）", _TICK_SECONDS)
    while True:
        try:
            await run_once()
        except Exception:  # noqa: BLE001
            logger.exception("[mix-runner] 循环异常")
        await asyncio.sleep(_TICK_SECONDS)
