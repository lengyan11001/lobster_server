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

WEBSITE_CLIENT_CODE_OTA_PATHS = [
    "scripts",
    "backend",
    # Website OTA also carries the small OEM switcher runtime. Keep these as
    # exact paths so a partial desktop update never replaces the whole desktop
    # directory on the client.
    "desktop/launcher.py",
    "desktop/launcher.pyc",
    "desktop/oem_branding.py",
    "desktop/oem_branding.pyc",
    "desktop/oem_configurator.py",
    "desktop/oem_configurator.pyc",
    # Optional BHZN ToDesk remote-support agent. Clients report the System
    # Config remote switch as "未安装" unless this executable is installed, so
    # website OTAs must ship it. It is an exact file path on purpose: any other
    # desktop/ entry flips the manifest into full-code mode and makes the
    # updater reconcile (and prune) the whole desktop directory.
    "desktop/BHZN-ToDesk-Agent.exe",
    "OEM配置启动器.exe",
    "static/css",
    "static/js",
    "static/views",
    "static/douyin-origin",
    "static/vendor",
    "static/data",
    "static/branding/brands.json",
    "static/index.html",
    "static/ai3d-model-preview.html",
    "requirements.txt",
    "install.bat",
    "install_slim.bat",
    "static/client_version.json",
    "CLIENT_CODE_VERSION.json",
]

OEM_SWITCHER_OTA_PATHS = frozenset(
    {
        "desktop/launcher.py",
        "desktop/launcher.pyc",
        "desktop/oem_branding.py",
        "desktop/oem_branding.pyc",
        "desktop/oem_configurator.py",
        "desktop/oem_configurator.pyc",
        "desktop/BHZN-ToDesk-Agent.exe",
        "OEM配置启动器.exe",
    }
)

_BRAND_ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".ico", ".icns", ".webp", ".svg")


def _static_root_brand_assets(names: set[str]) -> list[str]:
    """static 根目录下的品牌/图标资源（OEM 资源包）。

    它们必须出现在 manifest.paths 里：客户端更新器只对「manifest 列出的路径」做对账，
    列出的目录中"包里没有的文件"会被删掉；反过来，没列出来的文件升级时不会被写回。
    2026-09-20 build 341 事故就是 manifest 写了整根 static 而包里没有 daka_* 品牌图，
    客户端升级后左上角 logo / 首页大图 / 页头合作方 logo 全部 404。
    """
    assets = [
        name
        for name in names
        if name.startswith("static/")
        and name.count("/") == 1
        and name.lower().endswith(_BRAND_ASSET_SUFFIXES)
    ]
    return sorted(assets)


def _expand_bare_root_paths(paths: list[str], names: set[str]) -> list[str]:
    """把 "static"/"desktop" 这类整根路径展开成包内真实存在的子路径与根文件。

    manifest 里列一个整根目录 = 让客户端把该目录下"包里没有的文件"全部删除。
    发布常规网站 OTA 时包里只有 static 的部分子目录，一旦写成整根 static，
    客户端本地独有的文件（OEM 品牌图、static/generated 等）会被误删。
    """
    expanded: list[str] = []
    for path in paths:
        normalized = path.replace("\\", "/").rstrip("/")
        if normalized in {"static", "desktop"}:
            prefix = normalized + "/"
            derived: set[str] = set()
            for name in names:
                if not name.startswith(prefix):
                    continue
                rest = name[len(prefix) :]
                if not rest:
                    continue
                if "/" in rest:
                    derived.add(prefix + rest.split("/", 1)[0])
                else:
                    derived.add(name)
            for item in sorted(derived):
                if item not in expanded:
                    expanded.append(item)
            continue
        if path not in expanded:
            expanded.append(path)
    return expanded


def manifest_paths_for_zip(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    skill_roots = sorted(
        {
            "/".join(name.split("/")[:2])
            for name in names
            if name.startswith("skills/") and len(name.split("/")) >= 3
        }
    )
    # skills 根目录下的文件（skills/__init__.py、skills/__init__.pyc…）也要下发：
    # manifest 只列"目录"不会带上它们，而包内确实有此文件（2026-09-20 起常规网站 OTA 带 skills）。
    skill_root_files = sorted(
        {name for name in names if name.startswith("skills/") and name.count("/") == 1}
    )
    # 注意：2026-09-20 起「常规网站 OTA」也带 skills（见 pack_client_code_ota.WEBSITE_OTA_PATHS），
    # 包里因此会出现 skills/__init__.py。它不能再作为"这是完整代码包"的判据，否则常规网站 OTA
    # 会被误判成 full-code 模式、把整根 static 写进 manifest，客户端对账时删掉本地品牌资源图
    # （build 341 事故）。完整代码包仍然能通过 mcp/publisher/openclaw/desktop 非白名单条目识别。
    has_full_code_roots = any(
        any(name.startswith(root + "/") for name in names)
        for root in ("mcp", "publisher", "openclaw")
    ) or any(
        name.startswith("desktop/") and name not in OEM_SWITCHER_OTA_PATHS
        for name in names
    )
    if has_full_code_roots:
        candidate_paths = DEFAULT_CLIENT_CODE_OTA_PATHS
    elif skill_roots:
        # A targeted OTA carries only selected skills. Keep the manifest scoped
        # to those directories so the updater never reconciles the whole skills tree.
        candidate_paths = [
            path for path in WEBSITE_CLIENT_CODE_OTA_PATHS if path != "CLIENT_CODE_VERSION.json"
        ] + skill_roots + skill_root_files + ["CLIENT_CODE_VERSION.json"]
    else:
        candidate_paths = WEBSITE_CLIENT_CODE_OTA_PATHS
    paths = []
    for path in candidate_paths:
        normalized = path.replace("\\", "/").rstrip("/")
        if normalized in names or any(name.startswith(normalized + "/") for name in names):
            paths.append(path)
    runtime_dirs = [
        "scripts/ppt_runtime_wheels",
        "scripts/memory_document_runtime_wheels",
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
    # 安全修正（2026-09-20）：永远不要把「整个 skills 根」写进 manifest。
    # 客户端 update 会按 manifest.paths 对账：列了 skills 根就会把包里没有的 skill
    # 从用户机器上删掉（例如刻意排除的 skills/ppt_master ≈58MB、已退役的 media_edit 等）。
    # 一律展开成包内实际存在的 skill 目录，逐个列出。
    expanded: list[str] = []
    for path in paths:
        if path.replace("\\", "/").rstrip("/") == "skills":
            for root in skill_roots:
                if root not in expanded:
                    expanded.append(root)
            continue
        if path not in expanded:
            expanded.append(path)
    version_paths = {"CLIENT_CODE_VERSION.json", "static/client_version.json"}
    head = [p for p in expanded if p not in version_paths]
    tail = [p for p in expanded if p in version_paths]
    result = _expand_bare_root_paths(head, names)
    for asset in _static_root_brand_assets(names):
        if asset not in result:
            result.append(asset)
    return result + tail


def is_encrypted_ota_zip(zip_path: Path) -> bool:
    with zipfile.ZipFile(zip_path) as zf:
        names = [name.replace("\\", "/") for name in zf.namelist()]
        if not any(name.endswith(".pyc") for name in names):
            return False
        # Encrypted OTA keeps a tiny import loader in each Python path.  A
        # normal source file has no marker and must never reach production.
        for name in names:
            if not name.endswith(".py"):
                continue
            try:
                content = zf.read(name).decode("utf-8", errors="replace")
            except Exception:
                return False
            if "Auto-generated by scripts/pack_client_code_ota.py --encrypted" not in content:
                return False
        return True


def canonical_bundle_object_name(bundle_name: str) -> str:
    name = (bundle_name or "").strip().lower()
    variants: list[str] = []
    for token in (
        "with_nodejs",
        "with_ppt_runtime",
        "with_memory_document_runtime",
        "with_douyin_runtime",
        "with_wechat_runtime",
        "encrypted",
    ):
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
    if not is_encrypted_ota_zip(zip_path):
        print(
            "[ERR] 拒绝发布未加密 OTA：必须使用 pack_client_code_ota.py 的默认加密模式（或 --encrypted）",
            file=sys.stderr,
        )
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
