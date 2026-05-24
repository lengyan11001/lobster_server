from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Asset, User, UserWanRoleTask
from ..services.credit_ledger import append_credit_ledger
from ..services.credits_amount import credits_json_float, quantize_credits, user_balance_decimal
from .assets import _save_bytes_or_tos
from .auth import get_current_user

router = APIRouter()

_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com"
_WAN_ENDPOINT = "/api/v1/services/aigc/image2video/video-synthesis"
_TASK_STATUS_SUCCESS = {"succeeded", "success", "completed"}
_TASK_STATUS_FAILED = {"failed", "error", "canceled", "cancelled"}
_WAN_ROLE_MAX_VIDEO_SECONDS = 30
_WAN_ROLE_STD_YUAN_PER_SECOND = Decimal("0.6")
_WAN_ROLE_CREDITS_PER_YUAN = Decimal("100")
_WAN_ROLE_PRICE_MULTIPLIER = Decimal("1.5")
_WAN_ROLE_STD_CREDITS_PER_SECOND = quantize_credits(
    _WAN_ROLE_STD_YUAN_PER_SECOND * _WAN_ROLE_CREDITS_PER_YUAN * _WAN_ROLE_PRICE_MULTIPLIER
)


class WanRoleCreateBody(BaseModel):
    task_type: str = Field("move", description="move=动作迁移, mix=角色替换")
    image_url: str = Field(..., min_length=8)
    video_url: str = Field(..., min_length=8)
    title: str = ""
    mode: str = "wan-std"
    watermark: bool = True
    video_duration: float = 0


def _dashscope_key() -> str:
    key = (os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("ALIYUN_DASHSCOPE_API_KEY") or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="服务端未配置 DASHSCOPE_API_KEY")
    return key


def _dashscope_base() -> str:
    return (os.environ.get("DASHSCOPE_BASE_URL") or _DASHSCOPE_BASE_URL).strip().rstrip("/")


def _task_type_model(task_type: str) -> tuple[str, str]:
    value = (task_type or "move").strip().lower()
    if value in {"move", "animate_move", "action", "action_transfer"}:
        return "move", "wan2.2-animate-move"
    if value in {"mix", "replace", "role_replace", "animate_mix"}:
        return "mix", "wan2.2-animate-mix"
    raise HTTPException(status_code=400, detail="不支持的复刻类型")


def _safe_title(title: str, task_type: str) -> str:
    raw = " ".join(str(title or "").split())[:128]
    if raw:
        return raw
    return "动作迁移" if task_type == "move" else "角色替换"


def _status_label(status: str) -> str:
    return {
        "processing": "生成中",
        "success": "已完成",
        "failed": "失败",
    }.get(status or "", "生成中")


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _ceil_seconds(value: Any) -> int:
    try:
        seconds = Decimal(str(value or 0))
    except Exception:
        seconds = Decimal(0)
    if seconds <= 0:
        return 0
    return int(seconds.to_integral_value(rounding=ROUND_CEILING))


def _wan_role_estimate_billing(duration_seconds: Any, mode: str = "wan-std") -> Dict[str, Any]:
    billable_seconds = _ceil_seconds(duration_seconds)
    if billable_seconds <= 0:
        billable_seconds = _WAN_ROLE_MAX_VIDEO_SECONDS
    if billable_seconds > _WAN_ROLE_MAX_VIDEO_SECONDS:
        raise HTTPException(status_code=400, detail=f"参考视频最长支持 {_WAN_ROLE_MAX_VIDEO_SECONDS} 秒，请裁剪后再生成")
    normalized_mode = (mode or "wan-std").strip() or "wan-std"
    credits_per_second = _WAN_ROLE_STD_CREDITS_PER_SECOND
    expected_credits = quantize_credits(Decimal(billable_seconds) * credits_per_second)
    return {
        "billing_status": "pre_deducted",
        "mode": normalized_mode,
        "billable_seconds": billable_seconds,
        "max_video_seconds": _WAN_ROLE_MAX_VIDEO_SECONDS,
        "credits_per_second": credits_json_float(credits_per_second),
        "expected_credits": credits_json_float(expected_credits),
        "price_yuan_per_second": float(_WAN_ROLE_STD_YUAN_PER_SECOND),
        "credits_per_yuan": float(_WAN_ROLE_CREDITS_PER_YUAN),
        "price_multiplier": float(_WAN_ROLE_PRICE_MULTIPLIER),
    }


def _pre_deduct_wan_role_credits(current_user: User, billing: Dict[str, Any], db: Session) -> Decimal:
    credits = quantize_credits(billing.get("expected_credits") or 0)
    if credits <= 0:
        return credits
    bal = user_balance_decimal(current_user)
    if bal < credits:
        raise HTTPException(status_code=402, detail=f"算力不足：本次预计消耗 {credits_json_float(credits)} 算力，当前余额 {credits_json_float(bal)}。请充值后重试。")
    current_user.credits = quantize_credits(bal - credits)
    bal_after = quantize_credits(current_user.credits)
    append_credit_ledger(
        db,
        current_user.id,
        -credits,
        "pre_deduct",
        bal_after,
        description="AI角色替换/动作迁移预扣",
        ref_type="wan_role_task",
        ref_id="pending",
        meta={"billing": billing},
    )
    return credits


def _refund_create_failure(current_user: User, credits: Decimal, billing: Dict[str, Any], db: Session, *, reason: str, detail: Any) -> None:
    if credits <= 0:
        return
    current_user.credits = quantize_credits(user_balance_decimal(current_user) + credits)
    append_credit_ledger(
        db,
        current_user.id,
        credits,
        "refund",
        quantize_credits(current_user.credits),
        description="AI角色替换/动作迁移创建失败退款",
        ref_type="wan_role_task",
        ref_id="create_failed",
        meta={"billing": billing, "reason": reason, "detail": str(detail)[:1000]},
    )
    db.commit()


def _refund_wan_role_if_needed(row: UserWanRoleTask, db: Session) -> None:
    meta = dict(row.meta or {})
    billing = dict(meta.get("billing") or {})
    if billing.get("billing_status") != "pre_deducted":
        return
    credits = quantize_credits(billing.get("credits_pre_deducted") or billing.get("expected_credits") or 0)
    if credits <= 0:
        billing["billing_status"] = "refunded"
        meta["billing"] = billing
        row.meta = meta
        return
    user = db.query(User).filter(User.id == row.user_id).first()
    if not user:
        return
    user.credits = quantize_credits(user_balance_decimal(user) + credits)
    bal_after = quantize_credits(user.credits)
    append_credit_ledger(
        db,
        row.user_id,
        credits,
        "refund",
        bal_after,
        description="AI角色替换/动作迁移失败退款",
        ref_type="wan_role_task",
        ref_id=str(row.id),
        meta={"billing": billing, "dashscope_task_id": row.dashscope_task_id},
    )
    billing.update({
        "billing_status": "refunded",
        "credits_refunded": credits_json_float(credits),
        "refunded_at": datetime.utcnow().isoformat() + "Z",
    })
    meta["billing"] = billing
    row.meta = meta


def _guess_ext(content_type: str, fallback: str = ".mp4") -> str:
    ct = (content_type or "").lower()
    if "webm" in ct:
        return ".webm"
    if "quicktime" in ct or "mov" in ct:
        return ".mov"
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "mp4" in ct:
        return ".mp4"
    return fallback


def _probe_video_duration_seconds(data: bytes, ext: str = ".mp4") -> float:
    if not data:
        return 0
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0
    suffix = ext if ext.startswith(".") else f".{ext}"
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as fh:
            fh.write(data)
            tmp_path = fh.name
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", tmp_path],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return 0
        return max(0.0, float((proc.stdout or "0").strip() or 0))
    except Exception:
        return 0
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def _serialize_task(row: UserWanRoleTask) -> Dict[str, Any]:
    playable = row.asset_video_url or row.source_video_url or ""
    return {
        "id": row.id,
        "title": row.title,
        "task_type": row.task_type,
        "task_type_label": "动作迁移" if row.task_type == "move" else "角色替换",
        "model": row.model,
        "mode": row.mode,
        "status": row.status,
        "status_label": _status_label(row.status),
        "is_processing": row.status == "processing",
        "is_success": row.status == "success",
        "is_failed": row.status == "failed",
        "dashscope_task_id": row.dashscope_task_id,
        "image_url": row.image_url,
        "video_url": row.video_url,
        "source_video_url": row.source_video_url or "",
        "asset_video_url": row.asset_video_url or "",
        "video_result_url": playable,
        "playable_url": playable,
        "asset_id": row.asset_id or "",
        "error_message": row.error_message or "",
        "meta": row.meta or {},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


async def _download_bytes(url: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=600.0, follow_redirects=True, trust_env=False) as client:
        resp = await client.get(url)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"下载结果视频失败 HTTP {resp.status_code}")
    return resp.content, (resp.headers.get("content-type") or "video/mp4").split(";", 1)[0].strip() or "video/mp4"


def _extract_result_video_url(data: Dict[str, Any]) -> str:
    output = data.get("output") if isinstance(data.get("output"), dict) else {}
    for key in ("video_url", "url"):
        val = output.get(key)
        if isinstance(val, str) and val.startswith(("http://", "https://")):
            return val
    results = output.get("results")
    if isinstance(results, dict):
        for key in ("video_url", "url"):
            val = results.get(key)
            if isinstance(val, str) and val.startswith(("http://", "https://")):
                return val
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            for key in ("video_url", "url"):
                val = item.get(key)
                if isinstance(val, str) and val.startswith(("http://", "https://")):
                    return val
    return ""


async def _persist_result_video(row: UserWanRoleTask, source_url: str, db: Session) -> None:
    if not source_url or row.asset_video_url:
        return
    data, content_type = await _download_bytes(source_url)
    ext = _guess_ext(content_type, ".mp4")
    asset_id, filename_or_key, file_size, public_url = _save_bytes_or_tos(data, ext, content_type)
    if not public_url:
        raise HTTPException(status_code=503, detail="结果视频转存失败：服务端 TOS 未返回公网链接")
    asset = Asset(
        asset_id=asset_id,
        user_id=row.user_id,
        filename=filename_or_key,
        media_type="video",
        file_size=file_size,
        source_url=public_url,
        prompt=row.title,
        model=row.model,
        tags="aliyun,wan,role_transfer",
        meta={
            "wan_role_task_id": row.id,
            "dashscope_task_id": row.dashscope_task_id,
            "task_type": row.task_type,
            "mode": row.mode,
            "image_url": row.image_url,
            "video_url": row.video_url,
        },
    )
    db.add(asset)
    db.flush()
    row.asset_id = asset_id
    row.asset_video_url = public_url


async def _query_dashscope_task(task_id: str) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {_dashscope_key()}"}
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        resp = await client.get(f"{_dashscope_base()}/api/v1/tasks/{task_id}", headers=headers)
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=str(data.get("message") or data.get("error") or resp.text)[:500])
    return data


async def _refresh_task(row: UserWanRoleTask, db: Session) -> UserWanRoleTask:
    if row.status != "processing":
        return row
    data = await _query_dashscope_task(row.dashscope_task_id)
    output = data.get("output") if isinstance(data.get("output"), dict) else {}
    raw_status = str(output.get("task_status") or data.get("task_status") or data.get("status") or "").strip().lower()
    meta = dict(row.meta or {})
    meta["last_dashscope_response"] = data
    row.meta = meta
    row.updated_at = datetime.utcnow()
    if raw_status in _TASK_STATUS_FAILED:
        row.status = "failed"
        row.error_message = str(output.get("message") or data.get("message") or data.get("code") or "生成失败")[:1000]
        _refund_wan_role_if_needed(row, db)
        return row
    video_url = _extract_result_video_url(data)
    if raw_status in _TASK_STATUS_SUCCESS or video_url:
        if not video_url:
            row.status = "failed"
            row.error_message = "阿里任务成功但未返回视频地址"
            _refund_wan_role_if_needed(row, db)
            return row
        row.source_video_url = video_url
        await _persist_result_video(row, video_url, db)
        row.status = "success"
        row.error_message = None
    return row


@router.post("/api/wan/role-transfer/upload")
async def upload_role_transfer_asset(
    file: UploadFile = File(...),
    media_type: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    name = file.filename or "upload"
    ext = Path(name).suffix.lower() or ".bin"
    media_type_value = (media_type or "").strip().lower()
    if media_type_value not in {"image", "video"}:
        media_type_value = "video" if ext in {".mp4", ".webm", ".mov", ".avi"} else "image"
    if ext == ".bin":
        ext = ".mp4" if media_type_value == "video" else ".jpg"
    content_type = getattr(file, "content_type", "") or ("video/mp4" if media_type_value == "video" else "image/jpeg")
    duration = _probe_video_duration_seconds(data, ext) if media_type_value == "video" else 0
    if duration and duration > _WAN_ROLE_MAX_VIDEO_SECONDS + 0.2:
        raise HTTPException(status_code=400, detail=f"视频最长支持 {_WAN_ROLE_MAX_VIDEO_SECONDS} 秒，请裁剪后再上传")
    asset_id, filename_or_key, file_size, public_url = _save_bytes_or_tos(data, ext, content_type)
    if not public_url:
        raise HTTPException(status_code=503, detail="上传失败：服务端 TOS 未返回公网链接")
    asset = Asset(
        asset_id=asset_id,
        user_id=current_user.id,
        filename=filename_or_key,
        media_type=media_type_value,
        file_size=file_size,
        source_url=public_url,
        tags="aliyun,wan,role_transfer,input",
    )
    db.add(asset)
    db.commit()
    return {
        "ok": True,
        "asset_id": asset_id,
        "filename": filename_or_key,
        "media_type": media_type_value,
        "file_size": file_size,
        "duration": duration,
        "source_url": public_url,
        "url": public_url,
    }


@router.post("/api/wan/role-transfer/tasks")
async def create_role_transfer_task(
    body: WanRoleCreateBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task_type, model = _task_type_model(body.task_type)
    mode = (body.mode or "wan-std").strip()
    if mode not in {"wan-std", "wan-pro"}:
        raise HTTPException(status_code=400, detail="mode 仅支持 wan-std 或 wan-pro")
    billing = _wan_role_estimate_billing(body.video_duration, mode)
    pre_deducted = _pre_deduct_wan_role_credits(current_user, billing, db)
    billing["credits_pre_deducted"] = credits_json_float(pre_deducted)
    payload = {
        "model": model,
        "input": {
            "image_url": body.image_url.strip(),
            "video_url": body.video_url.strip(),
            "watermark": bool(body.watermark),
        },
        "parameters": {"mode": mode},
    }
    headers = {
        "Authorization": f"Bearer {_dashscope_key()}",
        "X-DashScope-Async": "enable",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
            resp = await client.post(f"{_dashscope_base()}{_WAN_ENDPOINT}", json=payload, headers=headers)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            _refund_create_failure(current_user, pre_deducted, billing, db, reason="dashscope_http_error", detail=data)
            raise HTTPException(status_code=502, detail=str(data.get("message") or data.get("error") or resp.text)[:500])
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        task_id = str(output.get("task_id") or data.get("task_id") or "").strip()
        if not task_id:
            _refund_create_failure(current_user, pre_deducted, billing, db, reason="missing_task_id", detail=data)
            raise HTTPException(status_code=502, detail="阿里百炼未返回 task_id")
    except HTTPException:
        raise
    except Exception as exc:
        _refund_create_failure(current_user, pre_deducted, billing, db, reason="request_exception", detail=exc)
        raise HTTPException(status_code=502, detail=f"阿里百炼请求失败: {exc}") from exc
    row = UserWanRoleTask(
        user_id=current_user.id,
        title=_safe_title(body.title, task_type),
        task_type=task_type,
        model=model,
        mode=mode,
        status="processing",
        dashscope_task_id=task_id,
        request_id=str(data.get("request_id") or "").strip() or None,
        image_url=body.image_url.strip(),
        video_url=body.video_url.strip(),
        meta={"create_response": data, "billing": billing},
    )
    db.add(row)
    db.flush()
    append_credit_ledger(
        db,
        current_user.id,
        0,
        "settle",
        user_balance_decimal(current_user),
        description="AI角色替换/动作迁移任务已创建",
        ref_type="wan_role_task",
        ref_id=str(row.id),
        meta={"billing": billing, "dashscope_task_id": task_id},
    )
    db.commit()
    db.refresh(row)
    return {"ok": True, "billing": billing, "item": _serialize_task(row)}


@router.get("/api/wan/role-transfer/tasks")
async def list_role_transfer_tasks(
    page: int = 1,
    size: int = 30,
    refresh: bool = True,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    page = max(1, int(page or 1))
    size = max(1, min(int(size or 30), 80))
    rows = (
        db.query(UserWanRoleTask)
        .filter(UserWanRoleTask.user_id == current_user.id)
        .order_by(UserWanRoleTask.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    if refresh:
        changed = False
        for row in rows:
            if row.status != "processing":
                continue
            await _refresh_task(row, db)
            changed = True
        if changed:
            db.commit()
    return {"ok": True, "items": [_serialize_task(r) for r in rows], "page": page, "size": size}


@router.get("/api/wan/role-transfer/tasks/{task_id}")
async def get_role_transfer_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(UserWanRoleTask)
        .filter(UserWanRoleTask.id == task_id, UserWanRoleTask.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    if row.status == "processing":
        await _refresh_task(row, db)
        db.commit()
        db.refresh(row)
    return {"ok": True, "item": _serialize_task(row)}


@router.delete("/api/wan/role-transfer/tasks/{task_id}")
async def delete_role_transfer_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(UserWanRoleTask)
        .filter(UserWanRoleTask.id == task_id, UserWanRoleTask.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted": True, "id": task_id}
