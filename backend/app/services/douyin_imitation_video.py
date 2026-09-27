"""抖音信息台「做同款（换人）」：榜单原视频 + 用户上传的单人图 → wan animate-mix 换人。

实测契约（2026-09-27，生产服务器、真实 key）：
- 取原视频：TikHub GET /api/v1/douyin/web/fetch_one_video_v2?aweme_id=<id>
  → data.aweme_detail.video.play_addr.url_list[0]（抖音 CDN，需带浏览器 UA 下载）
- 换人：DashScope POST /api/v1/services/aigc/image2video/video-synthesis
  {"model":"wan2.2-animate-mix","input":{"image_url":..,"video_url":..,"watermark":false},"parameters":{"mode":"wan-std"}}
  查询 GET /api/v1/tasks/{task_id}
- 两个 URL 必须能被阿里云拉取：我们自己的域名会被拒（实测 "Download https://manage.bhzn.top/... refused"），
  所以图片和视频都先传到 TOS 再用 TOS 公网地址。
- 参考图必须是「单个真人」：否则报 InvalidImage.NoHuman
  （实测原文：The input image has no human body. Please upload other image with single person.）
- 参考视频 ≤30 秒（官方限制），这里默认裁到 DOUYIN_IMITATION_MAX_SECONDS（默认 15 秒）控成本。
"""
from __future__ import annotations

import logging
import os
import pathlib
import subprocess
import tempfile
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

DASHSCOPE_BASE = "https://dashscope.aliyuncs.com"
ANIMATE_ENDPOINT = "/api/v1/services/aigc/image2video/video-synthesis"
DEFAULT_MODEL = "wan2.2-animate-mix"
MAX_VIDEO_SECONDS = 30
DEFAULT_MAX_SECONDS = 15
MAX_SOURCE_BYTES = 400 * 1024 * 1024
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

FAIL_HINTS = {
    "InvalidImage.NoHuman": "这张图里没有检测到人物，请换一张只有一个人的清晰照片",
    "InvalidParameter.DataInspection": "素材没通过内容审核，请换一张图片或换一条视频",
}


def _dashscope_base() -> str:
    return (os.environ.get("DASHSCOPE_BASE_URL") or DASHSCOPE_BASE).strip().rstrip("/")


def _dashscope_key() -> str:
    return (os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("ALIYUN_DASHSCOPE_API_KEY") or "").strip()


def _tikhub_base() -> str:
    base = (os.environ.get("TIKHUB_API_BASE") or "https://api.tikhub.io").strip().rstrip("/")
    if base == "https://api.tikhub.dev":
        base = "https://api.tikhub.io"
    return base


def _tikhub_key() -> str:
    return (os.environ.get("TIKHUB_API_KEY") or "").strip()


def _model() -> str:
    return (os.environ.get("DOUYIN_IMITATION_MODEL") or DEFAULT_MODEL).strip()


def _max_seconds() -> int:
    try:
        value = int(float(os.environ.get("DOUYIN_IMITATION_MAX_SECONDS") or DEFAULT_MAX_SECONDS))
    except (TypeError, ValueError):
        value = DEFAULT_MAX_SECONDS
    return max(3, min(MAX_VIDEO_SECONDS, value))


def friendly_error(code: str, message: str) -> str:
    """把上游错误翻成用户能懂的话。"""
    raw = str(message or "").strip()
    for key, hint in FAIL_HINTS.items():
        if key and (key in raw or key == str(code or "").strip()):
            return hint
    if "ConnectionRefused" in raw or "Download" in raw and "refused" in raw:
        return "素材地址阿里云拉不到，请重试（会自动改走 TOS 地址）"
    return raw[:200] or "生成失败"


def pick_play_url(detail: Dict[str, Any]) -> str:
    """从抖音作品详情里挑一个可下载的播放地址。"""
    video = (detail or {}).get("video") or {}
    for key in ("play_addr", "play_addr_h264", "play_addr_265"):
        for url in ((video.get(key) or {}).get("url_list") or []):
            if isinstance(url, str) and url.startswith("http"):
                return url
    for item in (video.get("bit_rate") or []):
        for url in ((item.get("play_addr") or {}).get("url_list") or []):
            if isinstance(url, str) and url.startswith("http"):
                return url
    return ""


async def resolve_source_video(item_id: str) -> Dict[str, Any]:
    """按作品 id 取原视频直链（TikHub 计费 1 次）。"""
    clean_id = str(item_id or "").strip()
    if not clean_id.isdigit():
        return {"ok": False, "error": "缺少作品 id，无法取原视频"}
    key = _tikhub_key()
    if not key:
        return {"ok": False, "error": "服务端未配置 TIKHUB_API_KEY"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0), trust_env=False) as client:
            resp = await client.get(
                _tikhub_base() + "/api/v1/douyin/web/fetch_one_video_v2",
                params={"aweme_id": clean_id},
                headers={"Authorization": f"Bearer {key}"},
            )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"取原视频失败：{exc}"}
    if resp.status_code != 200:
        return {"ok": False, "error": f"取原视频失败：HTTP {resp.status_code}"}
    detail = ((resp.json() or {}).get("data") or {}).get("aweme_detail") or {}
    url = pick_play_url(detail)
    if not url:
        return {"ok": False, "error": "这条作品拿不到可下载的视频地址"}
    duration_ms = ((detail.get("video") or {}).get("duration") or 0)
    try:
        duration_ms = int(duration_ms)
    except (TypeError, ValueError):
        duration_ms = 0
    return {"ok": True, "url": url, "duration_ms": duration_ms,
            "desc": str(detail.get("desc") or "")[:80]}


async def _download(url: str, limit: int = MAX_SOURCE_BYTES) -> Tuple[Optional[bytes], str]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0), trust_env=False,
                                     follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": BROWSER_UA})
    except Exception as exc:  # noqa: BLE001
        return None, f"下载素材失败：{exc}"
    if resp.status_code != 200:
        return None, f"下载素材失败：HTTP {resp.status_code}"
    data = resp.content
    if len(data) > limit:
        return None, "素材文件过大"
    return data, ""


async def _upload_tos(data: bytes, suffix: str, content_type: str) -> Tuple[str, str]:
    """上传到 TOS，返回 (公网地址, 错误)。阿里云拉不到我们自己的域名，必须走 TOS。"""
    from ..api.assets import _run_asset_upload_io, _save_upload_file_or_tos

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
        fh.write(data)
        temp_path = pathlib.Path(fh.name)
    try:
        with temp_path.open("rb") as handle:
            _asset_id, _key, _size, public_url = await _run_asset_upload_io(
                _save_upload_file_or_tos, handle, suffix, content_type
            )
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass
    if not public_url:
        return "", "素材上传失败：TOS 没返回公网地址"
    return public_url, ""


def trim_video(data: bytes, max_seconds: int) -> Tuple[bytes, str]:
    """裁到 max_seconds（优先 -c copy，失败则重编码）。"""
    from pathlib import Path

    with tempfile.TemporaryDirectory() as folder:
        src = Path(folder) / "src"
        dst = Path(folder) / "dst.mp4"
        src.write_bytes(data)
        for args in (
            ["ffmpeg", "-y", "-i", str(src), "-t", str(max_seconds), "-c", "copy", str(dst)],
            ["ffmpeg", "-y", "-i", str(src), "-t", str(max_seconds), "-movflags", "+faststart", str(dst)],
        ):
            try:
                done = subprocess.run(args, capture_output=True, timeout=180)
            except Exception:  # noqa: BLE001
                continue
            if done.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
                return dst.read_bytes(), ""
        return data, ""


async def submit_imitation(image_url: str, source_video_url: str, *, mode: str = "wan-std") -> Dict[str, Any]:
    """提交换人任务：返回 {ok, task_id, ...} 或 {ok: False, error}。"""
    key = _dashscope_key()
    if not key:
        return {"ok": False, "error": "服务端未配置 DASHSCOPE_API_KEY"}
    image = str(image_url or "").strip()
    video = str(source_video_url or "").strip()
    if not image.startswith(("http://", "https://")) or not video.startswith(("http://", "https://")):
        return {"ok": False, "error": "素材地址无效"}
    body = {
        "model": _model(),
        "input": {"image_url": image, "video_url": video, "watermark": False},
        "parameters": {"mode": mode if mode in {"wan-std", "wan-pro"} else "wan-std"},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0), trust_env=False) as client:
            resp = await client.post(
                _dashscope_base() + ANIMATE_ENDPOINT,
                json=body,
                headers={"Authorization": f"Bearer {key}", "X-DashScope-Async": "enable",
                         "Content-Type": "application/json"},
            )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"提交换人任务失败：{exc}"}
    if resp.status_code != 200:
        try:
            detail = resp.json()
        except Exception:  # noqa: BLE001
            detail = {}
        return {"ok": False, "error": friendly_error(str(detail.get("code") or ""),
                                                     str(detail.get("message") or resp.text)[:200])}
    task_id = str(((resp.json().get("output") or {}) or {}).get("task_id") or "").strip()
    if not task_id:
        return {"ok": False, "error": "换人任务没有返回任务号"}
    logger.info("[douyin-imitation] submit model=%s task=%s", _model(), task_id)
    return {"ok": True, "task_id": task_id, "model": _model(), "mode": body["parameters"]["mode"]}


async def query_imitation(task_id: str) -> Dict[str, Any]:
    """查询换人任务：{ok, status, progress, video_url, fail_reason, done}。"""
    clean_id = str(task_id or "").strip()
    if not clean_id:
        return {"ok": False, "error": "缺少任务号"}
    key = _dashscope_key()
    if not key:
        return {"ok": False, "error": "服务端未配置 DASHSCOPE_API_KEY"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0), trust_env=False) as client:
            resp = await client.get(_dashscope_base() + "/api/v1/tasks/" + clean_id,
                                    headers={"Authorization": f"Bearer {key}"})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"查询换人任务失败：{exc}"}
    if resp.status_code != 200:
        return {"ok": False, "error": f"查询换人任务失败：HTTP {resp.status_code}"}
    try:
        output = ((resp.json() or {}).get("output") or {})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"解析换人结果失败：{exc}"}
    status = str(output.get("task_status") or "").upper()
    video_url = str(output.get("video_url") or "").strip()
    return {
        "ok": True,
        "task_id": clean_id,
        "status": status,
        "progress": "100%" if status == "SUCCEEDED" else "",
        "video_url": video_url if status == "SUCCEEDED" else "",
        "fail_reason": "" if status == "SUCCEEDED" else friendly_error(str(output.get("code") or ""),
                                                                      str(output.get("message") or "")),
        "done": status in {"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"},
    }


async def prepare_imitation(image_url: str, item_id: str) -> Dict[str, Any]:
    """把素材准备好并提交：用户图 + 榜单原视频都转到 TOS，再提交换人。"""
    image_raw = str(image_url or "").strip()
    if not image_raw.startswith(("http://", "https://")):
        return {"ok": False, "error": "参考图地址无效，请重新上传"}
    source = await resolve_source_video(item_id)
    if not source.get("ok"):
        return source
    video_bytes, err = await _download(source["url"])
    if err:
        return {"ok": False, "error": err}
    max_seconds = _max_seconds()
    trimmed, _warn = trim_video(video_bytes, max_seconds)
    video_tos, err = await _upload_tos(trimmed, ".mp4", "video/mp4")
    if err:
        return {"ok": False, "error": err}
    image_bytes, err = await _download(image_raw, limit=20 * 1024 * 1024)
    if err:
        return {"ok": False, "error": f"读取参考图失败：{err}"}
    suffix = ".png" if image_bytes[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    image_tos, err = await _upload_tos(image_bytes, suffix, "image/png" if suffix == ".png" else "image/jpeg")
    if err:
        return {"ok": False, "error": err}
    result = await submit_imitation(image_tos, video_tos)
    if not result.get("ok"):
        return result
    result.update({"source_desc": source.get("desc") or "", "video_seconds": max_seconds,
                   "image_url": image_tos, "video_url": video_tos})
    return result
