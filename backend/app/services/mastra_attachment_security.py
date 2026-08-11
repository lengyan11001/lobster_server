from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlparse

import httpx


BLOCKED_IMAGE_MESSAGE = (
    "AI 调度暂不支持 ICNS、JPEG XL、HEIF/HEIC 或 AVIF 图片，"
    "请转换为 JPG、PNG、WebP 或 GIF 后重试"
)

_BLOCKED_EXTENSIONS = {
    ".avif",
    ".avifs",
    ".heic",
    ".heics",
    ".heif",
    ".heifs",
    ".hif",
    ".icns",
    ".jxl",
}
_BLOCKED_CONTENT_TYPES = {
    "image/avif",
    "image/avif-sequence",
    "image/heic",
    "image/heic-sequence",
    "image/heif",
    "image/heif-sequence",
    "image/icns",
    "image/jxl",
    "image/x-icns",
}
_BLOCKED_HEIF_BRANDS = {
    b"avif",
    b"avis",
    b"heic",
    b"heis",
    b"heix",
    b"heim",
    b"hevc",
    b"hevs",
    b"hevx",
    b"mif1",
    b"msf1",
}
_JXL_CONTAINER_SIGNATURE = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
_MAX_PROBE_BYTES = 256


class UnsafeMastraImageError(ValueError):
    pass


def _suffix(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    path = unquote(parsed.path) if parsed.scheme or parsed.netloc else raw
    return Path(path).suffix.lower()


def blocked_image_format(
    *,
    filename: str = "",
    url: str = "",
    content_type: str = "",
    header: bytes = b"",
) -> str:
    if _suffix(filename) in _BLOCKED_EXTENSIONS or _suffix(url) in _BLOCKED_EXTENSIONS:
        return BLOCKED_IMAGE_MESSAGE

    normalized_content_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if normalized_content_type in _BLOCKED_CONTENT_TYPES:
        return BLOCKED_IMAGE_MESSAGE

    data = bytes(header or b"")[:_MAX_PROBE_BYTES]
    if data.startswith(b"icns") or data.startswith(b"\xff\x0a"):
        return BLOCKED_IMAGE_MESSAGE
    if data.startswith(_JXL_CONTAINER_SIGNATURE):
        return BLOCKED_IMAGE_MESSAGE
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brands = {data[8:12]}
        brands.update(data[offset : offset + 4] for offset in range(16, len(data) - 3, 4))
        if brands & _BLOCKED_HEIF_BRANDS:
            return BLOCKED_IMAGE_MESSAGE
    return ""


def assert_safe_mastra_image(**values: Any) -> None:
    reason = blocked_image_format(**values)
    if reason:
        raise UnsafeMastraImageError(reason)


def _looks_like_image(attachment: Mapping[str, Any]) -> bool:
    media_type = str(attachment.get("media_type") or "").strip().lower()
    content_type = str(attachment.get("content_type") or "").strip().lower()
    return (
        media_type == "image"
        or content_type.startswith("image/")
        or _suffix(str(attachment.get("name") or "")) in _BLOCKED_EXTENSIONS
        or _suffix(str(attachment.get("url") or "")) in _BLOCKED_EXTENSIONS
    )


async def assert_safe_remote_mastra_images(
    attachments: Iterable[Mapping[str, Any]],
) -> None:
    images = [item for item in attachments if _looks_like_image(item)]
    if not images:
        return

    timeout = httpx.Timeout(connect=6.0, read=8.0, write=6.0, pool=6.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        for attachment in images:
            filename = str(attachment.get("name") or "")
            url = str(attachment.get("url") or "").strip()
            content_type = str(attachment.get("content_type") or "")
            assert_safe_mastra_image(filename=filename, url=url, content_type=content_type)
            try:
                async with client.stream(
                    "GET",
                    url,
                    headers={"Range": f"bytes=0-{_MAX_PROBE_BYTES - 1}", "Accept-Encoding": "identity"},
                ) as response:
                    response.raise_for_status()
                    header = b""
                    async for chunk in response.aiter_bytes():
                        header += chunk[: _MAX_PROBE_BYTES - len(header)]
                        if len(header) >= _MAX_PROBE_BYTES:
                            break
                    response_content_type = response.headers.get("content-type", "")
            except UnsafeMastraImageError:
                raise
            except Exception as exc:
                raise UnsafeMastraImageError(
                    f"图片安全检查失败，无法读取素材：{filename or url[:120]}，请重新上传后重试"
                ) from exc
            assert_safe_mastra_image(
                filename=filename,
                url=url,
                content_type=response_content_type or content_type,
                header=header,
            )
