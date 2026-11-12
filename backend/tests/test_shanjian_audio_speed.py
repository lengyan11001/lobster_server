"""数字人2.0 音频驱动：speed_ratio 必须真的作用到驱动音频。

数字人2.0 /v1/virtualman/video 的 audioUrl 分支没有 speedRatio 参数，前端传的
speed_ratio 以前在音频分支被静默丢掉，用户调了语速成片一模一样。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from backend.app.api import shanjian_digital_human as dh


ROOT = Path(__file__).resolve().parents[2]


def _ffmpeg() -> str:
    try:
        found = dh._find_asset_ffmpeg()
        if found:
            return found
    except Exception:
        pass
    exe = "ffmpeg.exe" if __import__("os").name == "nt" else "ffmpeg"
    for candidate in (ROOT / "deps" / "ffmpeg" / exe, ROOT.parent / "lobster_online" / "deps" / "ffmpeg" / exe):
        if candidate.exists():
            return str(candidate)
    return shutil.which("ffmpeg") or ""


def _ffprobe() -> str:
    ffmpeg = _ffmpeg()
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe.exe" if Path(ffmpeg).suffix.lower() == ".exe" else "ffprobe")
        if sibling.exists():
            return str(sibling)
    return shutil.which("ffprobe") or ""


FFMPEG = _ffmpeg()
FFPROBE = _ffprobe()

pytestmark = pytest.mark.skipif(
    not FFMPEG or not FFPROBE,
    reason="需要本机 ffmpeg/ffprobe 才能验证音频变速",
)


def _make_tone(tmp: Path, seconds: float = 2.0) -> bytes:
    src = tmp / "tone.mp3"
    subprocess.run(
        [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-ar", "32000", "-ac", "1", str(src),
        ],
        check=True,
    )
    return src.read_bytes()


def _duration(data: bytes, tmp: Path) -> float:
    probe = tmp / "probe.mp3"
    probe.write_bytes(data)
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(probe)],
        capture_output=True,
        check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def test_speed_ratio_applies_to_audio(monkeypatch):
    monkeypatch.setattr(dh, "_find_asset_ffmpeg", lambda: FFMPEG)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        raw = _make_tone(tmp)
        base = _duration(raw, tmp)
        assert abs(_duration(dh._apply_audio_speed(raw, "audio/mpeg", 1.5)[0], tmp) - base / 1.5) < 0.12
        assert abs(_duration(dh._apply_audio_speed(raw, "audio/mpeg", 0.5)[0], tmp) - base / 0.5) < 0.12
        assert dh._apply_audio_speed(raw, "audio/mpeg", 1.0)[0] == raw


def test_persist_audio_applies_speed_ratio(monkeypatch):
    monkeypatch.setattr(dh, "_find_asset_ffmpeg", lambda: FFMPEG)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        raw = _make_tone(tmp)
        base = _duration(raw, tmp)
        captured = {}

        async def fake_download(_url: str):
            return raw, "audio/mpeg"

        def fake_save(data: bytes, ext: str, media_type: str):
            captured["data"] = data
            return "asset-1", "asset-1.mp3", len(data), "https://example.com/a.mp3"

        monkeypatch.setattr(dh, "_download_audio_bytes", fake_download)
        monkeypatch.setattr(dh, "_save_bytes_or_tos", fake_save)

        class _DB:
            def add(self, _obj):
                pass

            def commit(self):
                pass

            def in_transaction(self):
                return False

        class _User:
            id = 1

        public_url, asset_id = asyncio.run(
            dh._persist_audio_for_shanjian(
                audio_url="https://example.com/src.mp3",
                db=_DB(),
                current_user=_User(),
                title="测试",
                speed_ratio=2.0,
            )
        )
        assert public_url == "https://example.com/a.mp3"
        assert asset_id == "asset-1"
        assert abs(_duration(captured["data"], tmp) - base / 2.0) < 0.12
