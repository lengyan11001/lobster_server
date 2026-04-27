"""系统日志只读接口：GET /api/logs 返回 lobster/logs/app.log 末尾内容，供「日志」Tab 查看。"""
import asyncio
import json
import logging
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse

from .auth import get_current_user
from ..models import User

router = APIRouter()
logger = logging.getLogger(__name__)

# lobster 项目根目录（与 run.py 中 _root 一致，即含 backend 的目录）
_BASE = Path(__file__).resolve().parent.parent.parent.parent
_LOG_FILE = (_BASE / "logs" / "app.log").resolve()
_DIAGNOSTICS_DIR = (_BASE / "diagnostics_uploads").resolve()
_MAX_LINES = 5000
_DEFAULT_TAIL = 2000
_MAX_DIAGNOSTIC_BYTES = 30 * 1024 * 1024


def _safe_filename(name: str) -> str:
    raw = Path(name or "lobster-diagnostics.zip").name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return (safe or "lobster-diagnostics.zip")[:140]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_log_tail(path: Path, tail: int) -> tuple[str, int]:
    """同步读文件最后 tail 行，在 executor 中调用避免阻塞。返回 (文本, 总行数)。"""
    if not path.exists():
        return "", 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return "", 0
    n = len(lines)
    if n > tail:
        lines = lines[-tail:]
    return "".join(lines), n


@router.get("/api/logs", summary="读取系统日志（末尾 N 行）")
async def get_logs(
    tail: int = Query(default=_DEFAULT_TAIL, ge=100, le=_MAX_LINES, description="返回最后 N 行"),
    current_user: User = Depends(get_current_user),
):
    """返回 lobster/logs/app.log 最后 tail 行，用于前端「日志」Tab 或排错。"""
    logger.info("[日志] GET /api/logs tail=%s path=%s exists=%s", tail, _LOG_FILE, _LOG_FILE.exists())
    if not _LOG_FILE.exists():
        logger.warning("[日志] 文件不存在: %s", _LOG_FILE)
        return PlainTextResponse(
            f"日志文件不存在: {_LOG_FILE}\n请确认已用 start.bat 或 run_backend 启动过至少一次。",
            status_code=404,
        )
    loop = asyncio.get_event_loop()
    text, total = await loop.run_in_executor(None, _read_log_tail, _LOG_FILE, tail)
    lines_returned = len(text.splitlines())
    logger.info("[日志] 返回 lines=%s total=%s", lines_returned, total)
    return PlainTextResponse(
        text if text else "(空)",
        media_type="text/plain; charset=utf-8",
        headers={"X-Log-Lines": str(lines_returned), "X-Log-Total-Lines": str(total)},
    )


@router.post("/api/diagnostics/upload", summary="Upload a redacted client diagnostic bundle")
async def upload_diagnostics_bundle(
    file: UploadFile = File(...),
    client_info: str = Form(default=""),
    current_user: User = Depends(get_current_user),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="diagnostic file is empty")
    if len(data) > _MAX_DIAGNOSTIC_BYTES:
        raise HTTPException(status_code=413, detail="diagnostic file is too large")

    diagnostic_id = f"diag_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"
    safe_name = _safe_filename(file.filename or f"{diagnostic_id}.zip")
    user_dir = (_DIAGNOSTICS_DIR / f"user_{current_user.id}" / diagnostic_id).resolve()
    user_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = user_dir / safe_name
    bundle_path.write_bytes(data)

    parsed_client_info = None
    if client_info:
        try:
            parsed_client_info = json.loads(client_info)
        except Exception:
            parsed_client_info = client_info[:4000]

    metadata = {
        "diagnostic_id": diagnostic_id,
        "user_id": current_user.id,
        "filename": safe_name,
        "size": len(data),
        "content_type": file.content_type,
        "uploaded_at": _utc_iso(),
        "client_info": parsed_client_info,
    }
    (user_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "[diagnostics] uploaded id=%s user_id=%s filename=%s size=%s",
        diagnostic_id,
        current_user.id,
        safe_name,
        len(data),
    )
    return {
        "ok": True,
        "diagnostic_id": diagnostic_id,
        "filename": safe_name,
        "size": len(data),
        "uploaded_at": metadata["uploaded_at"],
    }


@router.get("/api/diagnostics/uploads", summary="List current user's uploaded diagnostics")
async def list_diagnostics_uploads(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    base = (_DIAGNOSTICS_DIR / f"user_{current_user.id}").resolve()
    if not base.exists():
        return {"items": []}
    items = []
    for meta_path in base.glob("diag_*/metadata.json"):
        try:
            item = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append(item)
    items.sort(key=lambda x: str(x.get("uploaded_at") or ""), reverse=True)
    return {"items": items[:limit]}
