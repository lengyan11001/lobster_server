#!/usr/bin/env python3
"""用 .env.deploy SSH 将本地 OTA zip 上传到大陆机 client_static/client_code/bundles/ 并写入 manifest.json。"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path


DEFAULT_CLIENT_CODE_OTA_PATHS = [
    "scripts",
    "backend",
    "desktop",
    "mcp",
    "static",
    "publisher",
    "skills",
    "skill_registry.json",
    "upstream_urls.json",
    ".env",
    "必火智能AI.exe",
    "openclaw",
    "requirements.txt",
    ".env.example",
    "install.bat",
    "start.bat",
    "run_backend.bat",
    "run_mcp.bat",
    "nodejs/package.json",
    "nodejs/package-lock.json",
    "nodejs/ensure-npm-cli.mjs",
    "nodejs/run-npm.mjs",
    "nodejs/.gitignore",
    "nodejs/node_modules/@tencent-weixin/openclaw-weixin",
    # Keep version last so a partially applied OTA is retried on next launch.
    "CLIENT_CODE_VERSION.json",
]


def manifest_paths_for_zip(zip_path: Path) -> list[str]:
    paths = list(DEFAULT_CLIENT_CODE_OTA_PATHS)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    runtime_dirs = [
        "scripts/ppt_runtime_wheels",
        "scripts/douyin_runtime_wheels",
    ]
    found_runtime_dirs = [
        runtime_dir
        for runtime_dir in runtime_dirs
        if any(name.startswith(runtime_dir + "/") for name in names)
    ]
    if found_runtime_dirs:
        version_path = "CLIENT_CODE_VERSION.json"
        paths = [p for p in paths if p != version_path]
        for runtime_dir in found_runtime_dirs:
            if runtime_dir not in paths:
                paths.append(runtime_dir)
        paths.append(version_path)
    return paths


def load_deploy() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    d: dict[str, str] = {}
    for line in (root / ".env.deploy").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip()
    return d


def norm_key(path: str) -> str:
    path = path.strip()
    if path.startswith("/d/") or path.startswith("/D/"):
        path = "D:" + path[2:].replace("/", os.sep)
    else:
        path = path.replace("/", os.sep)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip_path", type=Path, help="本地 OTA zip")
    ap.add_argument("--version", default="1.0.5", help="manifest.version")
    ap.add_argument("--build", type=int, default=5, help="manifest.build（须大于客户端 CLIENT_CODE_VERSION.build 才会强拉）")
    ap.add_argument(
        "--public-base",
        default="https://bhzn.top",
        help="manifest.bundle_url 使用的 API 根（未指定 --bundle-url 时使用）",
    )
    ap.add_argument(
        "--bundle-url",
        default="",
        help="已上传到 OSS/TOS/CDN 的 OTA zip HTTPS 直链；指定后只上传/写 manifest，不再让 bundle_url 指向 bhzn.top 文件。",
    )
    args = ap.parse_args()

    z = args.zip_path.resolve()
    if not z.is_file():
        print(f"[ERR] 文件不存在: {z}", file=sys.stderr)
        return 2

    h = hashlib.sha256()
    with z.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    sha = h.hexdigest()

    env = load_deploy()
    host = env["LOBSTER_DEPLOY_HOST"]
    user, _, hostname = host.partition("@")
    keyp = norm_key(env["LOBSTER_DEPLOY_SSH_KEY"])
    remote_root = env.get("LOBSTER_DEPLOY_REMOTE_DIR", "/root/lobster_server").rstrip("/")
    pp = env.get("LOBSTER_SSH_KEY_PASSPHRASE", "").encode()

    bundle_name = z.name
    bundle_url = (args.bundle_url or "").strip() or f"{args.public_base.rstrip('/')}/client/client-code/bundles/{bundle_name}"
    manifest = {
        "version": args.version,
        "build": args.build,
        "bundle_url": bundle_url,
        "sha256": sha,
        "paths": manifest_paths_for_zip(z),
        "note": f"OTA {bundle_name}; ordinary client-code paths",
    }
    remote_base = f"{remote_root}/client_static/client_code"
    remote_bundles = f"{remote_base}/bundles"
    remote_zip = f"{remote_bundles}/{bundle_name}"
    remote_manifest = f"{remote_base}/manifest.json"

    import paramiko

    pkey = None
    for Key in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            pkey = Key.from_private_key_file(keyp, password=pp or None)
            break
        except Exception:
            continue
    if not pkey:
        print("could not load key:", keyp, file=sys.stderr)
        return 1

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(hostname=hostname, username=user, pkey=pkey, timeout=45)

    for cmd in (
        f"mkdir -p {remote_bundles}",
    ):
        _, stdout, stderr = c.exec_command(cmd, timeout=60)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if err.strip():
            print(err, file=sys.stderr)

    sftp = c.open_sftp()
    try:
        sftp.put(str(z), remote_zip)
    finally:
        sftp.close()

    payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    sftp = c.open_sftp()
    try:
        with sftp.file(remote_manifest, "w") as rf:
            rf.write(payload.encode("utf-8"))
    finally:
        sftp.close()

    c.close()

    print("[OK] 已上传:", remote_zip)
    print("[OK] 已写入:", remote_manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
