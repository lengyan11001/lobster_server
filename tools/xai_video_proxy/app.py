import asyncio
import ipaddress
import logging
import mimetypes
import os
import re
import socket
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Dict
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse

ENV_PATH = Path(__file__).with_name(".env")
if ENV_PATH.exists():
    for raw_line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

app = FastAPI(title="xAI Video Proxy", version="1.0.0")
logger = logging.getLogger("xai-video-proxy")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _proxy_token() -> str:
    token = _env("XAI_PROXY_TOKEN")
    if not token:
        raise HTTPException(500, "XAI_PROXY_TOKEN is not configured")
    return token


def _upstream_key() -> str:
    key = _env("XAI_API_KEY")
    if not key:
        raise HTTPException(500, "XAI_API_KEY is not configured")
    return key


def _upstream_base() -> str:
    return _env("XAI_API_BASE", "https://api.x.ai").rstrip("/")


def _require_auth(authorization: str | None) -> None:
    if authorization != f"Bearer {_proxy_token()}":
        raise HTTPException(401, "Invalid proxy token")


def _upstream_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_upstream_key()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _upstream_file_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_upstream_key()}",
        "Accept": "application/json",
    }


# The xAI result URL is often reachable from this proxy host but not from an
# end user's network. Keep the download and TOS upload on the proxy host.
_TRANSFER_MAX_BYTES = int(os.environ.get("VIDEO_TRANSFER_MAX_BYTES", str(512 * 1024 * 1024)))
_TRANSFER_TIMEOUT = float(os.environ.get("VIDEO_TRANSFER_DOWNLOAD_TIMEOUT", "300"))
_TRANSFER_MAX_REDIRECTS = 5
_TRANSFER_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_TRANSFER_BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain"}
_TRANSFER_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal")
_XAI_TOS_URL_CACHE: Dict[str, str] = {}
_XAI_TOS_URL_CACHE_MAX = 1000
_XAI_INPUT_IMAGE_MAX_BYTES = int(os.environ.get("XAI_INPUT_IMAGE_MAX_BYTES", str(50 * 1024 * 1024)))
_XAI_INPUT_IMAGE_DOWNLOAD_TIMEOUT = float(os.environ.get("XAI_INPUT_IMAGE_DOWNLOAD_TIMEOUT", "90"))
_XAI_INPUT_IMAGE_SPOOL_BYTES = int(os.environ.get("XAI_INPUT_IMAGE_SPOOL_BYTES", str(8 * 1024 * 1024)))
_XAI_INPUT_FILES_BY_REQUEST: Dict[str, str] = {}
_XAI_INPUT_FILES_BY_REQUEST_MAX = 1000


def _transfer_token() -> str:
    token = _env("VIDEO_TRANSFER_TOKEN")
    if not token:
        raise HTTPException(503, "VIDEO_TRANSFER_TOKEN is not configured")
    return token


def _require_transfer_auth(value: str | None) -> None:
    if value != _transfer_token():
        raise HTTPException(401, "Invalid video transfer token")


def _blocked_transfer_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


async def _validate_transfer_url(raw_url: str) -> None:
    parsed = urlparse(raw_url)
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise HTTPException(400, "source URL must be an http/https URL")
    if hostname in _TRANSFER_BLOCKED_HOSTNAMES or hostname.endswith(_TRANSFER_BLOCKED_SUFFIXES):
        raise HTTPException(400, "private or local source URL is not allowed")
    if _blocked_transfer_ip(hostname):
        raise HTTPException(400, "private or local source URL is not allowed")
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise HTTPException(400, "source hostname cannot be resolved") from exc
    if not addresses or any(_blocked_transfer_ip(item[4][0]) for item in addresses):
        raise HTTPException(400, "source URL resolves to a private or local address")


def _transfer_filename(raw: str | None, content_type: str, source_url: str) -> str:
    candidate = (raw or "").strip() or Path(urlparse(source_url).path).name
    candidate = _TRANSFER_FILENAME_RE.sub("-", candidate).strip(".-")[:160] or "video"
    if "." not in candidate:
        candidate += mimetypes.guess_extension(content_type) or ".mp4"
    return candidate


def _tos_config() -> Dict[str, str]:
    config = {
        "access_key": _env("TOS_ACCESS_KEY"),
        "secret_key": _env("TOS_SECRET_KEY"),
        "endpoint": _env("TOS_ENDPOINT"),
        "region": _env("TOS_REGION"),
        "bucket": _env("TOS_BUCKET"),
        "public_domain": _env("TOS_PUBLIC_DOMAIN").rstrip("/"),
    }
    missing = [key for key, value in config.items() if not value]
    if missing:
        raise HTTPException(503, f"TOS configuration is missing: {','.join(missing)}")
    return config


def _upload_transfer_file(path: Path, object_key: str, content_type: str) -> str:
    try:
        import tos
    except ImportError as exc:
        raise HTTPException(503, "TOS SDK is not installed on the proxy") from exc
    config = _tos_config()
    client = tos.TosClientV2(
        config["access_key"], config["secret_key"], config["endpoint"], config["region"]
    )
    with path.open("rb") as stream:
        try:
            client.put_object(
                config["bucket"],
                object_key,
                content=stream,
                content_type=content_type,
                content_disposition="inline",
            )
        except TypeError:
            stream.seek(0)
            client.put_object(
                config["bucket"], object_key, content=stream, content_type=content_type
            )
    return f"{config['public_domain']}/{object_key}"


async def _download_transfer_file(
    source_url: str, filename: str | None, requested_type: str | None
) -> tuple[Path, str, int, str]:
    current_url = source_url
    temp_path: Path | None = None
    total = 0
    content_type = (requested_type or "").split(";", 1)[0].strip().lower()
    try:
        timeout = httpx.Timeout(_TRANSFER_TIMEOUT, connect=30)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "Lobster-Video-Transfer/1.0", "Accept": "video/*,application/octet-stream;q=0.9,*/*;q=0.1"},
        ) as client:
            for redirect_index in range(_TRANSFER_MAX_REDIRECTS + 1):
                await _validate_transfer_url(current_url)
                response = await client.send(client.build_request("GET", current_url), stream=True)
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    await response.aclose()
                    if not location or redirect_index >= _TRANSFER_MAX_REDIRECTS:
                        raise HTTPException(502, "source URL redirect limit exceeded")
                    current_url = urljoin(current_url, location)
                    continue
                if response.status_code >= 400:
                    detail = (await response.aread())[:300].decode("utf-8", "replace")
                    await response.aclose()
                    raise HTTPException(502, f"source download HTTP {response.status_code}: {detail}")

                content_type = content_type or response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                declared_size = response.headers.get("content-length")
                if declared_size and int(declared_size) > _TRANSFER_MAX_BYTES:
                    await response.aclose()
                    raise HTTPException(413, f"source video exceeds {_TRANSFER_MAX_BYTES} bytes")
                suffix = Path(_transfer_filename(filename, content_type or "video/mp4", current_url)).suffix or ".mp4"
                handle = tempfile.NamedTemporaryFile(prefix="video-transfer-", suffix=suffix, delete=False)
                temp_path = Path(handle.name)
                try:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        total += len(chunk)
                        if total > _TRANSFER_MAX_BYTES:
                            raise HTTPException(413, f"source video exceeds {_TRANSFER_MAX_BYTES} bytes")
                        handle.write(chunk)
                finally:
                    handle.close()
                    await response.aclose()
                if total <= 0:
                    raise HTTPException(502, "source video is empty")
                safe_name = _transfer_filename(filename, content_type or "video/mp4", current_url)
                return temp_path, content_type or "video/mp4", total, safe_name
            raise HTTPException(502, "source URL redirect limit exceeded")
    except HTTPException:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise HTTPException(502, f"source video download failed: {type(exc).__name__}") from exc


async def _download_xai_input_image(source_url: str) -> tuple[BinaryIO, str, int, str]:
    """Fetch a public reference image once on the proxy, keeping it spooled."""
    current_url = source_url
    total = 0
    spool: BinaryIO | None = None
    content_type = ""
    try:
        timeout = httpx.Timeout(_XAI_INPUT_IMAGE_DOWNLOAD_TIMEOUT, connect=30)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": "Lobster-xAI-Input/1.0",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        ) as client:
            for redirect_index in range(_TRANSFER_MAX_REDIRECTS + 1):
                await _validate_transfer_url(current_url)
                response = await client.send(client.build_request("GET", current_url), stream=True)
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    await response.aclose()
                    if not location or redirect_index >= _TRANSFER_MAX_REDIRECTS:
                        raise HTTPException(502, "source image redirect limit exceeded")
                    current_url = urljoin(current_url, location)
                    continue
                if response.status_code >= 400:
                    detail = (await response.aread())[:300].decode("utf-8", "replace")
                    await response.aclose()
                    raise HTTPException(502, f"source image download HTTP {response.status_code}: {detail}")

                content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                declared_size = response.headers.get("content-length")
                if declared_size:
                    try:
                        if int(declared_size) > _XAI_INPUT_IMAGE_MAX_BYTES:
                            await response.aclose()
                            raise HTTPException(413, f"source image exceeds {_XAI_INPUT_IMAGE_MAX_BYTES} bytes")
                    except ValueError:
                        pass

                spool = tempfile.SpooledTemporaryFile(
                    max_size=_XAI_INPUT_IMAGE_SPOOL_BYTES,
                    mode="w+b",
                )
                try:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        total += len(chunk)
                        if total > _XAI_INPUT_IMAGE_MAX_BYTES:
                            raise HTTPException(413, f"source image exceeds {_XAI_INPUT_IMAGE_MAX_BYTES} bytes")
                        spool.write(chunk)
                finally:
                    await response.aclose()
                if total <= 0:
                    raise HTTPException(502, "source image is empty")
                spool.seek(0)
                filename = _transfer_filename(None, content_type or "image/png", current_url)
                return spool, content_type or "image/png", total, filename
            raise HTTPException(502, "source image redirect limit exceeded")
    except HTTPException:
        if spool:
            spool.close()
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        if spool:
            spool.close()
        raise HTTPException(502, f"source image download failed: {type(exc).__name__}") from exc


async def _upload_xai_input_image(source_url: str) -> tuple[str, Dict[str, Any]]:
    source_started = time.perf_counter()
    stream, content_type, source_bytes, filename = await _download_xai_input_image(source_url)
    source_fetch_ms = round((time.perf_counter() - source_started) * 1000, 1)
    upload_started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(180, connect=30),
            follow_redirects=True,
            trust_env=False,
        ) as client:
            response = await client.post(
                f"{_upstream_base()}/v1/files",
                headers=_upstream_file_headers(),
                files={"file": (filename, stream, content_type)},
            )
    finally:
        stream.close()
    upload_ms = round((time.perf_counter() - upload_started) * 1000, 1)
    if response.status_code >= 400:
        raise HTTPException(
            502,
            f"xAI input file upload HTTP {response.status_code}: {(response.text or '')[:500]}",
        )
    try:
        payload = response.json() if response.content else {}
    except ValueError as exc:
        raise HTTPException(502, "xAI input file upload returned invalid JSON") from exc
    file_id = str(payload.get("id") or payload.get("file_id") or "").strip()
    if not file_id:
        raise HTTPException(502, "xAI input file upload returned no file id")
    meta = {
        "mode": "proxy_uploaded_file_id",
        "source_bytes": source_bytes,
        "source_fetch_ms": source_fetch_ms,
        "upload_ms": upload_ms,
        "file_id_prefix": f"{file_id[:18]}..." if len(file_id) > 18 else file_id,
        "_uploaded_file_id": file_id,
    }
    logger.info(
        "xAI input uploaded file_id=%s bytes=%s source_fetch_ms=%s upload_ms=%s",
        meta["file_id_prefix"],
        source_bytes,
        source_fetch_ms,
        upload_ms,
    )
    return file_id, meta


async def _delete_xai_input_file(file_id: str) -> bool:
    safe_id = str(file_id or "").strip()
    if not safe_id:
        return True
    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            response = await client.delete(
                f"{_upstream_base()}/v1/files/{safe_id}",
                headers=_upstream_file_headers(),
            )
        if response.status_code >= 400:
            logger.warning(
                "xAI input file cleanup failed file_id=%s http=%s",
                f"{safe_id[:18]}...",
                response.status_code,
            )
            return False
        logger.info("xAI input file cleanup completed file_id=%s", f"{safe_id[:18]}...")
        return True
    except Exception as exc:
        logger.warning("xAI input file cleanup failed file_id=%s err=%r", f"{safe_id[:18]}...", exc)
        return False


def _extract_image_input(source: Dict[str, Any]) -> tuple[str, str]:
    image = source.get("image")
    if isinstance(image, dict):
        file_id = str(image.get("file_id") or "").strip()
        if file_id:
            return "file_id", file_id
        image_url = str(image.get("url") or "").strip()
        if image_url:
            return "url", image_url
    elif image:
        return "url", str(image).strip()

    file_id = str(source.get("image_file_id") or "").strip()
    if file_id:
        return "file_id", file_id
    image_url = str(source.get("image_url") or "").strip()
    if image_url:
        return "url", image_url

    images = source.get("images")
    if isinstance(images, list):
        for item in images:
            if isinstance(item, dict):
                file_id = str(item.get("file_id") or "").strip()
                if file_id:
                    return "file_id", file_id
                image_url = str(item.get("url") or "").strip()
                if image_url:
                    return "url", image_url
            else:
                value = str(item or "").strip()
                if value:
                    return "url", value
    return "", ""


def _extract_xai_video_url(payload: Dict[str, Any]) -> str:
    video = payload.get("video")
    if isinstance(video, dict):
        for key in ("url", "video_url", "download_url", "content_url"):
            value = str(video.get(key) or "").strip()
            if value.startswith(("http://", "https://")):
                return value
    for key in ("video_url", "videoUrl", "output_url", "outputUrl", "download_url", "downloadUrl"):
        value = str(payload.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    return ""


async def _mirror_xai_video_to_tos(payload: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    source_url = _extract_xai_video_url(payload)
    if not source_url:
        return payload

    public_domain = _env("TOS_PUBLIC_DOMAIN").rstrip("/")
    if public_domain and source_url.startswith(public_domain + "/"):
        return payload

    cache_key = f"{request_id}:{source_url}"
    cached_url = _XAI_TOS_URL_CACHE.get(cache_key)
    video = payload.get("video")
    if cached_url:
        if isinstance(video, dict):
            video["source_url"] = source_url
            video["url"] = cached_url
        payload["video_url"] = cached_url
        payload["tos_url"] = cached_url
        return payload

    temp_path: Path | None = None
    try:
        temp_path, content_type, size, filename = await _download_transfer_file(
            source_url,
            f"xai-{request_id}.mp4",
            "video/mp4",
        )
        prefix = _env("TOS_PREFIX", "assets").strip().strip("/") or "assets"
        object_key = f"{prefix}/{uuid.uuid4().hex}-{filename}"
        tos_url = await asyncio.to_thread(
            _upload_transfer_file, temp_path, object_key, content_type
        )
        _XAI_TOS_URL_CACHE[cache_key] = tos_url
        while len(_XAI_TOS_URL_CACHE) > _XAI_TOS_URL_CACHE_MAX:
            _XAI_TOS_URL_CACHE.pop(next(iter(_XAI_TOS_URL_CACHE)))
        if isinstance(video, dict):
            video["source_url"] = source_url
            video["url"] = tos_url
        payload["video_url"] = tos_url
        payload["tos_url"] = tos_url
        payload["source_video_url"] = source_url
        payload["tos_object_key"] = object_key
        logger.info(
            "xAI video mirrored to TOS request_id=%s size=%s object_key=%s",
            request_id,
            size,
            object_key,
        )
    except Exception as exc:
        # Preserve the upstream response so task status remains truthful. The
        # original URL is still useful for diagnostics if TOS is unavailable.
        payload["tos_transfer_error"] = str(exc)[:300]
        logger.exception("xAI video TOS transfer failed request_id=%s", request_id)
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)
    return payload


async def _normalize_body(source: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    prompt = str(source.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "missing prompt")
    try:
        duration = int(float(source.get("duration") or source.get("seconds") or 10))
    except (TypeError, ValueError):
        raise HTTPException(400, "duration must be an integer")
    if duration <= 0:
        raise HTTPException(400, "duration must be positive")
    body: Dict[str, Any] = {
        "model": _env("XAI_VIDEO_MODEL", "grok-imagine-video-1.5"),
        "prompt": prompt,
        "duration": duration,
    }
    image_kind, image_value = _extract_image_input(source)
    input_meta: Dict[str, Any] = {"mode": "text_to_video"}
    if image_kind == "file_id":
        body["image"] = {"file_id": image_value}
        input_meta = {
            "mode": "provided_file_id",
            "file_id_prefix": f"{image_value[:18]}..." if len(image_value) > 18 else image_value,
        }
    elif image_kind == "url":
        file_id, input_meta = await _upload_xai_input_image(image_value)
        body["image"] = {"file_id": file_id}
    return body, input_meta


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "xai-video-proxy",
        "transfer_configured": bool(_env("VIDEO_TRANSFER_TOKEN")),
        "tos_configured": all(
            _env(name)
            for name in (
                "TOS_ACCESS_KEY",
                "TOS_SECRET_KEY",
                "TOS_ENDPOINT",
                "TOS_REGION",
                "TOS_BUCKET",
                "TOS_PUBLIC_DOMAIN",
            )
        ),
    }


@app.post("/media/transfer-to-tos")
async def transfer_to_tos(
    body: Dict[str, Any],
    x_video_transfer_token: str | None = Header(default=None),
) -> Dict[str, Any]:
    """Download a provider video from this host and return its public TOS URL."""
    _require_transfer_auth(x_video_transfer_token)
    source_url = str(body.get("url") or "").strip()
    if len(source_url) < 8 or len(source_url) > 8192:
        raise HTTPException(400, "url is required")
    filename = body.get("filename")
    if filename is not None and not isinstance(filename, str):
        raise HTTPException(400, "filename must be a string")
    content_type = body.get("content_type")
    if content_type is not None and not isinstance(content_type, str):
        raise HTTPException(400, "content_type must be a string")

    temp_path: Path | None = None
    try:
        temp_path, detected_type, size, safe_name = await _download_transfer_file(
            source_url, filename, content_type
        )
        prefix = _env("TOS_PREFIX", "proxy-video").strip().strip("/") or "proxy-video"
        object_key = f"{prefix}/{uuid.uuid4().hex}-{safe_name}"
        try:
            tos_url = await asyncio.to_thread(
                _upload_transfer_file, temp_path, object_key, detected_type
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("TOS upload failed object_key=%s", object_key)
            detail = str(exc).lower()
            if "accessdenied" in detail or "access denied" in detail or "403" in detail:
                raise HTTPException(
                    502,
                    "TOS upload was denied; check the configured AK/SK write permission for the bucket",
                ) from exc
            raise HTTPException(502, "TOS upload failed") from exc
        return {
            "ok": True,
            "source_url": source_url,
            "tos_url": tos_url,
            "object_key": object_key,
            "filename": safe_name,
            "content_type": detected_type,
            "size": size,
        }
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


@app.post("/xai/v1/videos/generations")
async def submit(body: Dict[str, Any], authorization: str | None = Header(default=None)):
    _require_auth(authorization)
    upstream_body, input_meta = await _normalize_body(body)
    uploaded_file_id = str(input_meta.pop("_uploaded_file_id", "") or "").strip()
    submit_started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True, trust_env=False) as client:
            response = await client.post(
                f"{_upstream_base()}/v1/videos/generations",
                headers=_upstream_headers(),
                json=upstream_body,
            )
    except (httpx.HTTPError, OSError) as exc:
        if uploaded_file_id:
            await _delete_xai_input_file(uploaded_file_id)
        raise HTTPException(502, f"xAI upstream submit failed: {type(exc).__name__}") from exc
    submit_ms = round((time.perf_counter() - submit_started) * 1000, 1)
    if response.status_code >= 400:
        if uploaded_file_id:
            await _delete_xai_input_file(uploaded_file_id)
        raise HTTPException(response.status_code, f"xAI upstream HTTP {response.status_code}: {(response.text or '')[:700]}")
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        if uploaded_file_id:
            await _delete_xai_input_file(uploaded_file_id)
        raise HTTPException(502, "xAI upstream returned invalid JSON")
    if isinstance(payload, dict):
        request_id = str(payload.get("request_id") or payload.get("id") or "").strip()
        if request_id:
            payload.setdefault("request_id", request_id)
            payload.setdefault("task_id", request_id)
            if uploaded_file_id:
                _XAI_INPUT_FILES_BY_REQUEST[request_id] = uploaded_file_id
                while len(_XAI_INPUT_FILES_BY_REQUEST) > _XAI_INPUT_FILES_BY_REQUEST_MAX:
                    _XAI_INPUT_FILES_BY_REQUEST.pop(next(iter(_XAI_INPUT_FILES_BY_REQUEST)))
        elif uploaded_file_id:
            # A successful video submission must return a request ID. Without
            # one, there is no later poll through which this temp file can be
            # cleaned up.
            await _delete_xai_input_file(uploaded_file_id)
            logger.warning("xAI video submission returned no request_id; temporary input file removed")
        total_submit_path_ms = round(
            float(input_meta.get("source_fetch_ms") or 0)
            + float(input_meta.get("upload_ms") or 0)
            + submit_ms,
            1,
        )
        logger.info(
            "xAI video submitted request_id=%s input_mode=%s source_bytes=%s "
            "source_fetch_ms=%s upload_ms=%s submit_ms=%s total_submit_path_ms=%s",
            f"{request_id[:18]}..." if request_id else "<missing>",
            input_meta.get("mode"),
            input_meta.get("source_bytes"),
            input_meta.get("source_fetch_ms"),
            input_meta.get("upload_ms"),
            submit_ms,
            total_submit_path_ms,
        )
    return payload


@app.get("/xai/v1/videos/{request_id}")
async def poll(request_id: str, authorization: str | None = Header(default=None)):
    _require_auth(authorization)
    safe_id = str(request_id or "").strip()
    if not safe_id:
        raise HTTPException(400, "missing request_id")
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, trust_env=False) as client:
        response = await client.get(
            f"{_upstream_base()}/v1/videos/{safe_id}",
            headers={"Authorization": f"Bearer {_upstream_key()}", "Accept": "application/json"},
        )
    if response.status_code >= 400:
        raise HTTPException(response.status_code, f"xAI upstream HTTP {response.status_code}: {(response.text or '')[:700]}")
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        raise HTTPException(502, "xAI upstream returned invalid JSON")
    if isinstance(payload, dict):
        payload.setdefault("request_id", safe_id)
        payload.setdefault("task_id", safe_id)
        status = str(payload.get("status") or "").strip().lower()
        if status in {"done", "failed", "expired", "error"}:
            uploaded_file_id = _XAI_INPUT_FILES_BY_REQUEST.pop(safe_id, "")
            if uploaded_file_id:
                cleanup_ok = await _delete_xai_input_file(uploaded_file_id)
                logger.info(
                    "xAI input file cleanup request_id=%s result=%s",
                    f"{safe_id[:18]}..." if len(safe_id) > 18 else safe_id,
                    "ok" if cleanup_ok else "failed",
                )
        if _extract_xai_video_url(payload):
            payload = await _mirror_xai_video_to_tos(payload, safe_id)
    return payload


@app.get("/xai/v1/videos/{request_id}/content")
async def content(request_id: str, authorization: str | None = Header(default=None)):
    """Compatibility endpoint for callers that download the completed video path."""
    payload = await poll(request_id, authorization)
    video_url = _extract_xai_video_url(payload)
    if not video_url:
        raise HTTPException(404, "xAI video content is not available")
    return RedirectResponse(video_url, status_code=307)
