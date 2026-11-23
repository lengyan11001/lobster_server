"""口播驱动混剪：裁切 + 拼接 + 换音轨（服务端 ffmpeg，copy 优先）。"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import Asset, MontageJob, RecorderAudioRecord, User, UserContentRecord
from .assets import _gen_asset_id, _upload_to_tos
from .auth import get_current_user
from .mobile_identity import online_user_for_mobile_user
from ..services import device_tts

router = APIRouter()
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")


# Online 同款背景音乐库（TOS 公共素材，直接下载后与口播双音轨混音）
_BGM_BASE = "https://lobster-online-assets-2103871705.tos-cn-guangzhou.volces.com/assets/"
_MONTAGE_BGM = [
    ("\u7231\u4e0d\u91ca\u624b", "c8877058895c"),
    ("\u4e4c\u5170\u5df4\u6258\u7684\u591c", "f69fd79514c8"),
    ("\u6400\u6276", "6a4eb368e076"),
    ("\u5149\u8f89\u5c81\u6708", "5268b93f9941"),
    ("\u4f60\u662f\u98ce\u513f\u6211\u662f\u6c99", "23929b8355c4"),
    ("\u9752\u82b1", "762080df1a7a"),
    ("\u5077\u5fc3", "3d0d6b58112a"),
    ("\u91cd\u5927\u7684\u65b0\u95fb\u901a\u77e5", "fe15595e0ce2"),
    ("\u5171\u5b88\u6cb3\u5c71", "57e9eb26c2dd"),
    ("\u8fd9\u4e2a\u5c31\u53eb\u4e13\u4e1a", "909a0e86c332"),
    ("\u6c14\u8d28\u51fa\u573a", "a790fbef2b03"),
    ("\u4eca\u751f\u554a \u591a\u76f8\u89c1", "0b0b968f7534"),
    ("Victory", "2022144c0ba6"),
    ("\u6708\u4eae\u7167\u5c71\u5ddd", "bada613dd8da"),
    ("So let me know", "dd32d307c3f8"),
    ("\u6625\u98ce\u4f55\u65f6\u6765", "f374c3c1769d"),
    ("\u5343\u5343\u9619\u6b4c", "9e1960237a66"),
    ("\u7231\u7b11\u7684\u5973\u5b69", "1581cdaec400"),
    ("\u6f2b\u6b65\u4eba\u751f\u8def", "d4013e6c4919"),
]


class MontageBody(BaseModel):
    record_id: Optional[int] = None
    voice_url: str = ""
    asset_urls: List[str] = Field(default_factory=list)
    image_urls: List[str] = Field(default_factory=list)
    group: str = ""
    target_seconds: Optional[int] = None
    ratio: str = "9:16"
    title: str = ""
    template_style_id: str = ""
    template_name: str = ""
    bgm_url: str = ""
    bgm_name: str = ""
    bgm_volume: float = 0.24


def _run(args: List[str], timeout: int = 300) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise HTTPException(status_code=502, detail="ffmpeg 失败：" + (proc.stderr or "")[-300:])


def _probe(path: str) -> dict[str, Any]:
    proc = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
        capture_output=True, text=True, timeout=60,
    )
    try:
        data = json.loads(proc.stdout or "{}")
    except Exception:
        data = {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except Exception:
        duration = 0.0
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "vcodec": str(video.get("codec_name") or ""),
        "fps": str(video.get("r_frame_rate") or ""),
        "has_audio": bool(audio),
        "has_video": bool(video),
        "duration": duration,
    }


def _download(url: str, dst: Path, timeout: float = 120.0) -> Path:
    with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail="素材下载失败 HTTP %s" % resp.status_code)
            with open(dst, "wb") as fh:
                for chunk in resp.iter_bytes(65536):
                    fh.write(chunk)
    return dst


def _size_for_ratio(ratio: str) -> tuple[int, int]:
    if (ratio or "").strip() == "16:9":
        return 1280, 720
    if (ratio or "").strip() == "1:1":
        return 1080, 1080
    return 720, 1280


_BGM_LIBRARY_FILE = Path(__file__).resolve().parent.parent / "data" / "montage_bgm.json"


def _bgm_items() -> list[dict[str, Any]]:
    """优先读 data/montage_bgm.json（可直接追加整首歌），没有时回退到内置 10 秒循环音乐。"""
    try:
        payload = json.loads(_BGM_LIBRARY_FILE.read_text(encoding="utf-8"))
        rows = payload.get("items") if isinstance(payload, dict) else payload
        items: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            items.append({
                "key": str(row.get("key") or url.rsplit("/", 1)[-1]),
                "name": str(row.get("name") or "背景音乐"),
                "url": url,
                "volume": float(row.get("volume") if row.get("volume") is not None else 0.24),
            })
        if items:
            return items
    except Exception:  # noqa: BLE001 - 音乐库文件缺失时不影响接口
        pass
    return [
        {"key": media_id, "name": name, "url": _BGM_BASE + media_id + ".m4a", "volume": 0.24}
        for name, media_id in _MONTAGE_BGM
    ]


@router.get("/api/montage/bgm", summary="\u6df7\u526a\u80cc\u666f\u97f3\u4e50\u5217\u8868")
def montage_bgm_library() -> dict[str, Any]:
    return {"ok": True, "items": _bgm_items()}


def _prepare_segment(src: Path, dst: Path, seconds: float, size: tuple[int, int], reference: dict[str, Any], start: float = 0.0) -> Path:
    info = _probe(str(src))
    w, h = size
    # 只有本身就是目标分辨率/编码的片段才能直接 copy
    same = (
        info["vcodec"] == reference["vcodec"]
        and info["width"] == w
        and info["height"] == h
        and info["fps"] == reference["fps"]
    )
    take = "%.3f" % max(0.4, seconds)
    if same and start <= 0.05:
        args = [FFMPEG, "-y", "-ss", "0", "-t", take, "-i", str(src), "-an", "-c", "copy", str(dst)]
    else:
        # 9:16 成片为准：其他尺寸按比例完整缩放到画面内，上下/左右用黑底补齐
        args = [
            FFMPEG, "-y", "-ss", "%.3f" % max(0.0, start), "-t", take, "-i", str(src), "-an",
            "-vf", "scale=%d:%d:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,fps=30" % (w, h, w, h),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", str(dst),
        ]
    _run(args, timeout=180)
    return dst


def _prepare_image_segment(src: Path, dst: Path, seconds: float, size: tuple[int, int]) -> Path:
    """图片按固定 3 秒（或剩余时长）静帧成片；非 9:16 图片完整显示，上下补黑。"""
    w, h = size
    _run([
        FFMPEG, "-y", "-loop", "1", "-t", "%.3f" % max(0.5, seconds), "-i", str(src),
        "-vf", "scale=%d:%d:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,fps=30,format=yuv420p" % (w, h, w, h),
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", str(dst),
    ], timeout=180)
    return dst


def _mix_voice(
    video: Path,
    voice: Path,
    dst: Path,
    total: float,
    bgm: Optional[Path] = None,
    bgm_volume: float = 0.24,
) -> None:
    """把口播音轨混进成片。选了背景音乐就双音轨同时播放，BGM 循环铺满全片。"""
    if bgm is None:
        _run([
            FFMPEG, "-y", "-i", str(video), "-i", str(voice),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-t", "%.3f" % total, "-movflags", "+faststart", str(dst),
        ], timeout=300)
        return
    volume = min(1.0, max(0.0, float(bgm_volume if bgm_volume is not None else 0.24)))
    graph = (
        "[1:a:0]volume=1.0,aresample=48000,"
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[voice];"
        "[2:a:0]atrim=duration=%.3f,asetpts=PTS-STARTPTS,volume=%.3f,aresample=48000,"
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[bgm];"
        "[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2,"
        "loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
    ) % (max(0.1, float(total)), volume)
    _run([
        FFMPEG, "-y", "-i", str(video), "-i", str(voice),
        "-stream_loop", "-1", "-i", str(bgm),
        "-filter_complex", graph,
        "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-t", "%.3f" % total, "-movflags", "+faststart", str(dst),
    ], timeout=300)


def _render_montage(owner_id: int, body: MontageBody) -> dict[str, Any]:
    """同步渲染（在线程里跑）。自己开 session，避免长时间占用请求连接。"""
    started = time.time()
    db = SessionLocal()
    user = db.query(User).filter(User.id == int(owner_id)).first()
    if user is None:
        db.close()
        raise HTTPException(status_code=404, detail="用户不存在")
    # 后面要下载/跑 ffmpeg/轮询模板，可能几十秒；先把读事务提交掉，
    # 否则 PG 会 idle-in-transaction 超时把连接掐掉（之前 500 就是这个）
    db.commit()

    voice_path: Optional[Path] = None
    work = Path(tempfile.mkdtemp(prefix="montage_"))
    try:
        if body.record_id:
            row = (
                db.query(RecorderAudioRecord)
                .filter(RecorderAudioRecord.id == int(body.record_id), RecorderAudioRecord.user_id == int(user.id))
                .first()
            )
            if row is None:
                raise HTTPException(status_code=404, detail="录音不存在")
            raw = str(row.audio_path or "").strip()
            if raw and Path(raw).is_file():
                voice_path = Path(raw)
            elif str(row.source_url or "").startswith("http"):
                voice_path = _download(str(row.source_url), work / "voice.mp3")
            else:
                raise HTTPException(status_code=400, detail="这条录音没有可用的音频文件")
        elif body.voice_url.startswith("http"):
            voice_path = _download(body.voice_url, work / "voice.mp3")
        elif body.voice_url.startswith("data:"):
            # preview-tts 返回的是 data URL（base64 音频），直接落盘
            import base64 as _b64
            raw = body.voice_url.split(",", 1)[-1]
            try:
                blob = _b64.b64decode(raw)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail="语音数据解析失败") from exc
            voice_path = work / "voice.mp3"
            voice_path.write_bytes(blob)
        if voice_path is None:
            raise HTTPException(status_code=400, detail="请提供口播音频或录音")

        group_name = str(body.group or "").strip()
        urls = [u for u in (body.asset_urls or []) if isinstance(u, str) and u.startswith("http")][:12]
        images = [u for u in (body.image_urls or []) if isinstance(u, str) and u.startswith("http")][:30]
        if group_name and not urls and not images:
            rows = (
                db.query(Asset)
                .filter(Asset.user_id == int(user.id))
                .order_by(Asset.id.desc())
                .limit(300)
                .all()
            )
            picked_videos = []
            picked_images = []
            for row in rows:
                meta = row.meta if isinstance(row.meta, dict) else {}
                tags = str(getattr(row, "tags", "") or "")
                if group_name == str(meta.get("creative_candidate_group") or "") or group_name in tags:
                    url = str(row.source_url or "")
                    if not url.startswith("http"):
                        continue
                    if str(row.media_type or "").strip().lower() == "image":
                        picked_images.append(url)
                    elif str(row.media_type or "").strip().lower() == "video":
                        picked_videos.append(url)
            urls = (urls + picked_videos)[:60]
            images = (images + picked_images)[:60]
            db.commit()
        if not urls and not images:
            raise HTTPException(status_code=400, detail="请选择素材分组，或至少选一个视频/图片素材")

        voice_info = _probe(str(voice_path))
        voice_duration = float(voice_info.get("duration") or 0)
        if voice_duration <= 0:
            raise HTTPException(status_code=400, detail="口播音频无有效时长")

        total = float(body.target_seconds or 0) or voice_duration
        total = max(3.0, min(total, voice_duration if voice_duration > 0 else 60.0))

        bgm_path: Optional[Path] = None
        bgm_url = str(body.bgm_url or "").strip()
        if bgm_url.startswith(("http://", "https://")):
            suffix = Path(bgm_url.split("?")[0]).suffix.lower()
            if suffix not in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"):
                suffix = ".m4a"
            try:
                bgm_path = _download(bgm_url, work / ("bgm" + suffix), timeout=180.0)
            except Exception:  # noqa: BLE001 - 背景音乐拉取失败不应让整条混剪失败
                bgm_path = None

        import random
        seg_seconds = 3.0
        pool = [("video", u) for u in urls] + [("image", u) for u in images]
        random.shuffle(pool)
        segments: List[Path] = []
        reference: Optional[dict[str, Any]] = None
        acc = 0.0
        idx = 0
        size = _size_for_ratio(body.ratio)
        src_cache: dict[str, Optional[tuple[Path, dict[str, Any]]]] = {}
        queue: List[tuple[str, str]] = []
        fail_streak = 0
        max_fail = max(4, len(pool) * 2)
        while acc < total - 0.05 and idx < 240 and pool:
            if not queue:
                # 素材挑完还不够时长，就把整组重新洗牌继续用（可重复挑）
                queue = pool[:]
                random.shuffle(queue)
            kind, url = queue.pop()
            remaining = total - acc
            take = min(seg_seconds, remaining)
            if take < 0.05:
                break
            cached = src_cache.get(url)
            if url in src_cache and cached is None:
                # 已知不可用的素材（已删除/损坏），直接跳过取剩下的
                fail_streak += 1
                if fail_streak >= max_fail:
                    break
                idx += 1
                continue
            if kind == "image":
                if cached is None:
                    try:
                        suffix = Path(str(url).split("?")[0]).suffix.lower() or ".jpg"
                        src = _download(url, work / ("img_%03d%s" % (len(src_cache), suffix)))
                    except Exception:  # noqa: BLE001 - 素材被删了就跳过
                        src_cache[url] = None
                        fail_streak += 1
                        if fail_streak >= max_fail:
                            break
                        idx += 1
                        continue
                    cached = (src, {})
                    src_cache[url] = cached
                src, _ = cached
                try:
                    seg = _prepare_image_segment(src, work / ("seg_%03d.mp4" % idx), take, size)
                except Exception:  # noqa: BLE001
                    src_cache[url] = None
                    fail_streak += 1
                    if fail_streak >= max_fail:
                        break
                    idx += 1
                    continue
                segments.append(seg)
                acc += take
                idx += 1
                fail_streak = 0
                continue
            if cached is None:
                try:
                    src = _download(url, work / ("src_%03d.mp4" % len(src_cache)))
                    info = _probe(str(src))
                except Exception:  # noqa: BLE001 - 素材被删了就跳过
                    src_cache[url] = None
                    fail_streak += 1
                    if fail_streak >= max_fail:
                        break
                    idx += 1
                    continue
                cached = (src, info)
                src_cache[url] = cached
            src, info = cached
            if not info.get("has_video") or float(info.get("duration") or 0) <= 0.2:
                fail_streak += 1
                if fail_streak >= max_fail:
                    break
                idx += 1
                continue
            if reference is None:
                reference = info
            # 每段固定 3 秒（末段按剩余时长截断）；长视频随机取 3 秒窗口，
            # 这样混剪上传的原片和素材库里的切片都按 3 秒粒度参与。
            duration = float(info["duration"])
            take = min(take, duration)
            if take < 0.3:
                fail_streak += 1
                if fail_streak >= max_fail:
                    break
                idx += 1
                continue
            start = 0.0
            if duration > take + 0.1:
                start = random.uniform(0.0, duration - take - 0.05)
            try:
                seg = _prepare_segment(src, work / ("seg_%03d.mp4" % idx), take, size, reference, start=start)
            except Exception:  # noqa: BLE001 - 单个素材失败不应影响整条任务
                fail_streak += 1
                if fail_streak >= max_fail:
                    break
                idx += 1
                continue
            segments.append(seg)
            # 按实际产出时长累计，切片略短于 3 秒时靠后续素材补齐
            acc += float(_probe(str(seg)).get("duration") or take)
            idx += 1
            fail_streak = 0
        if not segments:
            raise HTTPException(status_code=400, detail="素材里没有可用视频或图片")

        listing = work / "list.txt"
        listing.write_text("".join("file '%s'\n" % p for p in segments), encoding="utf-8")
        board = work / "board.mp4"
        _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(board)], timeout=300)

        final = work / "final.mp4"
        _mix_voice(board, voice_path, final, total, bgm_path, body.bgm_volume)

        # ---- 套闪剪模板：底片交给闪剪渲染，再把音频换回我们的口播 ----
        template_style_id = str(body.template_style_id or "").strip()
        if template_style_id:
            board_bytes = final.read_bytes()
            board_id = _gen_asset_id()
            board_key = "assets/%s.mp4" % board_id
            board_url = _upload_to_tos(board_bytes, board_key, "video/mp4")
            if not board_url:
                raise HTTPException(status_code=503, detail="底片上传失败")
            templated = _apply_shanjian_template(
                db=db, user=user, style_id=template_style_id,
                board_url=board_url, title=body.title or "混剪成片",
            )
            if templated:
                src = _download(templated, work / "templated.mp4")
                remixed = work / "templated_voice.mp4"
                _mix_voice(src, voice_path, remixed, total, bgm_path, body.bgm_volume)
                final = remixed

        data = final.read_bytes()
        asset_id = _gen_asset_id()
        object_key = "assets/%s.mp4" % asset_id
        public_url = _upload_to_tos(data, object_key, "video/mp4")
        if not public_url:
            raise HTTPException(status_code=503, detail="成片上传失败")

        title = (body.title or "混剪成片")[:120]
        db.add(Asset(
            asset_id=asset_id,
            user_id=int(user.id),
            filename=object_key,
            media_type="video",
            file_size=len(data),
            source_url=public_url,
            prompt=title,
            model="montage_ffmpeg",
            tags="montage,ffmpeg",
            meta={"source": "montage_ffmpeg", "asset_origin": "generated", "content_visibility": "record_only",
                  "duration": round(total, 2), "segments": len(segments), "ratio": body.ratio},
        ))
        db.add(UserContentRecord(
            user_id=int(user.id), source="montage", source_id=asset_id, kind="video",
            title=title, file_url=public_url, status="completed",
            meta={"action": "montage_render", "source": "ffmpeg", "asset_id": asset_id,
                  "segments": len(segments), "duration": round(total, 2), "record_id": body.record_id},
            source_created_at=datetime.utcnow(), created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))
        db.commit()
        return {
            "ok": True,
            "video_url": public_url,
            "asset_id": asset_id,
            "duration": round(total, 2),
            "segments": len(segments),
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    finally:
        try:
            db.close()
        except Exception:
            pass
        try:
            for p in work.glob("*"):
                p.unlink(missing_ok=True)
            work.rmdir()
        except Exception:
            pass



def _apply_shanjian_template(*, db: Session, user: User, style_id: str, board_url: str, title: str) -> str:
    """把底片交给闪剪套模板，返回模板成片地址（失败返回空串，走无模板兜底）。"""
    import time as _time

    from .auth import access_token_claims, create_access_token

    try:
        token = create_access_token(access_token_claims(user), )
    except Exception:
        return ""
    base = (os.environ.get("LOBSTER_INTERNAL_API_BASE") or "").strip().rstrip("/") or "http://127.0.0.1:8000"
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=60.0, trust_env=False) as client:
            resp = client.post(base + "/api/shanjian-smart-clip/submit", headers=headers, json={
                "style_id": style_id,
                "scene": "realMan",
                "title": title[:40],
                "video_url": board_url,
                "materials": [],
                "subtitle_switch": True,
                "header_switch": True,
                "keyword_switch": True,
                "watermark_show": False,
            })
            if resp.status_code >= 400:
                return ""
            task_id = str((resp.json() or {}).get("task_id") or "")
            if not task_id:
                return ""
            for _ in range(60):
                _time.sleep(3)
                poll = client.post(base + "/api/shanjian-smart-clip/task", headers=headers, json={"task_id": task_id})
                if poll.status_code >= 400:
                    return ""
                data = poll.json() or {}
                status = str(data.get("status") or "").lower()
                url = str(data.get("video_url") or data.get("videoUrl") or "")
                if status in ("succeed", "success", "completed") and url:
                    return url
                if status in ("failed", "error"):
                    return ""
    except Exception:
        return ""
    return ""


_MONTAGE_MAX_CONCURRENCY = int(os.environ.get("MONTAGE_MAX_CONCURRENCY", "1"))
_MONTAGE_MAX_QUEUE = int(os.environ.get("MONTAGE_MAX_QUEUE", "6"))
_montage_sem = asyncio.Semaphore(_MONTAGE_MAX_CONCURRENCY)
_montage_waiting = 0


def _execute_job(job_id: int) -> None:
    db = SessionLocal()
    try:
        row = db.query(MontageJob).filter(MontageJob.id == int(job_id)).first()
        if row is None:
            return
        owner_id = int(row.user_id)
        params = dict(row.params or {})
        row.status = "running"
        row.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()
    try:
        body = MontageBody(**params)
        result = _render_montage(owner_id, body)
        db = SessionLocal()
        try:
            row = db.query(MontageJob).filter(MontageJob.id == int(job_id)).first()
            if row is not None:
                row.status = "succeed"
                row.video_url = result.get("video_url")
                row.asset_id = result.get("asset_id")
                row.duration = result.get("duration")
                row.updated_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()
    except HTTPException as exc:
        _fail_job(job_id, str(exc.detail or "生成失败"))
    except Exception as exc:  # noqa: BLE001
        _fail_job(job_id, str(exc)[:300])


def _fail_job(job_id: int, message: str) -> None:
    db = SessionLocal()
    try:
        row = db.query(MontageJob).filter(MontageJob.id == int(job_id)).first()
        if row is not None:
            row.status = "failed"
            row.error = message[:500]
            row.updated_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


async def _run_montage_job(job_id: int) -> None:
    global _montage_waiting
    _montage_waiting += 1
    try:
        async with _montage_sem:
            await asyncio.to_thread(_execute_job, job_id)
    finally:
        _montage_waiting -= 1


@router.post("/api/montage/jobs", summary="创建混剪任务（服务端持久化，前端只查）")
async def create_montage(
    body: MontageBody,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user = online_user_for_mobile_user(db, current_user)
    if _montage_waiting >= _MONTAGE_MAX_QUEUE:
        raise HTTPException(status_code=429, detail="当前混剪任务较多，请稍后重试")
    job = MontageJob(user_id=int(user.id), status="pending", params=body.model_dump())
    db.add(job)
    db.commit()
    db.refresh(job)
    background.add_task(_run_montage_job, int(job.id))
    return {"ok": True, "job_id": int(job.id), "status": "pending"}


@router.get("/api/montage/jobs/latest", summary="最近一条混剪任务（刷新后恢复）")
def latest_montage_job(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user = online_user_for_mobile_user(db, current_user)
    row = db.query(MontageJob).filter(MontageJob.user_id == int(user.id)).order_by(MontageJob.id.desc()).first()
    if row is None:
        return {"ok": True, "job": None}
    return {"ok": True, "job": {
        "job_id": row.id, "status": row.status, "video_url": row.video_url or "",
        "duration": float(row.duration or 0), "error": row.error or "",
    }}


class TTSBody(BaseModel):
    text: str = Field("", max_length=2000)
    voice_id: str = Field("", max_length=120)


@router.get("/api/montage/voices", summary="可用音色（含克隆音色）")
def montage_voices(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user = online_user_for_mobile_user(db, current_user)
    from .cutcli_templates import _load_sutui_token_for_stt  # noqa: PLC0415

    from ..models import UserHiflyVoiceAsset  # noqa: PLC0415
    from .hifly_assets import _minimax_voice_id_from_row  # noqa: PLC0415

    mine: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        rows = (
            db.query(UserHiflyVoiceAsset)
            .filter(UserHiflyVoiceAsset.user_id == int(user.id))
            .order_by(UserHiflyVoiceAsset.updated_at.desc())
            .limit(50)
            .all()
        )
    except Exception:
        rows = []
    for row in rows:
        try:
            vid = _minimax_voice_id_from_row(row)
        except Exception:
            vid = ""
        if not vid or vid in seen:
            continue
        seen.add(vid)
        name = str(getattr(row, "title", "") or getattr(row, "name", "") or "我的音色")[:40]
        mine.append({"id": vid, "name": name, "source": "mine"})

    official: list[dict[str, Any]] = []
    default_id = ""
    try:
        from .cutcli_templates import _load_sutui_token_for_stt  # noqa: PLC0415
        token, _src = _load_sutui_token_for_stt(db, int(user.id))
        if token:
            for item in device_tts.list_voices(token):
                if item.get("id") in seen:
                    continue
                official.append({"id": item.get("id"), "name": item.get("name"), "source": "official"})
            default_id = device_tts.pick_voice_id(token)
    except Exception:
        official = []
    items = mine + official[:40]
    if not default_id and mine:
        default_id = mine[0]["id"]
    return {"ok": True, "items": items, "default_id": default_id, "mine_count": len(mine)}


@router.post("/api/montage/tts", summary="口播文案转语音（试听/生成音轨）")
def montage_tts(
    body: TTSBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user = online_user_for_mobile_user(db, current_user)
    text = str(body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="口播文案为空")
    from .cutcli_templates import _load_sutui_token_for_stt  # noqa: PLC0415

    token, _source = _load_sutui_token_for_stt(db, int(user.id))
    if not token:
        raise HTTPException(status_code=503, detail="TTS 未配置，请联系管理员")
    audio_url = device_tts.synthesize(text, token=token, voice_id=str(body.voice_id or "").strip())
    return {"ok": True, "audio_url": audio_url, "chars": len(text)}


@router.get("/api/montage/jobs/{job_id}", summary="查询混剪任务")
def get_montage_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user = online_user_for_mobile_user(db, current_user)
    row = db.query(MontageJob).filter(MontageJob.id == int(job_id), MontageJob.user_id == int(user.id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "ok": True, "job_id": row.id, "status": row.status,
        "video_url": row.video_url or "", "asset_id": row.asset_id or "",
        "duration": float(row.duration or 0), "error": row.error or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }

@router.get("/api/tasks/active", summary="当前用户进行中的任务（聚合，减少轮询次数）")
def active_tasks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user = online_user_for_mobile_user(db, current_user)
    uid = int(user.id)
    montage = (
        db.query(MontageJob)
        .filter(MontageJob.user_id == uid, MontageJob.status.in_(["pending", "running"]))
        .order_by(MontageJob.id.desc()).limit(5).all()
    )
    digital = []
    try:
        from ..models import ShanjianDigitalHumanVideoTask  # noqa: PLC0415
        rows = (
            db.query(ShanjianDigitalHumanVideoTask)
            .filter(ShanjianDigitalHumanVideoTask.user_id == uid,
                    ShanjianDigitalHumanVideoTask.status.in_(["pending", "processing", "running"]))
            .order_by(ShanjianDigitalHumanVideoTask.id.desc()).limit(5).all()
        )
        digital = [{"task_id": r.task_id, "status": r.status, "title": r.title} for r in rows]
    except Exception:
        digital = []
    return {
        "ok": True,
        "montage": [{"job_id": r.id, "status": r.status, "video_url": r.video_url or ""} for r in montage],
        "digital": digital,
        "poll_hint_seconds": 10,
    }

@router.get("/api/montage/jobs", summary="混剪任务列表（任务列表页用）")
def list_montage_jobs(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user = online_user_for_mobile_user(db, current_user)
    rows = (
        db.query(MontageJob)
        .filter(MontageJob.user_id == int(user.id))
        .order_by(MontageJob.id.desc())
        .limit(max(1, min(50, int(limit or 20))))
        .all()
    )
    return {"ok": True, "items": [{
        "job_id": r.id,
        "status": r.status,
        "title": str((r.params or {}).get("title") or "混剪成片"),
        "video_url": r.video_url or "",
        "duration": float(r.duration or 0),
        "error": r.error or "",
        "created_at": r.created_at.isoformat() if r.created_at else "",
    } for r in rows]}
