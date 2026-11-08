"""媒体代理 / 视频首帧缩略图。

小程序里直接加载 TOS / OSS 链接会被 Referer 拦截（servicewechat.com → 403），
这里由服务器带正常 Referer 拉取，统一出口，避免前端拿不到缩略图。
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_CACHE_DIR = Path(os.environ.get("MEDIA_THUMB_DIR") or "/opt/lobster-server/backups/media_thumbs")
_ALLOWED_SUFFIX = (".volces.com", ".aliyuncs.com", ".myqcloud.com", "bhzn.top", ".51sux.com", ".cos.ap-")
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"


def _check_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw.startswith(("http://", "https://")):
        raise HTTPException(400, "url 必须是 http(s)")
    host = (urlparse(raw).hostname or "").lower()
    if not host or not any(host == s.lstrip(".") or host.endswith(s) for s in _ALLOWED_SUFFIX):
        raise HTTPException(400, "不支持的域名")
    return raw




def _is_jpeg_file(path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(2)
        return head == b"\xff\xd8"
    except Exception:
        return False

def _is_video(url: str, content_type: str = "") -> bool:
    if "video/" in (content_type or ""):
        return True
    path = urlparse(url).path.lower()
    return path.endswith((".mp4", ".mov", ".m4v", ".webm", ".avi"))


# 2026-11-01：缩略图 ffmpeg 并发与缓存清理。
# 国内 4 核机后端常驻约 1 核；列表页一次刷几十个视频会同时抽首帧，
# 无限并发会把 API 挤到超时。这里全局最多 2 个 ffmpeg，同一 URL 只算一次。
_FFMPEG_SEM = threading.BoundedSemaphore(2)
_KEY_LOCKS: dict[str, threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()
_CACHE_TTL_SECONDS = 7 * 24 * 3600
_last_cleanup_at = 0.0


def _key_lock(key: str) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[key] = lock
        return lock


def _cleanup_cache_once() -> None:
    """每小时最多清一次：删 7 天没被访问的缩略图，别把备份盘撑满。"""
    global _last_cleanup_at
    now = time.time()
    if now - _last_cleanup_at < 3600:
        return
    _last_cleanup_at = now
    try:
        cutoff = now - _CACHE_TTL_SECONDS
        for path in _CACHE_DIR.glob("*"):
            try:
                if path.is_file() and path.stat().st_atime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
    except Exception as exc:  # noqa: BLE001
        logger.info("[media-thumb] cache cleanup skipped: %s", exc)


def _cache_path(url: str, suffix: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return _CACHE_DIR / f"{key}{suffix}"


def _thumb_from_video(src: str, jpg: Path, seek: float = 0.5) -> FileResponse:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-headers", "Referer: https://bhzn.top/\r\nUser-Agent: " + _UA + "\r\n",
        "-ss", str(seek), "-i", src, "-frames:v", "1",
        "-vf", "scale=480:-2", "-q:v", "5", str(jpg),
    ]
    with _FFMPEG_SEM:
        try:
            subprocess.run(cmd, timeout=60, check=True)
        except Exception as exc:
            logger.warning("[media-thumb] ffmpeg failed url=%s err=%s", src[:120], exc)
            try:
                cmd[cmd.index(str(seek))] = "0"
                subprocess.run(cmd, timeout=60, check=True)
            except Exception as exc2:
                logger.warning("[media-thumb] ffmpeg retry failed url=%s err=%s", src[:120], exc2)
                raise HTTPException(422, "无法生成视频缩略图") from exc2
    if not jpg.exists() or jpg.stat().st_size == 0:
        raise HTTPException(422, "缩略图为空")
    return FileResponse(jpg, media_type="image/jpeg")


def _thumb_from_image(src: str) -> FileResponse:
    try:
        with httpx.Client(timeout=30, follow_redirects=True, headers={"Referer": "https://bhzn.top/", "User-Agent": _UA}) as client:
            resp = client.get(src)
        if resp.status_code != 200 or not resp.content:
            raise HTTPException(422, f"拉取图片失败 {resp.status_code}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, f"拉取图片失败：{exc}") from exc
    suffix = ".png" if "png" in (resp.headers.get("content-type") or "") else ".jpg"
    out = _cache_path(src, suffix)
    out.write_bytes(resp.content)
    return FileResponse(out, media_type="image/png" if suffix == ".png" else "image/jpeg")


@router.get("/api/media/thumb", summary="视频首帧/图片缩略图（服务器代理，规避 Referer 拦截）")
def media_thumb(url: str = Query(...)):
    src = _check_url(url)
    jpg = _cache_path(src, ".jpg")
    png = _cache_path(src, ".png")
    _cleanup_cache_once()
    for cached in (jpg, png):
        if cached.exists() and cached.stat().st_size > 0:
            if (cached.suffix != ".jpg" or _is_jpeg_file(cached)) and cached.stat().st_size <= 1500000:
                return FileResponse(cached, media_type="image/jpeg" if cached.suffix == ".jpg" else "image/png")
            try:
                cached.unlink()
            except OSError:
                pass

    with _key_lock(jpg.name):
        for cached in (jpg, png):
            if cached.exists() and cached.stat().st_size > 0:
                if (cached.suffix != ".jpg" or _is_jpeg_file(cached)) and cached.stat().st_size <= 1500000:
                    return FileResponse(cached, media_type="image/jpeg" if cached.suffix == ".jpg" else "image/png")
                try:
                    cached.unlink()
                except OSError:
                    pass

        # 通过 content-type 判断视频：素材 URL 带 token 后没有扩展名，不能只看后缀
        content_type = ""
        try:
            # 不能用 HEAD：本站 /api/assets/file 只允许 GET，会返回 405 不跟随跳转
            with httpx.Client(timeout=10, follow_redirects=True, headers={"Referer": "https://bhzn.top/", "User-Agent": _UA}) as client:
                with client.stream("GET", src) as resp:
                    content_type = (resp.headers.get("content-type") or "").lower()
        except Exception:
            content_type = ""

        try:
            is_image = content_type.startswith("image/")
            return _thumb_from_video(src, jpg, 0 if is_image else 0.5)
        except HTTPException:
            return _thumb_from_image(src)


@router.get("/api/media/proxy", summary="媒体流代理（支持 Range，供小程序预览视频）")
def media_proxy(url: str = Query(...), range_header: Optional[str] = Header(None, alias="Range")):
    src = _check_url(url)
    headers = {"Referer": "https://bhzn.top/", "User-Agent": _UA}
    if range_header:
        headers["Range"] = range_header
    client = httpx.Client(timeout=None, follow_redirects=True, headers=headers)
    try:
        resp = client.send(client.build_request("GET", src), stream=True)
    except Exception as exc:
        client.close()
        raise HTTPException(502, f"拉取媒体失败：{exc}") from exc
    if resp.status_code >= 400:
        client.close()
        raise HTTPException(resp.status_code, "上游媒体不可用")

    content_type = resp.headers.get("content-type") or "application/octet-stream"
    out_headers = {"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=600"}
    if resp.headers.get("content-length"):
        out_headers["Content-Length"] = resp.headers["content-length"]
    if resp.headers.get("content-range"):
        out_headers["Content-Range"] = resp.headers["content-range"]

    def _iter():
        try:
            for chunk in resp.iter_bytes(65536):
                yield chunk
        finally:
            resp.close()
            client.close()

    return StreamingResponse(_iter(), status_code=resp.status_code, media_type=content_type, headers=out_headers)