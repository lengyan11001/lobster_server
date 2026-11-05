#!/usr/bin/env python3
"""Calibrate designer template font sizes against designer sample frames.

Offline tool only: it renders each template's ASS with ffmpeg, compares glyph
heights with the designer sample video, and adjusts per-track font sizes.
No JianYing dependency at runtime.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import List, Tuple

from PIL import Image

ROOT = Path(r"D:\lobster_server")
MANIFEST = ROOT / "data" / "cutcli_templates" / "designer_templates.json"
CLIENT = Path(r"D:\lobster_online")
FFMPEG = CLIENT / "deps" / "ffmpeg" / "ffmpeg.exe"
PYTHON = CLIENT / "python" / "python.exe"
SAMPLES = Path(r"C:\Documents\New project\_scratch\_tplprev")
WORK = Path(r"C:\Documents\New project\_scratch\_calib_work")


def ass_rgb(value: str) -> Tuple[int, int, int]:
    text = str(value or "").strip()
    if text.startswith("&H") and len(text) >= 10:
        return int(text[8:10], 16), int(text[6:8], 16), int(text[4:6], 16)
    return 255, 255, 255


def grab(video: Path, t: float, out: Path) -> bool:
    if out.exists():
        return True
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.1, t):.2f}",
         "-i", str(video), "-frames:v", "1", "-update", "1", str(out)],
        capture_output=True, text=True,
    )
    return out.exists()


def build_ass(tid: str) -> Path:
    ass = WORK / f"{tid}.ass"
    if ass.exists():
        ass.unlink()
    code = (
        "import json,sys;"
        f"sys.path.insert(0, r'{CLIENT}');"
        "from backend.app.api.cutcli_templates_local import _write_ass;"
        "from pathlib import Path;"
        f"d=json.load(open(r'{MANIFEST}',encoding='utf-8'));"
        f"t=[x for x in d if x['id']=='{tid}'][0];"
        f"_write_ass(Path(r'{ass}'), [], t['caption_style'], 1280, 2276, {{}}, 30.0, 'AlibabaPuHuiTi-3-105-Heavy')"
    )
    subprocess.run([str(PYTHON), "-c", code], capture_output=True, text=True)
    return ass


def render_ours(ass: Path, t: float, out: Path) -> bool:
    if out.exists():
        return True
    subprocess.run(
        [str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=0x6b7280:s=1280x2276:d=30", "-vf", f"ass={ass.name}:fontsdir=fonts",
         "-ss", f"{max(0.1, t):.2f}", "-frames:v", "1", "-update", "1", str(out)],
        cwd=str(WORK), capture_output=True, text=True,
    )
    return out.exists()


def measure(img: Image.Image, xr: float, yr: float, color: Tuple[int, int, int],
            wf: float = 0.40, hf: float = 0.15) -> int:
    W, H = img.size
    px = img.load()
    cx, cy = int(xr * W), int(yr * H)
    bw, bh = int(W * wf), int(H * hf)
    x0, x1 = max(0, cx - bw // 2), min(W, cx + bw // 2)
    y0, y1 = max(0, cy - bh // 2), min(H, cy + bh // 2)
    cr, cg, cb = color
    rows: List[int] = []
    for y in range(y0, y1):
        cnt = 0
        for x in range(x0, x1):
            r, g, b = px[x, y]
            if abs(r - cr) <= 45 and abs(g - cg) <= 45 and abs(b - cb) <= 45 and (r + g + b) > 260:
                cnt += 1
        if cnt > 3:
            rows.append(y)
    if not rows:
        return 0
    best = cur = 1
    for i in range(1, len(rows)):
        cur = cur + 1 if rows[i] - rows[i - 1] <= 2 else 1
        best = max(best, cur)
    return int(round(best * 2276.0 / H))


def median(values: List[int]) -> int:
    values = [v for v in values if v > 0]
    if not values:
        return 0
    values.sort()
    return values[len(values) // 2]


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "fonts").mkdir(exist_ok=True)
    for otf in (CLIENT / "static" / "fonts").glob("*.otf"):
        target = WORK / "fonts" / otf.name
        if not target.exists():
            target.write_bytes(otf.read_bytes())

    items = json.loads(MANIFEST.read_text(encoding="utf-8"))
    changed = 0
    for tpl in items:
        tid = tpl["id"]
        video = SAMPLES / f"{tid}.mp4"
        tracks = ((tpl.get("caption_style") or {}).get("overlay_style") or {}).get("text_tracks") or []
        if not tracks or not video.exists():
            continue
        ass = build_ass(tid)
        for idx, tr in enumerate(tracks):
            color = ass_rgb(tr.get("color"))
            start = float(tr.get("start_ms") or 0) / 1000.0
            design_h: List[int] = []
            ours_h: List[int] = []
            for step in (0.3, 0.7, 1.1):
                t = start + step
                dpath = WORK / f"d_{tid}_{idx}_{step}.png"
                opath = WORK / f"o_{tid}_{idx}_{step}.png"
                if grab(video, t, dpath):
                    design_h.append(measure(Image.open(dpath).convert("RGB"), float(tr.get("x") or 0.5), float(tr.get("y") or 0.5), color))
                if render_ours(ass, t, opath):
                    ours_h.append(measure(Image.open(opath).convert("RGB"), float(tr.get("x") or 0.5), float(tr.get("y") or 0.5), color))
            dh, oh = median(design_h), median(ours_h)
            if dh < 25 or oh < 12:
                continue
            ratio = dh / oh
            if not (0.6 <= ratio <= 2.6):
                continue
            tr["font_size"] = round(float(tr.get("font_size") or 0) * ratio, 2)
            changed += 1
        print(tid, "tracks", len(tracks))
    MANIFEST.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("changed", changed)


if __name__ == "__main__":
    main()
