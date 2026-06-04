from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

load_dotenv(ROOT_DIR / ".env", override=False)

from backend.app.api import cutcli_templates as templates


def _find_ffmpeg() -> str:
    candidates = [
        r"D:\lobster_online\deps\ffmpeg\ffmpeg.exe",
        r"D:\lobster_online\skills\comfly_veo3_daihuo_video\tools\ffmpeg\windows\ffmpeg.exe",
        r"C:\Users\Administrator\AppData\Local\CapCut\Apps\8.7.0.3685\ffmpeg.exe",
        shutil.which("ffmpeg"),
        shutil.which("ffmpeg.exe"),
    ]
    try:
        candidates.insert(0, templates._find_ffmpeg_bin())
    except Exception:
        pass
    for value in candidates:
        if value and Path(value).exists():
            return str(Path(value))
    raise RuntimeError("ffmpeg not found")


def _template_captions(template: Dict[str, Any], stt_data: Dict[str, Any], duration_sec: float) -> List[Dict[str, Any]]:
    style = templates._caption_style_for_template(template)
    captions = templates._captions_from_stt(
        stt_data,
        video_duration_sec=duration_sec,
        caption_style=style,
    )
    if not captions:
        raise RuntimeError(f"no STT captions generated for template {template.get('id')}")
    return captions


def _load_stt_data(args: argparse.Namespace, *, ffmpeg: str, source: Path, scratch: Path) -> Dict[str, Any]:
    if args.stt_json:
        path = Path(args.stt_json).resolve()
        return json.loads(path.read_text(encoding="utf-8"))

    cached = scratch / f"{source.stem}.stt.json"
    if cached.exists() and not args.refresh_stt:
        return json.loads(cached.read_text(encoding="utf-8"))

    token, token_source = templates._server_sutui_token_from_env(args.brand)
    if not token:
        raise RuntimeError("server sutui token is not configured in .env")
    print(f"STT token source: {token_source}")

    audio_url = str(args.audio_url or "").strip()
    if not audio_url:
        audio_path = scratch / f"{source.stem}.preview_audio.wav"
        templates._extract_audio_wav(ffmpeg=ffmpeg, source=str(source), out_path=audio_path)
        audio_url = templates._upload_job_file_to_tos(
            audio_path,
            object_key=f"assets/cutcli_template_previews/{source.stem}/audio.wav",
            content_type="audio/wav",
        )
    print(f"STT model: {templates._STT_MODEL}")
    created = templates._stt_create_task(token, audio_url, job_dir=scratch)
    stt_data = templates._stt_poll_task(token, created["task_id"], job_dir=scratch)
    cached.write_text(json.dumps(stt_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return stt_data


def _write_preview_catalog(out_dir: Path, duration_sec: float, version: str, source_name: str) -> None:
    catalog: Dict[str, Any] = {}
    for template_id, template in templates._TEMPLATES.items():
        style = templates._caption_style_for_template(template)
        catalog[template_id] = {
            "preview_url": f"{templates._STATIC_PREVIEW_PUBLIC_PREFIX}/{template_id}.mp4?v={version}",
            "source": source_name,
            "caption_source": "stt",
            "stt_model": templates._STT_MODEL,
            "duration_sec": duration_sec,
            "style": style.get("id") or "",
        }
    templates._PREVIEW_CATALOG_FILE.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate server CutCLI template preview videos.")
    parser.add_argument("--source", default=str(templates._ROOT_DIR / "tmp" / "cutcli_test_f6d337.mp4"))
    parser.add_argument("--duration", type=float, default=6.8)
    parser.add_argument("--out-dir", default=str(templates._STATIC_PREVIEW_DIR))
    parser.add_argument("--audio-url", default="")
    parser.add_argument("--stt-json", default="")
    parser.add_argument("--refresh-stt", action="store_true")
    parser.add_argument("--brand", default="bihuo")
    parser.add_argument("--version", default="")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = templates._ROOT_DIR / "tmp" / "cutcli_template_previews"
    scratch.mkdir(parents=True, exist_ok=True)
    ffmpeg = _find_ffmpeg()
    duration_sec = max(0.6, float(args.duration))
    stt_data = _load_stt_data(args, ffmpeg=ffmpeg, source=source, scratch=scratch)
    version = args.version.strip() or time.strftime("%Y%m%d%H%M%S")

    for template_id, template in templates._TEMPLATES.items():
        style = templates._caption_style_for_template(template)
        captions = _template_captions(template, stt_data, duration_sec)
        ass_path = templates._write_pop_caption_ass(scratch, captions, caption_style=style)
        named_ass = scratch / f"{template_id}.ass"
        named_ass.write_text(ass_path.read_text(encoding="utf-8"), encoding="utf-8")
        output = out_dir / f"{template_id}.mp4"
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            "0",
            "-t",
            f"{duration_sec:.2f}",
            "-i",
            str(source),
            "-vf",
            f"scale=1080:1920:force_original_aspect_ratio=decrease,"
            f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,"
            f"ass='{templates._ffmpeg_filter_path(named_ass)}'",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output),
        ]
        templates._run_cmd(cmd, timeout=900)
        print(f"{template_id}: {output} ({output.stat().st_size} bytes)")
    _write_preview_catalog(out_dir, duration_sec, version, source.name)
    print(f"catalog: {templates._PREVIEW_CATALOG_FILE} (v={version})")


if __name__ == "__main__":
    main()
