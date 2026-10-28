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


def _is_video(url: str, content_type: str = "") -> bool:
    if "video/" in (content_type or ""):
        return True
    path = urlparse(url).path.lower()
    return path.endswith((".mp4", ".mov", ".m4v", ".webm", ".avi"))


def _cache_path(url: str, suffix: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return _CACHE_DIR / f"{key}{suffix}"


@router.get("/api/media/thumb", summary="视频首帧/图片缩略图（服务器代理，规避 Referer 拦截）")
def media_thumb(url: str = Query(...)):
    src = _check_url(url)
    jpg = _cache_path(src, ".jpg")
    png = _cache_path(src, ".png")
    for cached in (jpg, png):
        if cached.exists() and cached.stat().st_size > 0:
            return FileResponse(cached, media_type="image/jpeg" if cached.suffix == ".jpg" else "image/png")

    if _is_video(src):
        out = jpg
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-headers", "Referer: https://bhzn.top/\r\nUser-Agent: " + _UA + "\r\n",
            "-ss", "0.5", "-i", src, "-frames:v", "1",
            "-vf", "scale=480:-2", "-q:v", "5", str(out),
        ]
        try:
            subprocess.run(cmd, timeout=60, check=True)
        except Exception as exc:
            logger.warning("[media-thumb] ffmpeg failed url=%s err=%s", src[:120], exc)
            try:
                cmd[cmd.index("0.5")] = "0"
                subprocess.run(cmd, timeout=60, check=True)
            except Exception as exc2:
                logger.warning("[media-thumb] ffmpeg retry failed url=%s err=%s", src[:120], exc2)
                raise HTTPException(422, "无法生成视频缩略图") from exc2
        if not out.exists() or out.stat().st_size == 0:
            raise HTTPException(422, "缩略图为空")
        return FileResponse(out, media_type="image/jpeg")

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