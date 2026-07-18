#!/usr/bin/env python3
"""Publish a client-code OTA zip and update the live manifest.

Default behavior:
1. Upload the zip to the mainland server as a fallback copy under
   ``client_static/client_code/bundles/``.
2. If ``--bundle-url`` is not provided, upload the same zip to remote TOS using
   ``custom_configs.json -> configs.TOS_CONFIG``.
3. Write ``manifest.json`` so ``bundle_url`` points to the explicit URL or the
   TOS public URL by default.

Current TOS IAM policy only allows writes under ``assets/*``, so OTA bundles are
published to ``assets/client-code/bundles/<zip>`` by default.

The local server-hosted bundle path remains available as a fallback copy, but it
is no longer the default download target for clients.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
import urllib.parse
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
    "backend/douyin_origin/douyin_protocol/node_modules",
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
        "scripts/wechat_runtime_wheels",
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


def is_encrypted_ota_zip(zip_path: Path) -> bool:
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            normalized = name.replace("\\", "/")
            if normalized.endswith(".pyc"):
                return True
    return False


def canonical_bundle_object_name(bundle_name: str) -> str:
    name = (bundle_name or "").strip().lower()
    variants: list[str] = []
    for token in ("with_nodejs", "with_ppt_runtime", "with_douyin_runtime", "with_wechat_runtime", "encrypted"):
        if token in name:
            variants.append(token)
    suffix = "_" + "_".join(variants) if variants else ""
    return f"lobster_online_client_code_ota{suffix}_latest.zip"


def append_cache_bust_query(url: str, *, version: str, build: int, sha256: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    pairs = [(k, v) for (k, v) in pairs if k not in {"v", "build", "sha"}]
    pairs.extend([
        ("v", str(version or "").strip() or "0"),
        ("build", str(int(build or 0))),
        ("sha", str(sha256 or "").strip()[:12]),
    ])
    query = urllib.parse.urlencode(pairs)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def load_deploy() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    data: dict[str, str] = {}
    for line in (root / ".env.deploy").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def norm_key(path: str) -> str:
    path = path.strip()
    if path.startswith("/d/") or path.startswith("/D/"):
        path = "D:" + path[2:].replace("/", os.sep)
    else:
        path = path.replace("/", os.sep)
    return path


def upload_bundle_to_remote_tos(
    ssh_client,
    *,
    remote_root: str,
    remote_zip: str,
    bundle_name: str,
) -> str:
    object_name = canonical_bundle_object_name(bundle_name)
    object_key = f"assets/client-code/bundles/{object_name}"
    remote_repo = remote_root.rstrip("/")
    remote_py = f"""from pathlib import Path
import json

cfg_path = Path({json.dumps(remote_repo + "/custom_configs.json")})
cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
tc = (cfg_data.get("configs") or {{}}).get("TOS_CONFIG") or {{}}
ak = str(tc.get("access_key") or "").strip()
sk = str(tc.get("secret_key") or "").strip()
endpoint = str(tc.get("endpoint") or "").strip()
region = str(tc.get("region") or "").strip()
bucket = str(tc.get("bucket_name") or "").strip()
public_domain = str(tc.get("public_domain") or "").strip().rstrip("/")
missing = [name for name, value in (
    ("access_key", ak),
    ("secret_key", sk),
    ("endpoint", endpoint),
    ("region", region),
    ("bucket_name", bucket),
    ("public_domain", public_domain),
) if not value]
if missing:
    raise SystemExit("TOS_CONFIG missing fields: " + ",".join(missing))

import tos

src = Path({json.dumps(remote_zip)})
if not src.is_file():
    raise SystemExit("remote ota zip missing: " + str(src))

client = tos.TosClientV2(ak, sk, endpoint, region)
size = src.stat().st_size
with src.open("rb") as fh:
    client.put_object(
        bucket,
        {json.dumps(object_key)},
        content=fh,
        content_length=size,
        content_type="application/zip",
    )
print(public_domain + "/" + {json.dumps(object_key)})
"""
    remote_cmd = (
        f"cd {shlex.quote(remote_repo)} && "
        f".venv/bin/python3 - <<'PY'\n{remote_py}\nPY"
    )
    _, stdout, stderr = ssh_client.exec_command(remote_cmd, timeout=900)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    if err:
        raise RuntimeError(err)
    if not out:
        raise RuntimeError("remote TOS upload returned empty URL")
    return out.splitlines()[-1].strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip_path", type=Path, help="local OTA zip")
    ap.add_argument("--version", default="1.0.5", help="manifest.version")
    ap.add_argument("--build", type=int, default=5, help="manifest.build")
    ap.add_argument(
        "--public-base",
        default="https://bhzn.top",
        help="fallback API base only; used only if TOS upload is skipped or --bundle-url is absent and TOS upload fails",
    )
    ap.add_argument(
        "--bundle-url",
        default="",
        help="explicit HTTPS URL already uploaded to OSS/TOS/CDN; if provided, manifest uses this directly",
    )
    args = ap.parse_args()

    zip_path = args.zip_path.resolve()
    if not zip_path.is_file():
        print(f"[ERR] file not found: {zip_path}", file=sys.stderr)
        return 2

    sha256 = hashlib.sha256()
    with zip_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            sha256.update(chunk)
    digest = sha256.hexdigest()

    env = load_deploy()
    host = env["LOBSTER_DEPLOY_HOST"]
    user, _, hostname = host.partition("@")
    key_path = norm_key(env["LOBSTER_DEPLOY_SSH_KEY"])
    remote_root = env.get("LOBSTER_DEPLOY_REMOTE_DIR", "/root/lobster_server").rstrip("/")
    passphrase = env.get("LOBSTER_SSH_KEY_PASSPHRASE", "").encode()

    bundle_name = zip_path.name
    remote_base = f"{remote_root}/client_static/client_code"
    remote_bundles = f"{remote_base}/bundles"
    remote_zip = f"{remote_bundles}/{bundle_name}"
    remote_manifest = f"{remote_base}/manifest.json"

    import paramiko

    pkey = None
    for key_cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            pkey = key_cls.from_private_key_file(key_path, password=passphrase or None)
            break
        except Exception:
            continue
    if not pkey:
        print("could not load key:", key_path, file=sys.stderr)
        return 1

    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh_client.connect(hostname=hostname, username=user, pkey=pkey, timeout=45)

    try:
        _, stdout, stderr = ssh_client.exec_command(f"mkdir -p {remote_bundles}", timeout=60)
        _ = stdout.read()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        if err:
            print(err, file=sys.stderr)

        sftp = ssh_client.open_sftp()
        try:
            sftp.put(str(zip_path), remote_zip)
        finally:
            sftp.close()

        explicit_bundle_url = (args.bundle_url or "").strip()
        if explicit_bundle_url:
            bundle_url = explicit_bundle_url
        else:
            bundle_url = upload_bundle_to_remote_tos(
                ssh_client,
                remote_root=remote_root,
                remote_zip=remote_zip,
                bundle_name=bundle_name,
            )
        if not bundle_url:
            bundle_url = f"{args.public_base.rstrip('/')}/client/client-code/bundles/{bundle_name}"
        if not explicit_bundle_url:
            bundle_url = append_cache_bust_query(
                bundle_url,
                version=args.version,
                build=args.build,
                sha256=digest,
            )

        manifest = {
            "version": args.version,
            "build": args.build,
            "bundle_url": bundle_url,
            "sha256": digest,
            "paths": manifest_paths_for_zip(zip_path),
            "note": f"OTA {bundle_name}; ordinary client-code paths",
        }
        if is_encrypted_ota_zip(zip_path):
            manifest["encrypted"] = True
            manifest["note"] = f"OTA {bundle_name}; encrypted client-code paths"

        payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        sftp = ssh_client.open_sftp()
        try:
            with sftp.file(remote_manifest, "w") as remote_file:
                remote_file.write(payload.encode("utf-8"))
        finally:
            sftp.close()
    finally:
        ssh_client.close()

    print("[OK] uploaded fallback copy:", remote_zip)
    print("[OK] wrote manifest:", remote_manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
