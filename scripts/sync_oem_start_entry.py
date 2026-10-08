#!/usr/bin/env python3
"""Keep a renamed copy of every OEM launcher EXE in its own OEM folder.

The factory wires boot autostart at one fixed file name, so each brand folder
under ``client_static/oem/<mark>/`` carries ``start.exe`` as a byte-identical
copy of ``client_launcher.exe``. ``/api/oem/bootstrap`` advertises that copy as
the ``start_entry`` asset only while the bytes still match, which keeps the
brand icon identical between the desktop EXE and the autostart entry.

Usage:
    python scripts/sync_oem_start_entry.py           # copy / refresh
    python scripts/sync_oem_start_entry.py --check   # verify only, exit 1 if stale
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT_STATIC = ROOT / "client_static"
OEM_ROOT = CLIENT_STATIC / "oem"
MANIFEST_PATH = OEM_ROOT / "manifest.json"
START_ENTRY_FILENAME = "start.exe"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _launcher_source(launcher_asset: dict) -> Path | None:
    url = str(launcher_asset.get("url") or "").strip()
    if not url.startswith("/client/"):
        return None
    candidate = (CLIENT_STATIC / url[len("/client/") :].lstrip("/")).resolve()
    if CLIENT_STATIC.resolve() not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def sync(*, check_only: bool) -> int:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[error] cannot read {MANIFEST_PATH}: {exc}")
        return 1
    brands = manifest.get("brands") if isinstance(manifest, dict) else None
    if not isinstance(brands, dict):
        print("[error] manifest has no brands block")
        return 1

    problems = 0
    for mark in sorted(brands):
        brand = brands[mark]
        assets = brand.get("assets") if isinstance(brand, dict) else None
        if not isinstance(assets, list):
            print(f"[error] {mark}: assets block is missing")
            problems += 1
            continue
        launcher = next(
            (item for item in assets if isinstance(item, dict) and str(item.get("key") or "") == "launcher_exe"),
            None,
        )
        if launcher is None:
            print(f"[skip] {mark}: no launcher_exe asset")
            continue
        source = _launcher_source(launcher)
        if source is None:
            print(f"[error] {mark}: launcher_exe asset url is not a client_static file")
            problems += 1
            continue
        source_sha = _sha256(source)
        if source_sha != str(launcher.get("sha256") or "").strip().lower():
            print(f"[error] {mark}: {source.name} does not match the manifest sha256")
            problems += 1
            continue

        target = OEM_ROOT / mark / START_ENTRY_FILENAME
        if target.is_file() and target.read_bytes() == source.read_bytes():
            print(f"[ok] {mark}: {START_ENTRY_FILENAME} mirrors {source.name} ({source.stat().st_size} bytes)")
            continue
        if check_only:
            state = "missing" if not target.is_file() else "stale"
            print(f"[{state}] {mark}: {START_ENTRY_FILENAME} does not mirror {source.name}")
            problems += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        print(
            f"[write] {mark}/{START_ENTRY_FILENAME} <- {source.name} "
            f"({source.stat().st_size} bytes, sha256 {source_sha[:16]})"
        )
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args()
    return sync(check_only=bool(args.check))


if __name__ == "__main__":
    sys.exit(main())
