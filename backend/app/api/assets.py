"""Asset management: download, store, list, search local media files. 支持 TOS 上传后仅存公网 URL."""
import asyncio
import hmac
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import partial, wraps
from pathlib import Path
from typing import Any, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from .auth import get_current_user
from .mobile_identity import online_user_for_mobile_user
from ..core.config import settings
from ..db import get_db
from ..models import (
    Asset,
    GenerationRecord,
    H5ChatDevicePresence,
    H5ChatEvent,
    H5ChatMessage,
    IPContentDraftRecord,
    ScheduledTaskRun,
    User,
)
from ..services.device_presence import is_device_online
from ..services.h5_chat_sessions import attach_system_task_message

logger = logging.getLogger(__name__)
router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
ASSETS_DIR = _BASE_DIR / "assets"
ASSETS_DIR.mkdir(exist_ok=True)
TEMP_ASSETS_DIR = _BASE_DIR / "temp_assets"  # 临时文件目录（用于无TOS时的中转）
TEMP_ASSETS_DIR.mkdir(exist_ok=True)
_CUSTOM_CONFIGS_FILE = _BASE_DIR / "custom_configs.json"

# 带签名的临时访问：用于会话里上传的图/视频生成可被速推拉取的 URL
_ASSET_FILE_EXPIRY_SEC = 86400  # 24 hours
_VIDEO_SEGMENT_SECONDS = 3
_VIDEO_SEGMENT_MAX_COUNT = 120
_H5_CLIENT_COMMAND_PREFIX = "__LOBSTER_H5_CLIENT_COMMAND__"
_ASSET_UPLOAD_IO_WORKERS = max(1, int(os.environ.get("ASSET_UPLOAD_IO_WORKERS") or "8"))
_ASSET_UPLOAD_MAX_BYTES = max(
    16 * 1024 * 1024,
    int(os.environ.get("ASSET_UPLOAD_MAX_BYTES") or str(1024 * 1024 * 1024)),
)
_ASSET_UPLOAD_IN_MEMORY_BYTES = 32 * 1024 * 1024
_asset_upload_executor = ThreadPoolExecutor(
    max_workers=_ASSET_UPLOAD_IO_WORKERS,
    thread_name_prefix="asset-upload",
)
_asset_upload_gate = asyncio.Semaphore(_ASSET_UPLOAD_IO_WORKERS)
_asset_upload_request_gate = asyncio.Semaphore(max(_ASSET_UPLOAD_IO_WORKERS * 2, 8))

# 临时文件跟踪：task_id -> [temp_file_paths]，用于任务完成后清理
_temp_files_by_task: dict[str, list[Path]] = {}


def ensure_asset_library_indexes(bind) -> None:
    """Create indexes needed by paged H5 asset-library queries on existing databases."""
    try:
        with bind.begin() as connection:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_assets_user_created ON assets (user_id, created_at)"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("asset library index migration skipped: %s", exc)


def _get_tos_config() -> Optional[dict]:
    """从 custom_configs.json 读取 TOS_CONFIG，用于上传到 TOS 并得到公网 URL。"""
    if not _CUSTOM_CONFIGS_FILE.exists():
        return None
    try:
        data = json.loads(_CUSTOM_CONFIGS_FILE.read_text(encoding="utf-8"))
        cfg = (data.get("configs") or {}).get("TOS_CONFIG")
        if isinstance(cfg, dict) and cfg.get("access_key") and cfg.get("secret_key"):
            return cfg
    except Exception as e:
        logger.debug("[TOS] 读取 TOS_CONFIG 失败: %s", e)
    return None


def _tos_object_headers(content_type: str, object_key: str) -> tuple[str, str]:
    ct = (content_type or "").strip() or "application/octet-stream"
    ct_lower = ct.lower()
    ext = Path(object_key).suffix.lower()
    inline_exts = {
        ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg",
        ".mp4", ".mov", ".webm", ".m4v",
        ".mp3", ".wav", ".m4a", ".aac", ".ogg",
    }
    if ct_lower.startswith(("image/", "video/", "audio/")) or ext in inline_exts:
        return ct, "inline"
    return ct, "attachment"


def _upload_to_tos(data: bytes, object_key: str, content_type: str) -> Optional[str]:
    """上传字节到 TOS，返回公网可访问 URL；失败返回 None。"""
    cfg = _get_tos_config()
    if not cfg:
        logger.warning("[TOS] 配置未读取到，object_key=%s", object_key)
        return None
    try:
        import tos
        ak = str(cfg.get("access_key", "")).strip()
        sk = str(cfg.get("secret_key", "")).strip()
        endpoint = str(cfg.get("endpoint", "")).strip()
        region = str(cfg.get("region", "")).strip()
        bucket = str(cfg.get("bucket_name", "")).strip()
        public_domain = str(cfg.get("public_domain", "")).strip().rstrip("/")
        if not all([ak, sk, endpoint, region, bucket, public_domain]):
            logger.warning("[TOS] 配置不完整，跳过上传")
            return None
        client = tos.TosClientV2(ak, sk, endpoint, region)
        object_content_type, content_disposition = _tos_object_headers(content_type, object_key)
        try:
            client.put_object(
                bucket,
                object_key,
                content=data,
                content_type=object_content_type,
                content_disposition=content_disposition,
            )
        except TypeError:
            logger.warning("[TOS] SDK does not accept content_disposition; retrying object_key=%s", object_key)
            client.put_object(bucket, object_key, content=data, content_type=object_content_type)
        url = f"{public_domain}/{object_key}"
        logger.info("[TOS] 上传成功 object_key=%s url=%s", object_key, url[:80])
        return url
    except Exception as e:
        logger.exception("[TOS] 上传失败: %s", e)
        return None


def _upload_file_to_tos(file_obj, object_key: str, content_type: str) -> Optional[str]:
    """Upload a seekable file without materializing the whole payload in memory."""
    cfg = _get_tos_config()
    if not cfg:
        return None
    try:
        import tos

        ak = str(cfg.get("access_key", "")).strip()
        sk = str(cfg.get("secret_key", "")).strip()
        endpoint = str(cfg.get("endpoint", "")).strip()
        region = str(cfg.get("region", "")).strip()
        bucket = str(cfg.get("bucket_name", "")).strip()
        public_domain = str(cfg.get("public_domain", "")).strip().rstrip("/")
        if not all([ak, sk, endpoint, region, bucket, public_domain]):
            return None
        client = tos.TosClientV2(ak, sk, endpoint, region)
        object_content_type, content_disposition = _tos_object_headers(content_type, object_key)
        file_obj.seek(0)
        try:
            client.put_object(
                bucket,
                object_key,
                content=file_obj,
                content_type=object_content_type,
                content_disposition=content_disposition,
            )
        except TypeError:
            file_obj.seek(0)
            client.put_object(bucket, object_key, content=file_obj, content_type=object_content_type)
        return f"{public_domain}/{object_key}"
    except Exception as exc:
        logger.exception("[TOS] 流式上传失败 object_key=%s error=%s", object_key, exc)
        return None


def _seekable_file_size(file_obj) -> int:
    file_obj.seek(0, os.SEEK_END)
    size = int(file_obj.tell())
    file_obj.seek(0)
    return size


def _validate_upload_size(file_obj, max_bytes: int = _ASSET_UPLOAD_MAX_BYTES) -> int:
    size = _seekable_file_size(file_obj)
    if size <= 0:
        raise HTTPException(status_code=400, detail="文件为空")
    if size > max_bytes:
        limit_mb = max(1, int(max_bytes / 1024 / 1024))
        raise HTTPException(status_code=413, detail=f"单个素材不能超过 {limit_mb}MB")
    return size


def _copy_upload_file(file_obj, target: Path) -> None:
    file_obj.seek(0)
    with target.open("wb") as output:
        shutil.copyfileobj(file_obj, output, length=1024 * 1024)


def _save_upload_file_or_tos(
    file_obj,
    ext: str,
    content_type: str = "",
) -> Tuple[str, str, int, Optional[str]]:
    size = _validate_upload_size(file_obj)
    if size <= _ASSET_UPLOAD_IN_MEMORY_BYTES:
        file_obj.seek(0)
        data = file_obj.read(size + 1)
        file_obj.seek(0)
        return _save_bytes_or_tos(data, ext, content_type)
    aid = _gen_asset_id()
    object_key = f"assets/{aid}{ext}"
    tos_url = _upload_file_to_tos(file_obj, object_key, content_type or "application/octet-stream")
    if tos_url:
        return aid, object_key, size, tos_url
    filename = f"{aid}{ext}"
    _copy_upload_file(file_obj, ASSETS_DIR / filename)
    return aid, filename, size, None


def _store_temp_upload_file(
    file_obj,
    object_key: str,
    content_type: str,
    fallback_path: Path,
) -> Tuple[int, Optional[str]]:
    size = _validate_upload_size(file_obj)
    tos_url = _upload_file_to_tos(file_obj, object_key, content_type)
    if not tos_url:
        _copy_upload_file(file_obj, fallback_path)
    return size, tos_url


def _delete_tos_object(object_key: str) -> bool:
    key = str(object_key or "").strip().lstrip("/")
    if not key.startswith("assets/"):
        return False
    cfg = _get_tos_config()
    if not cfg:
        return False
    try:
        import tos

        client = tos.TosClientV2(
            str(cfg.get("access_key", "")).strip(),
            str(cfg.get("secret_key", "")).strip(),
            str(cfg.get("endpoint", "")).strip(),
            str(cfg.get("region", "")).strip(),
        )
        client.delete_object(str(cfg.get("bucket_name", "")).strip(), key)
        return True
    except Exception as exc:
        logger.warning("[TOS] 删除中间素材失败 object_key=%s error=%s", key, exc)
        return False


def _asset_file_token(asset_id: str, expiry_ts: int) -> str:
    raw = f"{asset_id}:{expiry_ts}"
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _is_loopback_base(base: str) -> bool:
    if not (base or "").strip():
        return True
    b = (base or "").lower()
    return "127.0.0.1" in b or "localhost" in b or "0.0.0.0" in b


def _resolve_asset_public_base(request: Request) -> str:
    """生成 /api/assets/file 签名链的根：避免误用 127.0.0.1 作多设备预览。"""
    from ..core.config import get_settings

    settings = get_settings()
    port = getattr(settings, "port", 8000)
    pub = (getattr(settings, "public_base_url", None) or "").strip().rstrip("/")
    lan = (getattr(settings, "lan_public_base_url", None) or "").strip().rstrip("/")

    base = ""
    if pub and not _is_loopback_base(pub):
        base = pub
    elif lan and not _is_loopback_base(lan):
        base = lan

    if not base:
        try:
            base = str((request.base_url or "").rstrip("/"))
        except Exception:
            base = ""
    if not base:
        base = f"http://127.0.0.1:{port}"
    if "0.0.0.0" in base:
        host = (request.headers.get("host") or "").strip()
        if host:
            sch = getattr(request.url, "scheme", None) or "http"
            base = f"{sch}://{host}"
        else:
            base = base.replace("0.0.0.0", "127.0.0.1")

    if _is_loopback_base(base) and lan and not _is_loopback_base(lan):
        base = lan
    if _is_loopback_base(base) and pub:
        base = pub

    try:
        base.encode("ascii")
    except UnicodeEncodeError:
        base = f"http://127.0.0.1:{port}"
        logger.warning(
            "[素材] base_url 含非 ASCII，已回退为 127.0.0.1。请在 .env 设置 PUBLIC_BASE_URL 或 LAN_PUBLIC_BASE_URL。"
        )
    return base


def build_asset_file_url(request: Request, asset_id: str) -> Optional[str]:
    """生成带签名的素材文件访问 URL，供注入到对话消息中（速推可拉取）。保证返回纯 ASCII。
    若速推报 Failed to download：说明其服务器无法访问该 URL，请在 .env 设置 PUBLIC_BASE_URL 为
    速推可访问的地址（公网 IP/域名或内网穿透如 ngrok），勿用 localhost/127.0.0.1/仅局域网 IP。"""
    expiry_ts = int(time.time()) + _ASSET_FILE_EXPIRY_SEC
    token = _asset_file_token(asset_id, expiry_ts)
    base = _resolve_asset_public_base(request)
    return f"{base}/api/assets/file/{asset_id}?token={token}&expiry={expiry_ts}"


def get_asset_public_url(
    asset_id: str, user_id: int, request: Request, db: Session
) -> Optional[str]:
    """供速推使用的素材 URL：仅当 DB 中 source_url 为可对外拉取的公网地址时返回；内部地址或缺失则返回 None。
    不再回退到 /api/assets/file/ 签名链（与 lobster_online 一致，避免无效拉图）。"""
    row = db.query(Asset).filter(Asset.asset_id == asset_id, Asset.user_id == user_id).first()
    if row and getattr(row, "source_url", None):
        url = (row.source_url or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            # 检测是否是内部地址（需要转存）
            from urllib.parse import urlparse
            import ipaddress
            is_internal = False
            try:
                parsed = urlparse(url)
                hostname = (parsed.hostname or "").lower()
                # 首先检查明显的内部地址标识
                if not hostname:
                    is_internal = True
                elif hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
                    is_internal = True
                elif "42.194.209.150" in hostname or "bhzn.top" in hostname:
                    is_internal = True
                elif "token=" in url or "?token" in url:
                    # 包含 token 参数，很可能是内部 API
                    is_internal = True
                else:
                    # 尝试解析为 IP 地址，判断是否为内网 IP
                    try:
                        ip = ipaddress.ip_address(hostname)
                        if ip.is_private or ip.is_loopback:
                            is_internal = True
                    except ValueError:
                        # 不是 IP 地址，检查是否是已知的公开 CDN
                        cdn_keywords = ("cdn.", "oss.", "cos.", "tos.", "s3.", "cloudfront.", "fastly.", "cloudflare.", "img.", "static.", "media.", "assets.", "qiniucdn.", "upyun.", "aliyuncs.", "cdn-video.51sux.com")
                        if any(cdn_keyword in hostname for cdn_keyword in cdn_keywords):
                            is_internal = False
                        # 如果不在已知 CDN 列表中，且包含 token，认为是内部地址
                        elif "token=" in url or "?token" in url:
                            is_internal = True
                
                if is_internal:
                    # 内部地址，返回 None，让调用方使用 build_asset_file_url 构建临时 URL，然后由服务器端转存
                    logger.warning("[素材] get_asset_public_url 检测到内部地址，将返回 None 以触发服务器端转存: %s", url[:100])
                    return None
            except Exception as e:
                # 检测失败时，如果 URL 包含明显的内网标识，也认为是内部地址
                logger.debug("[素材] get_asset_public_url 检测内部地址失败: %s", e)
                if "42.194.209.150" in url or "bhzn.top" in url or "token=" in url or "?token" in url:
                    logger.warning("[素材] get_asset_public_url 检测异常但包含内网标识，返回 None: %s", url[:100])
                    return None
            # 只有确认不是内部地址时才返回原始 URL
            return url
    return None


def _gen_asset_id() -> str:
    return uuid.uuid4().hex[:12]


def _find_asset_ffmpeg() -> str:
    candidates = [
        os.environ.get("FFMPEG_BIN"),
        shutil.which("ffmpeg"),
        shutil.which("ffmpeg.exe"),
        str(_BASE_DIR / "ffmpeg" / "ffmpeg"),
        str(_BASE_DIR / "ffmpeg" / "ffmpeg.exe"),
        str(_BASE_DIR / "deps" / "ffmpeg" / "ffmpeg"),
        str(_BASE_DIR / "deps" / "ffmpeg" / "ffmpeg.exe"),
    ]
    for item in candidates:
        if item and Path(item).exists():
            return str(Path(item))
    raise HTTPException(status_code=500, detail="服务器缺少 ffmpeg，无法把视频切成 2～3 秒片段")


def _save_bytes(data: bytes, ext: str) -> tuple[str, str, int]:
    """Save raw bytes to local disk, return (asset_id, filename, size)."""
    aid = _gen_asset_id()
    fname = f"{aid}{ext}"
    path = ASSETS_DIR / fname
    path.write_bytes(data)
    return aid, fname, len(data)


def _save_bytes_or_tos(
    data: bytes, ext: str, content_type: str = ""
) -> Tuple[str, str, int, Optional[str]]:
    """有 TOS 时上传到 TOS 并返回公网 URL，不落本地；否则落盘。返回 (asset_id, filename_or_key, size, source_url or None)。"""
    aid = _gen_asset_id()
    object_key = f"assets/{aid}{ext}"
    tos_url = _upload_to_tos(data, object_key, content_type or "application/octet-stream")
    if tos_url:
        return aid, object_key, len(data), tos_url
    fname = f"{aid}{ext}"
    path = ASSETS_DIR / fname
    path.write_bytes(data)
    return aid, fname, len(data), None


async def _run_asset_upload_io(func, *args):
    """Run blocking TOS work in a bounded pool that cannot starve API workers."""
    async with _asset_upload_gate:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_asset_upload_executor, partial(func, *args))


def _release_asset_db_before_io(db: Session) -> None:
    """Return the request connection before waiting on file, FFmpeg, or TOS I/O."""
    if db.in_transaction():
        db.commit()


def _online_capability_device(
    db: Session,
    user_id: int,
    capability: str,
) -> tuple[Optional[H5ChatDevicePresence], bool]:
    """Return a live Online installation with the requested capability."""
    now = datetime.utcnow()
    rows = (
        db.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.user_id == user_id)
        .order_by(H5ChatDevicePresence.last_seen_at.desc())
        .limit(20)
        .all()
    )
    online_rows = [row for row in rows if is_device_online(row.last_seen_at, now=now)]
    for row in online_rows:
        payload = row.account_payload if isinstance(row.account_payload, dict) else {}
        capabilities = payload.get("capabilities") if isinstance(payload.get("capabilities"), list) else []
        if capability in capabilities:
            return row, True
    return None, bool(online_rows)


def _online_video_split_device(db: Session, user_id: int) -> tuple[Optional[H5ChatDevicePresence], bool]:
    return _online_capability_device(db, user_id, "asset_video_split_v1")


def _queue_online_video_split(
    db: Session,
    *,
    owner_user_id: int,
    installation_id: str,
    source_asset: Asset,
    source_filename: str,
) -> H5ChatMessage:
    now = datetime.utcnow()
    command = {
        "action": "split_uploaded_video_asset",
        "source_asset_id": source_asset.asset_id,
        "source_url": source_asset.source_url or "",
        "source_filename": source_filename,
        "segment_seconds": _VIDEO_SEGMENT_SECONDS,
        "max_segments": _VIDEO_SEGMENT_MAX_COUNT,
    }
    message = H5ChatMessage(
        id=uuid.uuid4().hex,
        user_id=owner_user_id,
        installation_id=installation_id,
        mode="client_command",
        content=_H5_CLIENT_COMMAND_PREFIX + json.dumps(command, ensure_ascii=False, separators=(",", ":")),
        status="pending",
        created_at=now,
        updated_at=now,
    )
    attach_system_task_message(db, message, now=now)
    db.add(message)
    db.add(
        H5ChatEvent(
            message_id=message.id,
            user_id=owner_user_id,
            event_type="queued",
            payload={
                "mode": "client_command",
                "action": command["action"],
                "source_asset_id": source_asset.asset_id,
            },
            created_at=now,
        )
    )
    return message


def _existing_online_split_segment(
    db: Session,
    *,
    owner_user_id: int,
    split_job_id: str,
    segment_index: int,
) -> Optional[Asset]:
    if not split_job_id or segment_index <= 0:
        return None
    candidates = (
        db.query(Asset)
        .filter(Asset.user_id == owner_user_id, Asset.media_type == "video")
        .order_by(Asset.created_at.desc(), Asset.id.desc())
        .limit(500)
        .all()
    )
    for row in candidates:
        meta = row.meta if isinstance(row.meta, dict) else {}
        if (
            str(meta.get("split_job_id") or "") == split_job_id
            and int(meta.get("segment_index") or 0) == segment_index
        ):
            return row
    return None


def _uploaded_asset_payload(row: Asset, *, deduplicated: bool = False) -> dict:
    return {
        "asset_id": row.asset_id,
        "filename": row.filename,
        "media_type": row.media_type,
        "file_size": row.file_size,
        "source_url": row.source_url,
        "url": row.source_url,
        "asset_origin": "user_upload",
        "deduplicated": deduplicated,
    }


def _limit_asset_upload_requests(func):
    """Bound retained upload bodies while TOS work runs outside the event loop."""
    @wraps(func)
    async def wrapped(*args, **kwargs):
        async with _asset_upload_request_gate:
            return await func(*args, **kwargs)

    return wrapped


# ── Download from URL ─────────────────────────────────────────────

class SaveAssetReq(BaseModel):
    url: str
    media_type: str = "image"
    name: Optional[str] = None
    tags: Optional[str] = None
    prompt: Optional[str] = None
    model: Optional[str] = None


class RegisterAssetUrlReq(BaseModel):
    url: str
    media_type: str = "image"
    filename: Optional[str] = None
    file_size: Optional[int] = None
    source_asset_id: Optional[str] = None
    asset_origin: Optional[str] = None
    creative_candidate_group: Optional[str] = None
    creative_candidate_groups: Optional[list[str]] = None
    prompt: Optional[str] = None
    model: Optional[str] = None
    tags: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    creative_prompt: Optional[str] = None
    content_visibility: Optional[str] = None
    generation_task_id: Optional[str] = None
    generation_record_id: Optional[int] = None
    source_created_at: Optional[str] = None


class RegisterAssetBatchReq(BaseModel):
    assets: list[RegisterAssetUrlReq] = Field(default_factory=list)


class CreativeCandidateGroupReq(BaseModel):
    group_name: str


def _autosave_tags_require_tos(tags: Optional[str]) -> bool:
    """MCP 对话生成后自动入库使用 tags=auto,<capability_id>，此类必须走 TOS，source_url 才稳定可预览。"""
    return (tags or "").strip().startswith("auto,")


def _unlink_safe_asset_file(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


_SAVE_URL_DOWNLOADER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


def _save_url_dedupe_key(url: str) -> str:
    """同一用户、同一规范化 URL 只入库一次（防 MCP+前端重复 save-url）。"""
    return hashlib.sha256(
        (url or "").strip().split("?")[0].split("#")[0].lower().encode("utf-8")
    ).hexdigest()


def _find_existing_asset_by_save_url_dedupe(db: Session, user_id: int, dedupe_key: str) -> Optional[Asset]:
    rows = (
        db.query(Asset)
        .filter(Asset.user_id == user_id)
        .order_by(Asset.id.desc())
        .limit(800)
        .all()
    )
    for a in rows:
        if (a.meta or {}).get("save_url_dedupe") == dedupe_key:
            return a
        if a.source_url and _save_url_dedupe_key(a.source_url) == dedupe_key:
            return a
    return None


def _safe_remote_filename(raw: Optional[str], url: str, fallback: str) -> str:
    name = Path(str(raw or "").replace("\\", "/")).name.strip()
    if not name:
        path = str(url or "").split("?", 1)[0].split("#", 1)[0]
        name = Path(path.replace("\\", "/")).name.strip()
    if not name or "." not in name:
        ext = Path(name).suffix or ".bin"
        name = f"{fallback}{ext}"
    return name[:180]


def _clean_creative_group_name_optional(value: Optional[str]) -> str:
    name = " ".join(str(value or "").strip().split())
    return name[:40]


def _incoming_creative_candidate_group(body: RegisterAssetUrlReq) -> str:
    group = _clean_creative_group_name_optional(body.creative_candidate_group)
    if group:
        return group
    groups = body.creative_candidate_groups if isinstance(body.creative_candidate_groups, list) else []
    for item in groups:
        group = _clean_creative_group_name_optional(str(item or ""))
        if group:
            return group
    return ""


def _register_asset_origin(body: RegisterAssetUrlReq) -> str:
    if body.generation_record_id is not None or str(body.generation_task_id or "").strip():
        return "generated"
    origin = _normalize_asset_origin_filter(body.asset_origin)
    return origin or "user_upload"


def _apply_creative_candidate_group_meta(meta: dict, group_name: str) -> bool:
    if not group_name:
        return False
    changed = False
    if meta.get("creative_candidate_group") != group_name:
        meta["creative_candidate_group"] = group_name
        changed = True
    groups = meta.get("creative_candidate_groups")
    if not isinstance(groups, list) or groups != [group_name]:
        meta["creative_candidate_groups"] = [group_name]
        changed = True
    return changed


def _registered_asset_payload(row: Asset) -> dict:
    group = _creative_candidate_group(row.meta)
    context = _stored_asset_content_context(row)
    source_url = (row.source_url or "").strip()
    source_asset_id = ""
    if isinstance(row.meta, dict):
        source_asset_id = str(row.meta.get("source_asset_id") or row.meta.get("client_asset_id") or "").strip()
    return {
        "asset_id": row.asset_id,
        "source_asset_id": source_asset_id,
        "filename": row.filename,
        "media_type": row.media_type,
        "file_size": row.file_size or 0,
        "source_url": source_url,
        "preview_url": source_url,
        "cover_url": source_url if row.media_type == "image" else "",
        "open_url": source_url,
        "url": source_url,
        "asset_origin": _asset_origin(row.meta),
        "title": context.get("title", ""),
        "description": context.get("description", ""),
        "prompt": row.prompt or context.get("creative_prompt", ""),
        "creative_prompt": context.get("creative_prompt", "") or row.prompt or "",
        "tags": row.tags or context.get("tags", ""),
        "creative_candidate_group": group,
        "creative_candidate_groups": _creative_candidate_groups(row.meta),
    }


def _registered_asset_time(value: Optional[str]) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        return datetime.utcnow()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        return datetime.utcnow()


def _registered_content_context(body: RegisterAssetUrlReq) -> dict[str, str]:
    values = {
        "title": _context_text(body.title, 500),
        "description": _context_text(body.description),
        "creative_prompt": _first_context_text(body.creative_prompt, body.prompt),
        "tags": _publish_tags(body.tags),
        "source": "online_asset_sync",
    }
    return {key: value for key, value in values.items() if value}


def _apply_registered_asset(
    row: Asset,
    body: RegisterAssetUrlReq,
    *,
    asset_origin: str,
    group_name: str,
    registered_from: str,
) -> bool:
    changed = False
    source_url = str(body.url or "").strip()
    media_type = str(body.media_type or "image").strip().lower()
    if media_type not in ("image", "video", "audio", "document"):
        media_type = "image"
    filename = _safe_remote_filename(body.filename, source_url, row.asset_id)
    scalar_updates = {
        "source_url": source_url,
        "media_type": media_type,
        "filename": filename,
    }
    if body.file_size is not None:
        scalar_updates["file_size"] = max(int(body.file_size or 0), 0)
    if body.prompt is not None:
        scalar_updates["prompt"] = _context_text(body.prompt, 12000) or None
    if body.model is not None:
        scalar_updates["model"] = _context_text(body.model, 128) or None
    if body.tags is not None:
        scalar_updates["tags"] = _context_text(body.tags, 2048) or None
    for field_name, value in scalar_updates.items():
        if getattr(row, field_name) != value:
            setattr(row, field_name, value)
            changed = True

    meta = dict(row.meta or {})
    meta_updates: dict[str, Any] = {
        "asset_origin": asset_origin,
        "save_url_dedupe": _save_url_dedupe_key(source_url),
        "registered_from": registered_from,
    }
    source_asset_id = str(body.source_asset_id or "").strip()[:80]
    if source_asset_id:
        meta_updates["source_asset_id"] = source_asset_id
        meta_updates["client_asset_id"] = source_asset_id
    visibility = str(body.content_visibility or "").strip().lower()[:32]
    if visibility:
        meta_updates["content_visibility"] = visibility
    generation_task_id = str(body.generation_task_id or "").strip()[:128]
    if generation_task_id:
        meta_updates["generation_task_id"] = generation_task_id
    if body.generation_record_id is not None:
        meta_updates["generation_record_id"] = int(body.generation_record_id)
    for key, value in meta_updates.items():
        if meta.get(key) != value:
            meta[key] = value
            changed = True
    if _apply_creative_candidate_group_meta(meta, group_name):
        changed = True
    incoming_context = _registered_content_context(body)
    if incoming_context:
        current_context = meta.get("content_context") if isinstance(meta.get("content_context"), dict) else {}
        merged_context = dict(current_context)
        for key, value in incoming_context.items():
            if value:
                merged_context[key] = value
        if merged_context != current_context:
            meta["content_context"] = merged_context
            changed = True
    if changed:
        row.meta = meta
    return changed


def upsert_registered_assets(
    db: Session,
    user_id: int,
    bodies: list[RegisterAssetUrlReq],
    *,
    registered_from: str = "online",
) -> tuple[list[Asset], int, int]:
    """Upsert a batch of public Online assets without downloading media."""
    normalized = [body for body in bodies if str(body.url or "").strip().startswith(("http://", "https://"))]
    if not normalized:
        return [], 0, 0

    urls = list(dict.fromkeys(str(body.url or "").strip() for body in normalized))
    source_ids = list(dict.fromkeys(
        str(body.source_asset_id or "").strip()[:80]
        for body in normalized
        if str(body.source_asset_id or "").strip()
    ))
    conditions = [Asset.source_url.in_(urls)]
    if source_ids:
        conditions.extend([
            Asset.meta["source_asset_id"].as_string().in_(source_ids),
            Asset.meta["client_asset_id"].as_string().in_(source_ids),
        ])
    existing_rows = db.query(Asset).filter(Asset.user_id == int(user_id), or_(*conditions)).all()
    recent_rows = (
        db.query(Asset)
        .filter(Asset.user_id == int(user_id))
        .order_by(Asset.id.desc())
        .limit(max(800, len(normalized) * 4))
        .all()
    )
    all_existing = {row.id: row for row in [*existing_rows, *recent_rows] if row.id is not None}.values()
    by_url: dict[tuple[str, str], Asset] = {}
    by_source_id: dict[tuple[str, str], Asset] = {}
    by_dedupe: dict[tuple[str, str], Asset] = {}
    for row in all_existing:
        row_origin = _asset_origin(row.meta)
        url = str(row.source_url or "").strip()
        if url:
            by_url.setdefault((row_origin, url), row)
            by_dedupe.setdefault((row_origin, _save_url_dedupe_key(url)), row)
        meta = row.meta if isinstance(row.meta, dict) else {}
        dedupe = str(meta.get("save_url_dedupe") or "").strip()
        if dedupe:
            by_dedupe.setdefault((row_origin, dedupe), row)
        for key in ("source_asset_id", "client_asset_id"):
            source_id = str(meta.get(key) or "").strip()
            if source_id:
                by_source_id.setdefault((row_origin, source_id), row)

    rows: list[Asset] = []
    created = 0
    updated = 0
    for body in normalized:
        source_url = str(body.url or "").strip()
        source_id = str(body.source_asset_id or "").strip()[:80]
        dedupe_key = _save_url_dedupe_key(source_url)
        asset_origin = _register_asset_origin(body)
        row = by_source_id.get((asset_origin, source_id)) if source_id else None
        if row is None:
            candidate = by_url.get((asset_origin, source_url)) or by_dedupe.get((asset_origin, dedupe_key))
            candidate_meta = candidate.meta if candidate is not None and isinstance(candidate.meta, dict) else {}
            candidate_source_ids = {
                str(candidate_meta.get(key) or "").strip()
                for key in ("source_asset_id", "client_asset_id")
                if str(candidate_meta.get(key) or "").strip()
            }
            # A legacy server row without an Online id can be enriched in place.
            # Distinct Online ids remain distinct content records even if an
            # upstream provider reused the same public URL.
            if not source_id or not candidate_source_ids or source_id in candidate_source_ids:
                row = candidate
        group_name = _incoming_creative_candidate_group(body)
        if row is None:
            aid = _gen_asset_id()
            row = Asset(
                asset_id=aid,
                user_id=int(user_id),
                filename=_safe_remote_filename(body.filename, source_url, aid),
                media_type="image",
                file_size=0,
                source_url=source_url,
                meta={},
                created_at=_registered_asset_time(body.source_created_at),
            )
            db.add(row)
            created += 1
        elif _apply_registered_asset(
            row,
            body,
            asset_origin=asset_origin,
            group_name=group_name,
            registered_from=registered_from,
        ):
            updated += 1
        if row.id is None or row in db.new:
            _apply_registered_asset(
                row,
                body,
                asset_origin=asset_origin,
                group_name=group_name,
                registered_from=registered_from,
            )
        by_url[(asset_origin, source_url)] = row
        by_dedupe[(asset_origin, dedupe_key)] = row
        if source_id:
            by_source_id[(asset_origin, source_id)] = row
        rows.append(row)
    return rows, created, updated


def _context_text(value, limit: int = 12000) -> str:
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(item or "").strip() for item in value if str(item or "").strip())
    if not isinstance(value, (str, int, float)):
        return ""
    return str(value).strip()[:limit]


def _first_context_text(*values, limit: int = 12000) -> str:
    for value in values:
        text = _context_text(value, limit)
        if text:
            return text
    return ""


def _publish_tags(value) -> str:
    text = _context_text(value, 2000)
    if not text:
        return ""
    internal = {"auto", "task.get_result", "save-url", "generated"}
    parts = [part.strip() for part in re.split(r"[,，\s]+", text) if part.strip()]
    return " ".join(part for part in parts if part.lower() not in internal)


def _stored_asset_content_context(row: Asset) -> dict[str, str]:
    meta = row.meta if isinstance(row.meta, dict) else {}
    raw = meta.get("content_context") if isinstance(meta.get("content_context"), dict) else {}
    return {
        "title": _first_context_text(raw.get("title"), meta.get("content_title"), limit=500),
        "description": _first_context_text(raw.get("description"), meta.get("content_description")),
        "creative_prompt": _first_context_text(raw.get("creative_prompt"), meta.get("creative_prompt"), row.prompt),
        "tags": _publish_tags(_first_context_text(raw.get("tags"), meta.get("content_tags"), row.tags, limit=2000)),
        "source": _first_context_text(raw.get("source"), limit=64),
    }


def _asset_source_ids(row: Asset) -> list[str]:
    meta = row.meta if isinstance(row.meta, dict) else {}
    values = [row.asset_id, meta.get("source_asset_id"), meta.get("client_asset_id")]
    return list(dict.fromkeys(_context_text(value, 128) for value in values if _context_text(value, 128)))


def _run_asset_content_context(row: ScheduledTaskRun) -> dict[str, str]:
    result = row.result_payload if isinstance(row.result_payload, dict) else {}
    generated = result.get("generated") if isinstance(result.get("generated"), dict) else {}
    mcp = result.get("mcp_result") if isinstance(result.get("mcp_result"), dict) else {}
    mcp_result = mcp.get("result") if isinstance(mcp.get("result"), dict) else {}
    mcp_pipeline = mcp_result.get("result") if isinstance(mcp_result.get("result"), dict) else {}
    plans = [
        value.get("plan")
        for value in (result, generated, mcp, mcp_result, mcp_pipeline)
        if isinstance(value, dict) and isinstance(value.get("plan"), dict)
    ]
    plan = next((value for value in plans if value), {})
    draft = result.get("publish_draft") if isinstance(result.get("publish_draft"), dict) else {}
    input_payload = row.payload if isinstance(row.payload, dict) else {}
    inner_input = input_payload.get("payload") if isinstance(input_payload.get("payload"), dict) else {}
    result_text = _context_text(row.result_text, 20000)
    caption_match = re.search(r"(?:发布文案|发布正文|配文)\s*[:：]\s*([^\r\n]+)", result_text)
    return {
        "title": _first_context_text(
            plan.get("title"), plan.get("headline"), result.get("title"), draft.get("title"), generated.get("title"), row.title,
            limit=500,
        ),
        "description": _first_context_text(
            result.get("caption"), generated.get("caption"), generated.get("copy"), plan.get("copy"), plan.get("caption"),
            draft.get("description"), caption_match.group(1) if caption_match else "",
        ),
        "creative_prompt": _first_context_text(
            result.get("skill_prompt"), result.get("image_prompt"), result.get("video_prompt"),
            plan.get("image_prompt"), plan.get("video_prompt"), generated.get("prompt"),
            inner_input.get("prompt"), inner_input.get("task_text"), input_payload.get("prompt"),
        ),
        "tags": _publish_tags(_first_context_text(
            result.get("tags"), result.get("hashtags"), generated.get("tags"), plan.get("tags"), draft.get("tags"),
            limit=2000,
        )),
        "source": "scheduled_task_run",
    }


def _value_contains_source_id(value, source_ids: set[str]) -> bool:
    if isinstance(value, dict):
        return any(_value_contains_source_id(item, source_ids) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_value_contains_source_id(item, source_ids) for item in value)
    if isinstance(value, str):
        return value in source_ids or any(source_id in value for source_id in source_ids)
    return False


def _candidate_asset_runs(
    db: Session,
    row: Asset,
    source_ids: list[str],
    date_hints: list[datetime],
) -> list[ScheduledTaskRun]:
    candidates: dict[str, ScheduledTaskRun] = {}
    hints = [value for value in date_hints if isinstance(value, datetime)]
    if not hints and isinstance(row.created_at, datetime):
        hints = [row.created_at]
    for hint in hints:
        rows = (
            db.query(ScheduledTaskRun)
            .filter(
                ScheduledTaskRun.user_id == row.user_id,
                ScheduledTaskRun.created_at >= hint - timedelta(hours=12),
                ScheduledTaskRun.created_at <= hint + timedelta(hours=12),
            )
            .order_by(ScheduledTaskRun.created_at.desc())
            .limit(200)
            .all()
        )
        for run in rows:
            candidates[run.id] = run
    source_id_set = set(source_ids)
    ordered = sorted(candidates.values(), key=lambda item: item.created_at or datetime.min, reverse=True)
    return [run for run in ordered if _value_contains_source_id(run.result_payload, source_id_set)]


def _resolve_asset_content_context(db: Session, row: Asset) -> dict[str, str]:
    context = _stored_asset_content_context(row)

    def merge(values: dict[str, str]) -> None:
        for key in ("title", "description", "creative_prompt", "tags", "source"):
            if not context.get(key) and values.get(key):
                context[key] = values[key]

    source_ids = _asset_source_ids(row)
    date_hints: list[datetime] = []
    if source_ids:
        draft = (
            db.query(IPContentDraftRecord)
            .filter(
                IPContentDraftRecord.user_id == row.user_id,
                IPContentDraftRecord.image_asset_id.in_(source_ids),
            )
            .order_by(IPContentDraftRecord.created_at.desc())
            .first()
        )
        if draft:
            if draft.created_at:
                date_hints.append(draft.created_at)
            merge({
                "title": _first_context_text(draft.title, limit=500),
                "description": _first_context_text(draft.content),
                "creative_prompt": _first_context_text(draft.image_prompt),
                "tags": "",
                "source": "ip_daily",
            })

        generation = (
            db.query(GenerationRecord)
            .filter(GenerationRecord.user_id == row.user_id, GenerationRecord.client_asset_id.in_(source_ids))
            .order_by(GenerationRecord.created_at.desc())
            .first()
        )
        if generation:
            if generation.created_at:
                date_hints.append(generation.created_at)
            merge({
                "title": "",
                "description": "",
                "creative_prompt": _first_context_text(generation.prompt),
                "tags": "",
                "source": "generation_record",
            })

        if not context.get("title") or not context.get("description") or not context.get("creative_prompt"):
            for run in _candidate_asset_runs(db, row, source_ids, date_hints):
                merge(_run_asset_content_context(run))
                if context.get("title") and context.get("description") and context.get("creative_prompt"):
                    break
    return context


def _persist_asset_content_context(row: Asset, context: dict[str, str]) -> bool:
    useful = {key: _context_text(context.get(key), 12000) for key in ("title", "description", "creative_prompt", "tags", "source")}
    useful["tags"] = _publish_tags(useful.get("tags"))
    useful = {key: value for key, value in useful.items() if value}
    if not useful:
        return False
    changed = False
    meta = dict(row.meta or {})
    if meta.get("content_context") != useful:
        meta["content_context"] = useful
        row.meta = meta
        changed = True
    if not row.prompt and useful.get("creative_prompt"):
        row.prompt = useful["creative_prompt"]
        changed = True
    if not row.tags and useful.get("tags"):
        row.tags = useful["tags"]
        changed = True
    elif row.tags and not _publish_tags(row.tags):
        row.tags = None
        changed = True
    return changed


@router.post("/api/assets/register-url", summary="登记公网素材为用户上传素材")
async def register_asset_url(
    body: RegisterAssetUrlReq,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    url = (body.url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="素材 URL 必须是公网 http/https 地址")
    lowered = url.lower()
    if any(bad in lowered for bad in ("localhost", "127.0.0.1", "0.0.0.0")):
        raise HTTPException(status_code=400, detail="不能登记本机或内网素材地址")
    media_type = (body.media_type or "image").strip().lower()
    if media_type not in ("image", "video", "audio", "document"):
        body.media_type = "image"
    rows, _, _ = upsert_registered_assets(db, owner_user.id, [body], registered_from="online")
    if not rows:
        raise HTTPException(status_code=400, detail="Asset URL must be a public http(s) URL")
    asset = rows[0]
    db.flush()
    _persist_asset_content_context(asset, _resolve_asset_content_context(db, asset))
    db.commit()
    db.refresh(asset)
    return _registered_asset_payload(asset)


@router.post("/api/assets/register-batch", summary="Batch sync Online assets into the shared content library")
def register_asset_batch(
    body: RegisterAssetBatchReq,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if len(body.assets) > 200:
        raise HTTPException(status_code=400, detail="A batch can contain at most 200 assets")
    if not body.assets:
        return {"ok": True, "created": 0, "updated": 0, "items": []}
    for item in body.assets:
        url = str(item.url or "").strip()
        lowered = url.lower()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Asset URL must be a public http(s) URL")
        if any(value in lowered for value in ("localhost", "127.0.0.1", "0.0.0.0")):
            raise HTTPException(status_code=400, detail="Local asset URLs cannot be synchronized")
    owner_user = online_user_for_mobile_user(db, current_user)
    rows, created, updated = upsert_registered_assets(
        db,
        owner_user.id,
        body.assets,
        registered_from="online_batch",
    )
    db.commit()
    for row in rows:
        db.refresh(row)
    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "items": [_registered_asset_payload(row) for row in rows],
    }


@router.post("/api/assets/save-url", summary="从 URL 保存素材")
async def save_asset_from_url(
    body: SaveAssetReq,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = int(current_user.id)
    dk = _save_url_dedupe_key(body.url)
    existing = _find_existing_asset_by_save_url_dedupe(db, user_id, dk)
    if existing:
        logger.info(
            "[素材] save-url 去重 命中已有 asset_id=%s",
            existing.asset_id,
        )
        return {
            "asset_id": existing.asset_id,
            "filename": existing.filename,
            "media_type": existing.media_type,
            "file_size": existing.file_size or 0,
            "source_url": existing.source_url or "",
        }

    # Remote download and TOS upload can each take minutes. Release the
    # dedupe query transaction before doing either network operation.
    db.commit()

    if _get_tos_config() is None:
        raise HTTPException(
            status_code=503,
            detail="save-url 入库需配置 TOS_CONFIG（custom_configs.json，含 access_key/secret_key/endpoint/region/bucket_name/public_domain），未配置无法保存统一 CDN 地址。",
        )

    temp_file = await asyncio.to_thread(tempfile.TemporaryFile)
    response_content_type = ""
    try:
        try:
            async with httpx.AsyncClient(
                timeout=120.0,
                follow_redirects=True,
                trust_env=False,
            ) as client:
                async with client.stream("GET", body.url, headers=_SAVE_URL_DOWNLOADER_HEADERS) as resp:
                    resp.raise_for_status()
                    response_content_type = resp.headers.get("content-type", "") or ""
                    total = 0
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > _ASSET_UPLOAD_MAX_BYTES:
                            limit_mb = int(_ASSET_UPLOAD_MAX_BYTES / 1024 / 1024)
                            raise HTTPException(status_code=413, detail=f"远程素材不能超过 {limit_mb}MB")
                        await asyncio.to_thread(temp_file.write, chunk)
                    if total <= 0:
                        raise HTTPException(status_code=400, detail="下载失败: 远程素材为空")
        except HTTPException:
            raise
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"下载失败: HTTP {exc.response.status_code}",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"下载失败: {type(exc).__name__}: {exc!s}",
            ) from exc

        url_path = body.url.split("?")[0].split("#")[0]
        url_ext = Path(url_path).suffix.lower() if "." in url_path.split("/")[-1] else ""
        ct = response_content_type
        ext = url_ext or ".png"
        if not url_ext:
            if "jpeg" in ct or "jpg" in ct:
                ext = ".jpg"
            elif "webp" in ct:
                ext = ".webp"
            elif "gif" in ct:
                ext = ".gif"
            elif "mp4" in ct or "video/mp4" in ct:
                ext = ".mp4"
            elif "webm" in ct:
                ext = ".webm"
            elif "mov" in ct or "quicktime" in ct:
                ext = ".mov"

        if body.media_type == "video" and ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            ext = ".mp4"
        elif body.media_type == "image" and ext in (".mp4", ".webm", ".mov", ".avi"):
            ext = ".png"

        ct_use = ct if ct else "application/octet-stream"
        aid, fname_or_key, fsize, tos_public_url = await _run_asset_upload_io(
            _save_upload_file_or_tos,
            temp_file,
            ext,
            ct_use,
        )
    finally:
        await asyncio.to_thread(temp_file.close)
    if not tos_public_url:
        _unlink_safe_asset_file(ASSETS_DIR / fname_or_key)
        raise HTTPException(
            status_code=503,
            detail="save-url 已下载素材但火山 TOS 上传失败，无法入库。请检查 TOS 配置与网络后重试。",
        )
    source_url = tos_public_url
    asset = Asset(
        asset_id=aid,
        user_id=user_id,
        filename=fname_or_key,
        media_type=body.media_type,
        file_size=fsize,
        source_url=source_url,
        prompt=body.prompt,
        model=body.model,
        tags=body.tags,
        meta={"save_url_dedupe": dk},
    )
    db.add(asset)
    db.commit()
    logger.info("[素材] save-url 完成 url=%s asset_id=%s size=%s media_type=%s tos=%s", body.url[:80] + ("..." if len(body.url) > 80 else ""), aid, fsize, body.media_type, bool(tos_public_url))
    return {
        "asset_id": aid,
        "filename": fname_or_key,
        "media_type": body.media_type,
        "file_size": fsize,
        "source_url": source_url,
    }


# ── Upload file ───────────────────────────────────────────────────

@router.post("/api/assets/upload", summary="上传素材文件")
@_limit_asset_upload_requests
async def upload_asset(
    file: UploadFile = File(...),
    split_video: bool = Form(False),
    source_upload_filename: str = Form(""),
    video_segment: bool = Form(False),
    segment_index: int = Form(0),
    split_job_id: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    owner_user_id = int(owner_user.id)
    name = file.filename or "upload"
    ext = Path(name).suffix or ".bin"
    mtype = "file"
    if ext.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif"):
        mtype = "image"
    elif ext.lower() in (".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".wmv"):
        mtype = "video"
    elif ext.lower() in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"):
        mtype = "audio"
    elif ext.lower() in (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".csv", ".txt", ".md"):
        mtype = "document"

    split_device = None
    if split_video and mtype == "video":
        split_device, has_online_device = _online_video_split_device(db, owner_user_id)
        if split_device is None:
            if has_online_device:
                raise HTTPException(status_code=409, detail="当前 Online 版本不支持本机视频切片，请升级最新 OTA 后重试")
            raise HTTPException(status_code=409, detail="视频切片需要先启动并登录 Online，服务器不再代替本机执行切片")
    clean_split_job_id = str(split_job_id or "")[:64] if isinstance(split_job_id, str) else ""
    clean_segment_index = max(0, int(segment_index or 0)) if isinstance(segment_index, int) else 0
    if video_segment is True and clean_split_job_id and clean_segment_index:
        existing_segment = _existing_online_split_segment(
            db,
            owner_user_id=owner_user_id,
            split_job_id=clean_split_job_id,
            segment_index=clean_segment_index,
        )
        if existing_segment is not None:
            return _uploaded_asset_payload(existing_segment, deduplicated=True)
    _release_asset_db_before_io(db)
    content_type = getattr(file, "content_type", "") or ""
    started_at = time.monotonic()
    aid, fname_or_key, fsize, tos_public_url = await _run_asset_upload_io(
        _save_upload_file_or_tos,
        file.file,
        ext,
        content_type,
    )
    logger.info(
        "[asset-upload] processing start user_id=%s filename=%s size=%s media_type=%s split_video=%s",
        owner_user_id,
        name,
        fsize,
        mtype,
        bool(split_video and mtype == "video"),
    )
    if split_video and mtype == "video":
        if not tos_public_url:
            _unlink_safe_asset_file(ASSETS_DIR / fname_or_key)
            raise HTTPException(status_code=503, detail="原视频未成功写入 TOS，无法下发本机切片")
        source_asset = Asset(
            asset_id=aid,
            user_id=owner_user_id,
            filename=fname_or_key,
            media_type="video",
            file_size=fsize,
            source_url=tos_public_url,
            meta={
                "asset_origin": "intermediate",
                "content_visibility": "intermediate",
                "online_split_source": True,
                "source_upload_filename": name,
            },
        )
        db.add(source_asset)
        message = _queue_online_video_split(
            db,
            owner_user_id=owner_user_id,
            installation_id=str(split_device.installation_id),
            source_asset=source_asset,
            source_filename=name,
        )
        db.commit()
        from .h5_chat import _clear_pending_empty_for_target

        _clear_pending_empty_for_target(owner_user_id, str(split_device.installation_id))
        logger.info(
            "[素材库视频切片-下发Online] user_id=%s filename=%s source_asset_id=%s message_id=%s installation_id=%s",
            owner_user_id,
            name,
            aid,
            message.id,
            split_device.installation_id,
        )
        return {
            "ok": True,
            "split_video": True,
            "processing": "online",
            "message_id": message.id,
            "installation_id": str(split_device.installation_id),
            "total": 0,
            "assets": [],
        }

    if not tos_public_url:
        local_path = ASSETS_DIR / fname_or_key
        try:
            if local_path.exists():
                local_path.unlink()
        except Exception as e:
            logger.warning("[上传流程-失败] 删除本地文件异常 asset_id=%s err=%s", aid, e)
        logger.error(
            "[上传流程-失败] 服务器 /api/assets/upload 无 TOS 公网 URL asset_id=%s 已删本地，终止上传",
            aid,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "服务器未成功写入 TOS 公网链接，无法用于图生视频等。"
                "请在服务器 custom_configs.json 配置 TOS_CONFIG，或改用 lobster_online 本机上传（本机 TOS → 失败则 upload-temp）。"
            ),
        )
    asset_meta = {"asset_origin": "user_upload"}
    if video_segment is True:
        asset_meta.update(
            {
                "source_upload_filename": str(source_upload_filename or "")[:255],
                "video_segment": True,
                "segment_index": max(1, clean_segment_index or 1),
                "segment_seconds": _VIDEO_SEGMENT_SECONDS,
                "split_job_id": clean_split_job_id,
            }
        )
    asset = Asset(
        asset_id=aid,
        user_id=owner_user_id,
        filename=fname_or_key,
        media_type=mtype,
        file_size=fsize,
        source_url=tos_public_url,
        meta=asset_meta,
    )
    db.add(asset)
    db.commit()
    logger.info("[上传流程-步骤5] 服务器直连上传完成（TOS）asset_id=%s source_url=%s", aid, tos_public_url[:80])
    return _uploaded_asset_payload(asset)


# ── Temporary file upload (for clients without TOS) ───────────────

class TempUploadResponse(BaseModel):
    temp_id: str
    public_url: str
    storage: str = "temp"
    object_key: Optional[str] = None


@router.post("/api/assets/upload-temp", summary="上传临时文件（无TOS时使用）")
async def upload_temp_file(
    file: UploadFile = File(...),
    request: Request = None,
    current_user: User = Depends(get_current_user),
):
    """【服务器端-步骤3.1】接收客户端上传的临时文件，返回可访问的URL。这些文件将在视频生成任务完成后自动删除。"""
    logger.info("[服务器端-步骤3.1] 收到临时文件上传请求 filename=%s user_id=%s", file.filename, current_user.id if current_user else "N/A")
    
    # 【服务器端-步骤3.2】生成临时文件ID
    name = file.filename or "upload"
    ext = Path(name).suffix or ".bin"
    content_type = (file.content_type or "").strip() or "application/octet-stream"

    object_id = uuid.uuid4().hex[:16]
    object_key = f"assets/{object_id}{ext}"
    temp_id = f"temp_{uuid.uuid4().hex[:16]}"
    temp_filename = f"{temp_id}{ext}"
    temp_path = TEMP_ASSETS_DIR / temp_filename
    file_size, tos_url = await _run_asset_upload_io(
        _store_temp_upload_file,
        file.file,
        object_key,
        content_type,
        temp_path,
    )
    if tos_url:
        logger.info(
            "[server-upload-temp] stored in TOS user_id=%s object_key=%s size=%d",
            current_user.id if current_user else "N/A",
            object_key,
            file_size,
        )
        return TempUploadResponse(
            temp_id=object_id,
            public_url=tos_url,
            storage="tos",
            object_key=object_key,
        )

    logger.warning(
        "[server-upload-temp] TOS unavailable, falling back to short-lived temp URL user_id=%s filename=%s size=%d",
        current_user.id if current_user else "N/A",
        name,
        file_size,
    )

    logger.info("[服务器端-步骤3.2] 生成临时文件ID temp_id=%s filename=%s", temp_id, temp_filename)

    logger.info("[服务器端-步骤3.3] 临时文件已保存 temp_id=%s path=%s size=%d", temp_id, temp_path, file_size)
    
    # 【服务器端-步骤3.4】生成可访问的URL
    from ..core.config import get_settings
    settings = get_settings()
    base = (getattr(settings, "public_base_url", None) or "").strip().rstrip("/")
    if not base and request:
        try:
            base = str((request.base_url or "").rstrip("/"))
            logger.info("[服务器端-步骤3.4] 从请求获取base_url=%s", base)
        except Exception:
            pass
    if not base:
        base = "https://bhzn.top"
        logger.info("[服务器端-步骤3.4] 使用默认base_url=%s", base)
    expiry_ts = int(time.time()) + _ASSET_FILE_EXPIRY_SEC
    public_url = f"{base}/api/assets/temp/{temp_id}?token={_asset_file_token(temp_id, expiry_ts)}&expiry={expiry_ts}"
    logger.info("[服务器端-步骤3.5] 生成临时文件URL temp_id=%s public_url=%s", temp_id, public_url[:80])
    
    return TempUploadResponse(temp_id=temp_id, public_url=public_url)


@router.get("/api/assets/temp/{temp_id}", summary="访问临时文件")
@router.head("/api/assets/temp/{temp_id}", include_in_schema=False)
async def get_temp_file(
    temp_id: str,
    token: str = Query(...),
    expiry: int = Query(...),
):
    """提供临时文件的访问接口，带签名验证。"""
    # 验证token
    expected_token = _asset_file_token(temp_id, expiry)
    if not hmac.compare_digest(token, expected_token):
        raise HTTPException(403, detail="无效的token")
    
    # 检查过期
    if int(time.time()) > expiry:
        raise HTTPException(403, detail="URL已过期")
    
    # 查找临时文件
    temp_files = list(TEMP_ASSETS_DIR.glob(f"{temp_id}.*"))
    if not temp_files:
        raise HTTPException(404, detail="临时文件不存在或已删除")
    
    temp_path = temp_files[0]
    if not temp_path.exists():
        raise HTTPException(404, detail="临时文件不存在")
    
    return FileResponse(
        temp_path,
        media_type=_tos_object_headers("", temp_path.name)[0],
        filename=temp_path.name,
        content_disposition_type="inline",
    )


def register_temp_file_for_task(task_id: str, temp_id: str):
    """注册临时文件与任务ID的关联，用于任务完成后清理。"""
    if task_id not in _temp_files_by_task:
        _temp_files_by_task[task_id] = []
    
    # 查找临时文件路径
    temp_files = list(TEMP_ASSETS_DIR.glob(f"{temp_id}.*"))
    if temp_files:
        _temp_files_by_task[task_id].append(temp_files[0])
        logger.info("[临时文件] 注册 task_id=%s temp_id=%s path=%s", task_id, temp_id, temp_files[0])


def cleanup_temp_files_for_task(task_id: str):
    """清理指定任务关联的临时文件。"""
    if task_id not in _temp_files_by_task:
        return
    
    deleted_count = 0
    for temp_path in _temp_files_by_task[task_id]:
        try:
            if temp_path.exists():
                temp_path.unlink()
                deleted_count += 1
                logger.info("[临时文件] 已删除 task_id=%s path=%s", task_id, temp_path)
        except Exception as e:
            logger.warning("[临时文件] 删除失败 task_id=%s path=%s error=%s", task_id, temp_path, e)
    
    del _temp_files_by_task[task_id]
    if deleted_count > 0:
        logger.info("[临时文件] 任务完成清理 task_id=%s 删除文件数=%d", task_id, deleted_count)


# ── List / search ─────────────────────────────────────────────────

def _asset_origin(meta: Optional[dict]) -> str:
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = None
    if isinstance(meta, dict):
        origin = str(meta.get("asset_origin") or meta.get("origin") or "").strip()
        if origin == "user_upload":
            return "user_upload"
    return "generated"


def _asset_hidden_from_library(row: Asset) -> bool:
    meta = row.meta
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = None
    if isinstance(meta, dict):
        visibility = str(meta.get("content_visibility") or meta.get("library_visibility") or "").strip().lower()
        origin = str(meta.get("asset_origin") or meta.get("origin") or "").strip().lower()
        if visibility in {"hidden", "internal", "intermediate"} or origin in {"internal", "intermediate"}:
            return True
    # Historical rows created before content_visibility was recorded must also
    # stay out of the user-facing content list.
    return str(row.model or "").strip() == "shanjian-digital-human-template-media"


def _normalize_asset_origin_filter(value: Optional[str]) -> str:
    raw = str(value or "").strip().lower()
    if raw in ("user_upload", "upload", "uploaded", "manual_upload"):
        return "user_upload"
    if raw in ("generated", "generate", "ai_generated"):
        return "generated"
    return ""


def _creative_candidate_group(meta: Optional[dict]) -> str:
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = None
    if not isinstance(meta, dict):
        return ""
    current = _clean_creative_group_name_optional(meta.get("creative_candidate_group"))
    if current:
        return current
    raw = meta.get("creative_candidate_groups")
    if isinstance(raw, list):
        for item in raw:
            name = _clean_creative_group_name_optional(item)
            if name:
                return name
    return ""


def _creative_candidate_groups(meta: Optional[dict]) -> list[str]:
    group = _creative_candidate_group(meta)
    return [group] if group else []


@router.get("/api/assets", summary="列出本地素材")
def list_assets(
    media_type: Optional[str] = None,
    q: Optional[str] = None,
    source: Optional[str] = None,
    origin: Optional[str] = None,
    asset_origin: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    query = db.query(Asset).filter(Asset.user_id == owner_user.id)
    if media_type:
        query = query.filter(Asset.media_type == media_type)
    if q:
        pat = f"%{q}%"
        query = query.filter(
            (Asset.tags.ilike(pat))
            | (Asset.prompt.ilike(pat))
            | (Asset.filename.ilike(pat))
        )
    max_limit = max(1, min(int(limit or 50), 200))
    clean_offset = max(0, int(offset or 0))
    meta_origin = func.lower(
        func.coalesce(
            Asset.meta["asset_origin"].as_string(),
            Asset.meta["origin"].as_string(),
            "",
        )
    )
    meta_visibility = func.lower(
        func.coalesce(
            Asset.meta["content_visibility"].as_string(),
            Asset.meta["library_visibility"].as_string(),
            "",
        )
    )
    query = query.filter(
        ~meta_visibility.in_(("hidden", "internal", "intermediate")),
        ~meta_origin.in_(("internal", "intermediate")),
        func.coalesce(Asset.model, "") != "shanjian-digital-human-template-media",
    )
    origin_filter = _normalize_asset_origin_filter(origin or asset_origin or source)
    if origin_filter == "user_upload":
        query = query.filter(meta_origin == "user_upload")
    elif origin_filter == "generated":
        query = query.filter(meta_origin != "user_upload")
    total = query.with_entities(func.count(Asset.id)).scalar() or 0
    rows = (
        query.order_by(Asset.created_at.desc(), Asset.id.desc())
        .offset(clean_offset)
        .limit(max_limit)
        .all()
    )
    def payload(row: Asset) -> dict:
        context = _stored_asset_content_context(row)
        source_url = (row.source_url or "").strip()
        return {
            "asset_id": row.asset_id,
            "filename": row.filename,
            "media_type": row.media_type,
            "file_size": row.file_size,
            "source_url": source_url,
            "preview_url": source_url,
            "cover_url": source_url if row.media_type == "image" else "",
            "open_url": source_url,
            "title": context.get("title", ""),
            "description": context.get("description", ""),
            "prompt": row.prompt or context.get("creative_prompt", ""),
            "creative_prompt": context.get("creative_prompt", "") or row.prompt or "",
            "model": row.model,
            "tags": row.tags or context.get("tags", ""),
            "creative_candidate_group": _creative_candidate_group(row.meta),
            "creative_candidate_groups": _creative_candidate_groups(row.meta),
            "asset_origin": _asset_origin(row.meta),
            "created_at": row.created_at.isoformat() if row.created_at else "",
        }

    return {
        "total": total,
        "assets": [payload(row) for row in rows],
    }


def _asset_local_path(asset: Asset) -> Optional[Path]:
    """有本地文件时返回路径，仅 TOS（无本地）时返回 None。"""
    fn = asset.filename or ""
    if "/" in fn:
        return None
    p = ASSETS_DIR / fn
    return p if p.exists() else None


@router.get("/api/assets/creative-candidate-groups", summary="创意成片备选素材组列表")
def list_creative_candidate_groups(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    rows = db.query(Asset).filter(Asset.user_id == owner_user.id, Asset.media_type == "image").all()
    groups: dict[str, dict] = {}
    for row in rows:
        name = _creative_candidate_group(row.meta)
        if name:
            current = groups.setdefault(name, {"name": name, "count": 0})
            current["count"] += 1
    return {
        "ok": True,
        "groups": [
            item
            for item in sorted(groups.values(), key=lambda row: (-int(row.get("count") or 0), str(row.get("name") or "")))
        ],
    }


@router.post("/api/assets/{asset_id}/creative-candidate-groups", summary="加入创意成片备选素材组")
def add_asset_to_creative_candidate_group(
    asset_id: str,
    body: CreativeCandidateGroupReq,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    row = db.query(Asset).filter(Asset.asset_id == asset_id, Asset.user_id == owner_user.id).first()
    if not row:
        raise HTTPException(404, detail="素材不存在")
    if (row.media_type or "").strip().lower() != "image":
        raise HTTPException(400, detail="只有图片素材可以设为创意备选素材")
    group_name = _clean_creative_group_name_optional(body.group_name)
    if not group_name:
        raise HTTPException(400, detail="备选组名字不能为空")
    meta = dict(row.meta or {})
    _apply_creative_candidate_group_meta(meta, group_name)
    row.meta = meta
    db.add(row)
    db.commit()
    return {"ok": True, "asset_id": row.asset_id, "group_name": group_name, "groups": [group_name]}


# ── Get single + serve file ──────────────────────────────────────

@router.get("/api/assets/{asset_id}/content", summary="素材文件内容（需登录，用于前端预览）")
def get_asset_content(
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    a = db.query(Asset).filter(Asset.asset_id == asset_id, Asset.user_id == owner_user.id).first()
    if not a:
        raise HTTPException(404, detail="素材不存在")
    local_path = _asset_local_path(a)
    if local_path is None and getattr(a, "source_url", None):
        url = (a.source_url or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            return RedirectResponse(url=url)
    if local_path is None:
        raise HTTPException(404, detail="文件不存在")
    mt_map = {"image": "image/jpeg", "video": "video/mp4", "audio": "audio/mpeg"}
    ct = mt_map.get((a.media_type or "").lower(), "application/octet-stream")
    return FileResponse(local_path, media_type=ct, filename=a.filename)


@router.get("/api/assets/file/{asset_id}", summary="素材文件（带签名公开访问，供速推等拉取）")
def serve_asset_file(
    asset_id: str,
    token: str = Query(..., description="签名 token"),
    expiry: int = Query(..., description="过期时间戳"),
    db: Session = Depends(get_db),
):
    """不校验登录，仅校验 token 与 expiry；用于会话附图/视频时生成可被上游拉取的 URL。仅 TOS 时重定向到公网 URL。"""
    now = int(time.time())
    if expiry < now:
        raise HTTPException(403, detail="链接已过期")
    expected = _asset_file_token(asset_id, expiry)
    if not hmac.compare_digest(expected, token):
        raise HTTPException(403, detail="无效链接")
    a = db.query(Asset).filter(Asset.asset_id == asset_id).first()
    if not a:
        raise HTTPException(404, detail="素材不存在")
    local_path = _asset_local_path(a)
    if local_path is None and getattr(a, "source_url", None):
        url = (a.source_url or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            return RedirectResponse(url=url)
    if local_path is None:
        raise HTTPException(404, detail="文件不存在")
    media_type = a.media_type or "application/octet-stream"
    mt_map = {"image": "image/jpeg", "video": "video/mp4", "audio": "audio/mpeg"}
    ct = mt_map.get(media_type, "application/octet-stream")
    return FileResponse(local_path, media_type=ct, filename=a.filename)


@router.get("/api/assets/{asset_id}", summary="获取素材详情")
def get_asset(
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    a = db.query(Asset).filter(Asset.asset_id == asset_id, Asset.user_id == owner_user.id).first()
    if not a:
        raise HTTPException(404, detail="素材不存在")
    context = _resolve_asset_content_context(db, a)
    if _persist_asset_content_context(a, context):
        db.add(a)
        db.commit()
        db.refresh(a)
        context = _stored_asset_content_context(a)
    local_path = _asset_local_path(a)
    out = {
        "asset_id": a.asset_id,
        "filename": a.filename,
        "media_type": a.media_type,
        "file_size": a.file_size,
        "source_url": a.source_url,
        "preview_url": a.source_url or "",
        "cover_url": (a.source_url or "") if a.media_type == "image" else "",
        "open_url": a.source_url or "",
        "title": context.get("title", ""),
        "description": context.get("description", ""),
        "prompt": a.prompt or context.get("creative_prompt", ""),
        "creative_prompt": context.get("creative_prompt", "") or a.prompt or "",
        "tags": a.tags or context.get("tags", ""),
        "content_context": context,
        "created_at": a.created_at.isoformat() if a.created_at else "",
    }
    if local_path is not None:
        out["local_path"] = str(local_path)
    else:
        out["local_path"] = None
    return out


@router.delete("/api/assets/{asset_id}", summary="删除素材")
def delete_asset(
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    a = db.query(Asset).filter(Asset.asset_id == asset_id, Asset.user_id == owner_user.id).first()
    if not a:
        raise HTTPException(404, detail="素材不存在")
    local_path = _asset_local_path(a)
    if local_path is not None and local_path.exists():
        local_path.unlink()
    db.delete(a)
    db.commit()
    return {"ok": True}


@router.delete("/api/assets/{asset_id}/online-split-source", summary="清理 Online 切片原视频")
async def delete_online_split_source(
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    row = db.query(Asset).filter(Asset.asset_id == asset_id, Asset.user_id == owner_user.id).first()
    if not row:
        return {"ok": True, "already_deleted": True}
    meta = row.meta if isinstance(row.meta, dict) else {}
    if not meta.get("online_split_source"):
        raise HTTPException(status_code=409, detail="该素材不是 Online 切片中间原片")
    object_key = str(row.filename or "")
    db.delete(row)
    db.commit()
    deleted_from_tos = await _run_asset_upload_io(_delete_tos_object, object_key)
    return {"ok": True, "deleted_from_tos": bool(deleted_from_tos)}


@router.delete("/api/assets/{asset_id}/online-split-segment", summary="清理 Online 未完成切片")
async def delete_online_split_segment(
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user = online_user_for_mobile_user(db, current_user)
    row = db.query(Asset).filter(Asset.asset_id == asset_id, Asset.user_id == owner_user.id).first()
    if not row:
        return {"ok": True, "already_deleted": True}
    meta = row.meta if isinstance(row.meta, dict) else {}
    if not meta.get("video_segment") or not meta.get("split_job_id"):
        raise HTTPException(status_code=409, detail="该素材不是 Online 切片任务产物")
    object_key = str(row.filename or "")
    db.delete(row)
    db.commit()
    deleted_from_tos = await _run_asset_upload_io(_delete_tos_object, object_key)
    return {"ok": True, "deleted_from_tos": bool(deleted_from_tos)}
