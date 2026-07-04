#!/usr/bin/env python3
"""Windows-friendly server deploy entrypoint.

Equivalent to scripts/deploy_from_local.sh: SSH to the configured server,
reset the remote checkout to origin/main, then run server_update_and_restart.sh.
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _split_host(value: str) -> tuple[str, str]:
    if "@" in value:
        user, host = value.split("@", 1)
        return user, host
    return "ubuntu", value


@dataclass(frozen=True)
class Target:
    name: str
    host: str
    remote_dir: str
    key_path: str = ""
    key_passphrase: str = ""
    password: str = ""


def _target_from_env(env: dict[str, str], *, test: bool = False, overseas: bool = False) -> Target | None:
    if test:
        host = env.get("LOBSTER_DEPLOY_HOST_TEST", "").strip()
        if not host:
            return None
        prod = env.get("LOBSTER_DEPLOY_HOST", "").strip()
        if prod and prod == host:
            raise SystemExit("[ERR] LOBSTER_DEPLOY_HOST_TEST 与 LOBSTER_DEPLOY_HOST 相同，拒绝测试部署。")
        return Target(
            name="test",
            host=host,
            remote_dir=env.get("LOBSTER_DEPLOY_REMOTE_DIR_TEST", "").strip() or "/opt/lobster-server",
            key_path=env.get("LOBSTER_DEPLOY_SSH_KEY_TEST", "").strip() or env.get("LOBSTER_DEPLOY_SSH_KEY", "").strip(),
            key_passphrase=env.get("LOBSTER_SSH_KEY_PASSPHRASE_TEST", "").strip()
            or env.get("LOBSTER_SSH_KEY_PASSPHRASE", "").strip(),
            password=env.get("LOBSTER_DEPLOY_PASSWORD_TEST", "").strip() or env.get("LOBSTER_DEPLOY_PASSWORD", "").strip(),
        )

    if overseas:
        host = env.get("LOBSTER_DEPLOY_HOST_OVERSEAS", "").strip()
        if not host:
            return None
        return Target(
            name="overseas",
            host=host,
            remote_dir=env.get("LOBSTER_DEPLOY_REMOTE_DIR_OVERSEAS", "").strip()
            or env.get("LOBSTER_DEPLOY_REMOTE_DIR", "").strip()
            or "/opt/lobster-server",
            key_path=env.get("LOBSTER_DEPLOY_SSH_KEY_OVERSEAS", "").strip()
            or env.get("LOBSTER_DEPLOY_SSH_KEY", "").strip(),
            key_passphrase=env.get("LOBSTER_SSH_KEY_PASSPHRASE_OVERSEAS", "").strip()
            or env.get("LOBSTER_SSH_KEY_PASSPHRASE", "").strip(),
            password=env.get("LOBSTER_DEPLOY_PASSWORD_OVERSEAS", "").strip(),
        )

    host = env.get("LOBSTER_DEPLOY_HOST", "").strip()
    if not host:
        return None
    return Target(
        name="primary",
        host=host,
        remote_dir=env.get("LOBSTER_DEPLOY_REMOTE_DIR", "").strip() or "/opt/lobster-server",
        key_path=env.get("LOBSTER_DEPLOY_SSH_KEY", "").strip(),
        key_passphrase=env.get("LOBSTER_SSH_KEY_PASSPHRASE", "").strip(),
        password=env.get("LOBSTER_DEPLOY_PASSWORD", "").strip(),
    )


def _connect(target: Target):
    try:
        import paramiko
    except Exception as exc:
        raise SystemExit("缺少 paramiko，先执行：python -m pip install paramiko") from exc

    user, host = _split_host(target.host)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    base_kwargs = {
        "username": user,
        "timeout": 25,
        "banner_timeout": 25,
        "auth_timeout": 25,
    }

    if target.key_path:
        key = Path(target.key_path)
        if key.is_file():
            key_loaders = [
                getattr(paramiko, name)
                for name in ("RSAKey", "Ed25519Key", "ECDSAKey", "DSSKey")
                if hasattr(paramiko, name)
            ]
            for loader in key_loaders:
                try:
                    pkey = loader.from_private_key_file(str(key), password=target.key_passphrase or None)
                    client.connect(host, pkey=pkey, **base_kwargs)
                    return client
                except Exception:
                    continue
            if not target.password:
                raise SystemExit(f"[ERR] SSH key auth failed and no password fallback configured: {key}")

    if target.password:
        client.connect(host, password=target.password, **base_kwargs)
        return client

    client.connect(host, **base_kwargs)
    return client


def _run(client, command: str, *, timeout: int = 900) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def _deploy_command(remote_dir: str, *, reset: bool = True) -> str:
    quoted_dir = shlex.quote(remote_dir)
    if reset:
        return f"""cd {quoted_dir} && \
PREV=$(git rev-parse HEAD) && echo "$PREV" > .deploy_rollback_commit && \
echo "[backup] previous=$PREV" && \
git fetch origin main && \
DIRTY_TRACKED="$(git status --porcelain --untracked-files=no)" && \
if [ -n "$DIRTY_TRACKED" ]; then BACKUP_DIR=.deploy_dirty_backups; mkdir -p "$BACKUP_DIR"; BACKUP_PATCH="$BACKUP_DIR/$(date +%Y%m%d_%H%M%S)_$(git rev-parse --short HEAD).patch"; git diff > "$BACKUP_PATCH"; echo "[WARN] tracked dirty backup=$BACKUP_PATCH"; fi && \
git reset --hard origin/main && \
bash scripts/server_update_and_restart.sh && \
echo "[verify] commit=$(git rev-parse --short HEAD)" && \
(systemctl is-active lobster-backend lobster-mcp lobster-background lobster-h5 2>/dev/null || true)"""
    return f"""cd {quoted_dir} && \
git fetch origin main && git pull origin main && \
bash scripts/server_update_and_restart.sh && \
echo "[verify] commit=$(git rev-parse --short HEAD)" && \
(systemctl is-active lobster-backend lobster-mcp lobster-background lobster-h5 2>/dev/null || true)"""


def deploy_target(target: Target, *, reset: bool = True) -> None:
    print(f"[deploy] {target.name}: {target.host} dir={target.remote_dir}")
    client = _connect(target)
    try:
        code, out, err = _run(client, _deploy_command(target.remote_dir, reset=reset), timeout=900)
    finally:
        client.close()
    if out:
        print(out)
    if err:
        print("[stderr]", file=sys.stderr)
        print(err, file=sys.stderr)
    if code != 0:
        raise SystemExit(f"[ERR] deploy failed on {target.host}, code={code}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy lobster_server from Windows/Python without local bash.")
    parser.add_argument("--env", type=Path, default=ROOT / ".env.deploy", help=".env.deploy path")
    parser.add_argument("--test", action="store_true", help="Deploy only LOBSTER_DEPLOY_HOST_TEST")
    parser.add_argument("--no-reset", action="store_true", help="Use git pull instead of reset --hard origin/main")
    args = parser.parse_args()

    env = _load_env(args.env)
    if args.test:
        target = _target_from_env(env, test=True)
        if not target:
            raise SystemExit("[ERR] 未设置 LOBSTER_DEPLOY_HOST_TEST")
        deploy_target(target, reset=not args.no_reset)
        return 0

    if _truthy(env.get("LOBSTER_DEPLOY_ONLY_TEST")) or _truthy(env.get("LOBSTER_DEPLOY_TEST_MODE")):
        raise SystemExit("[ERR] .env.deploy 已开启仅测试部署，拒绝正式部署。请用 --test。")

    primary = _target_from_env(env)
    if not primary:
        raise SystemExit("[ERR] 未设置 LOBSTER_DEPLOY_HOST")
    deploy_target(primary, reset=not args.no_reset)

    if _truthy(env.get("LOBSTER_DEPLOY_OVERSEAS")):
        overseas = _target_from_env(env, overseas=True)
        if overseas:
            deploy_target(overseas, reset=not args.no_reset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
