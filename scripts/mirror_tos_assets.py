#!/usr/bin/env python3
"""Mirror Guangzhou TOS objects into the overseas bucket (Jakarta).

Usage:
    python scripts/mirror_tos_assets.py --apply            # mirror curated list
    python scripts/mirror_tos_assets.py --apply --list     # mirror whole bucket (needs ListBucket)
    python scripts/mirror_tos_assets.py --paths a.zip,b.zip --apply

Source objects are fetched over their public URL (bucket is public-read), so the
script works even when the access key lacks ListBucket/GetObject on the source.
Dest is written with TOS SDK using TOS_CONFIG_OVERSEAS.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app.services.tos_storage import get_tos_config  # noqa: E402

SRC_BASE = "https://lobster-online-assets-2103871705.tos-cn-guangzhou.volces.com"
CURATED = [
    "assets/client-code/deps/cutout/selfie_segmenter.tflite",
    "assets/client-code/deps/fonts/NotoSansSC-400.woff2",
    "assets/client-code/deps/fonts/NotoSansSC-700.woff2",
    "assets/client-code/deps/whisper/ggml-medium-q5_0.bin",
    "assets/cutcli_templates/fonts/AlibabaPuHuiTi-3-105-Heavy.otf",
    "assets/cutcli_templates/fonts/AlibabaPuHuiTi-3-95-ExtraBold.otf",
    "assets/cutcli_templates/product_case/product_case_x72_v1.jpg",
    "assets/cutcli_templates/product_case/product_case_x72_v1.mp4",
    "assets/cutcli_templates/tools/auto-editor-windows-x86_64.exe",
    "assets/ffmpeg/lobster-ffmpeg-win64.zip",
    "assets/cutcli_templates/fonts/AlibabaPuHuiTi-3-75-SemiBold.otf",
    "assets/cutcli_templates/fonts/AlibabaPuHuiTi-3-115-Black.otf",
    "assets/cutcli_templates/fonts/AlibabaPuHuiTi-3-65-Medium.otf",
    "assets/c8877058895c.m4a",
    "assets/f69fd79514c8.m4a",
    "assets/6a4eb368e076.m4a",
    "assets/5268b93f9941.m4a",
    "assets/23929b8355c4.m4a",
    "assets/762080df1a7a.m4a",
    "assets/3d0d6b58112a.m4a",
    "assets/fe15595e0ce2.m4a",
    "assets/57e9eb26c2dd.m4a",
    "assets/909a0e86c332.m4a",
    "assets/a790fbef2b03.m4a",
    "assets/0b0b968f7534.m4a",
    "assets/2022144c0ba6.m4a",
    "assets/bada613dd8da.m4a",
    "assets/dd32d307c3f8.m4a",
    "assets/f374c3c1769d.m4a",
    "assets/9e1960237a66.m4a",
    "assets/1581cdaec400.m4a",
    "assets/d4013e6c4919.m4a",
]


def _client():
    cfg = get_tos_config("overseas")
    if not cfg:
        raise SystemExit("TOS_CONFIG_OVERSEAS not configured")
    import tos
    return tos.TosClientV2(cfg["access_key"], cfg["secret_key"], cfg["endpoint"], cfg["region"]), cfg


def _src_head(key: str) -> tuple[int, str]:
    req = urllib.request.Request(f"{SRC_BASE}/{key}", method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return int(resp.headers.get("Content-Length") or 0), str(resp.headers.get("ETag") or "").strip('"')


def _download(key: str, dest: pathlib.Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    next_report = 50 * 1024 * 1024
    with urllib.request.urlopen(f"{SRC_BASE}/{key}", timeout=1800) as resp, dest.open("wb") as fh:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            written += len(chunk)
            if written >= next_report:
                print(f"  .. {key}: {written/1024/1024:.0f} MB", flush=True)
                next_report += 50 * 1024 * 1024


def _copy_one(client, bucket: str, key: str, staging: pathlib.Path, apply: bool) -> str:
    try:
        size, etag = _src_head(key)
    except Exception as exc:  # noqa: BLE001
        return f"MISSING {key} ({type(exc).__name__})"
    try:
        head = client.head_object(bucket, key)
        if int(head.content_length or 0) == size:
            return f"SKIP {key} ({size} bytes already present)"
    except Exception:
        pass
    if not apply:
        return f"WOULD COPY {key} ({size/1024/1024:.1f} MB)"
    print(f"START {key} ({size/1024/1024:.1f} MB)", flush=True)
    tmp = staging / hashlib.sha1(key.encode()).hexdigest()
    _download(key, tmp)
    with tmp.open("rb") as fh:
        client.put_object(bucket, key, content=fh, content_length=tmp.stat().st_size)
    got = int(client.head_object(bucket, key).content_length or 0)
    tmp.unlink(missing_ok=True)
    if got != size:
        return f"SIZE MISMATCH {key} src={size} dst={got}"
    return f"COPIED {key} ({size/1024/1024:.1f} MB)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually copy (default: dry run)")
    ap.add_argument("--list", action="store_true", help="enumerate the whole source bucket")
    ap.add_argument("--paths", default="", help="comma separated object keys")
    ap.add_argument("--prefix", default="assets/")
    ap.add_argument("--max-mb", type=float, default=0, help="skip objects larger than this")
    ap.add_argument("--skip-mb", type=float, default=0, help="skip objects smaller than this")
    args = ap.parse_args()

    client, cfg = _client()
    bucket = cfg["bucket_name"]
    keys = [p for p in (args.paths.split(",") if args.paths else []) if p.strip()] or list(CURATED)
    if args.max_mb or args.skip_mb:
        kept = []
        for key in keys:
            try:
                size = _src_head(key)[0]
            except Exception:
                continue
            mb = size / 1024 / 1024
            if args.max_mb and mb > args.max_mb:
                continue
            if args.skip_mb and mb < args.skip_mb:
                continue
            kept.append(key)
        keys = kept
    if args.list:
        import tos
        srccfg = json.loads((ROOT / "custom_configs.json").read_text(encoding="utf-8"))["configs"]["TOS_CONFIG"]
        src = tos.TosClientV2(srccfg["access_key"], srccfg["secret_key"], srccfg["endpoint"], srccfg["region"])
        token = ""
        keys = []
        while True:
            out = src.list_objects_type2(srccfg["bucket_name"], prefix=args.prefix, max_keys=1000, continuation_token=token)
            keys.extend(o.key for o in out.contents)
            if not out.is_truncated:
                break
            token = out.next_continuation_token
    staging = ROOT / "data" / "_tos_mirror_tmp"
    staging.mkdir(parents=True, exist_ok=True)
    total_mb = 0.0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(lambda k: _copy_one(client, bucket, k, staging, args.apply), keys):
            print(result, flush=True)
            if result.startswith(("COPIED", "WOULD")):
                try:
                    total_mb += float(result.split("(")[-1].split(" ")[0])
                except Exception:
                    pass
    print(f"done: {len(keys)} objects, {total_mb:.1f} MB, apply={args.apply}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
