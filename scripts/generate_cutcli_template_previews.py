from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

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


def _template_captions(template: Dict[str, Any], duration_us: int) -> List[Dict[str, Any]]:
    style = templates._caption_style_for_template(template)
    raw = template.get("preview_captions") or []
    stt_like = {
        "output": {
            "utterances": [
                {
                    "text": str(item.get("text") or ""),
                    "start_time": int(item.get("start") or 0) // 1000,
                    "end_time": int(item.get("end") or duration_us) // 1000,
                    "words": [
                        {
                            "text": str(item.get("text") or ""),
                            "start_time": int(item.get("start") or 0) // 1000,
                            "end_time": int(item.get("end") or duration_us) // 1000,
                        }
                    ],
                }
                for item in raw
            ]
        }
    }
    captions = templates._captions_from_stt(
        stt_like,
        video_duration_sec=duration_us / 1_000_000,
        caption_style=style,
    )
    return captions


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate server CutCLI template preview videos.")
    parser.add_argument("--source", default=str(templates._ROOT_DIR / "tmp" / "cutcli_test_f6d337.mp4"))
    parser.add_argument("--duration", type=float, default=6.8)
    parser.add_argument("--out-dir", default=str(templates._STATIC_PREVIEW_DIR))
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = templates._ROOT_DIR / "tmp" / "cutcli_template_previews"
    scratch.mkdir(parents=True, exist_ok=True)
    ffmpeg = _find_ffmpeg()
    duration_us = max(600_000, int(args.duration * 1_000_000))

    for template_id, template in templates._TEMPLATES.items():
        style = templates._caption_style_for_template(template)
        captions = _template_captions(template, duration_us)
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
            f"{args.duration:.2f}",
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


if __name__ == "__main__":
    main()
