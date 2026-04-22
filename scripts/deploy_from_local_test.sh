#!/usr/bin/env bash
# 仅在「测试环境 lobster-server」执行：远端 git pull + server_update_and_restart.sh
# 与 deploy_from_local.sh 区别：只读 LOBSTER_DEPLOY_HOST_TEST，绝不部署生产与海外机。
# 依赖：lobster-server/.env.deploy 中配置 LOBSTER_DEPLOY_HOST_TEST（见 .env.deploy.example）
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ -f "$ROOT/.env.deploy" ]; then
  set -a
  # shellcheck source=../.env.deploy
  . "$ROOT/.env.deploy"
  set +a
fi

export GIT_TERMINAL_PROMPT=0

if [ -z "${LOBSTER_DEPLOY_HOST_TEST:-}" ]; then
  echo "[ERR] 未设置 LOBSTER_DEPLOY_HOST_TEST。请在 lobster-server/.env.deploy 配置测试机，例如：" >&2
  echo "  LOBSTER_DEPLOY_HOST_TEST=ubuntu@139.199.168.36" >&2
  echo "  LOBSTER_DEPLOY_REMOTE_DIR_TEST=/opt/lobster-server" >&2
  exit 1
fi

if [ -n "${LOBSTER_DEPLOY_HOST:-}" ] && [ "$LOBSTER_DEPLOY_HOST_TEST" = "$LOBSTER_DEPLOY_HOST" ]; then
  echo "[ERR] LOBSTER_DEPLOY_HOST_TEST 与 LOBSTER_DEPLOY_HOST 相同，拒绝执行以免误发到生产。" >&2
  exit 1
fi

REMOTE_DIR="${LOBSTER_DEPLOY_REMOTE_DIR_TEST:-/opt/lobster-server}"
SSH_BASE="-o StrictHostKeyChecking=accept-new"
DEPLOY_KEY="${LOBSTER_DEPLOY_SSH_KEY_TEST:-${LOBSTER_DEPLOY_SSH_KEY:-}}"

echo ""
echo "========================================"
echo "[TEST ONLY] 本脚本只会部署到下面这一台（不会跑 deploy_from_local.sh / 生产）"
echo "[TEST ONLY]   SSH: ${LOBSTER_DEPLOY_HOST_TEST}"
echo "[TEST ONLY]   DIR: ${REMOTE_DIR}"
echo "========================================"
echo ""

_ssh_agent_has_keys() {
  [ -n "${SSH_AUTH_SOCK:-}" ] && ssh-add -l >/dev/null 2>&1
}

_DEPLOY_SSH_AGENT_STARTED=0
_deploy_cleanup_ssh_agent() {
  if [ "$_DEPLOY_SSH_AGENT_STARTED" = 1 ]; then
    eval "$(ssh-agent -k)" 2>/dev/null || true
    _DEPLOY_SSH_AGENT_STARTED=0
  fi
}

_ssh_private_key_seems_encrypted() {
  local k="$1"
  [ ! -r "$k" ] && return 1
  grep -q "ENCRYPTED" "$k" 2>/dev/null && return 0
  if ssh-keygen -y -f "$k" -P "" >/dev/null 2>&1; then
    return 1
  fi
  return 0
}

if ! _ssh_agent_has_keys; then
  if [ -n "$DEPLOY_KEY" ] && [ -r "$DEPLOY_KEY" ]; then
    if _ssh_private_key_seems_encrypted "$DEPLOY_KEY" && [ -z "${LOBSTER_SSH_KEY_PASSPHRASE:-}" ]; then
      echo "[ERR] 部署私钥已加密：请在 lobster-server/.env.deploy 配置 LOBSTER_SSH_KEY_PASSPHRASE，或先 ssh-add。" >&2
      exit 1
    fi
  fi
fi

if ! _ssh_agent_has_keys; then
  if [ -n "$DEPLOY_KEY" ] && [ -r "$DEPLOY_KEY" ] && [ -n "${LOBSTER_SSH_KEY_PASSPHRASE:-}" ]; then
    AP="$(mktemp)"
    {
      echo '#!/usr/bin/env sh'
      echo 'printf %s\\n "$LOBSTER_SSH_KEY_PASSPHRASE"'
    } > "$AP"
    chmod +x "$AP"
    trap 'rm -f "$AP"; _deploy_cleanup_ssh_agent' EXIT
    eval "$(ssh-agent -s)"
    _DEPLOY_SSH_AGENT_STARTED=1
    export SSH_ASKPASS_REQUIRE=force
    export SSH_ASKPASS="$AP"
    export DISPLAY="${DISPLAY:-localhost:0}"
    ssh-add "$DEPLOY_KEY"
    rm -f "$AP"
    trap _deploy_cleanup_ssh_agent EXIT
  fi
fi

SSH_OPTS_MAIN="$SSH_BASE"
if ! _ssh_agent_has_keys; then
  if [ -n "$DEPLOY_KEY" ] && [ -r "$DEPLOY_KEY" ]; then
    SSH_OPTS_MAIN="-i $DEPLOY_KEY $SSH_BASE"
  fi
fi

if ! _ssh_agent_has_keys && [ -z "$DEPLOY_KEY" ]; then
  echo "[ERR] 未检测到 ssh-agent 中的密钥，且未配置 LOBSTER_DEPLOY_SSH_KEY / LOBSTER_DEPLOY_SSH_KEY_TEST。" >&2
  exit 1
fi

_run_remote() {
  local host="$1"
  local dir="$2"
  local sshopts="$3"
  echo "[部署·测试] SSH $host → cd $dir && git pull origin main && bash scripts/server_update_and_restart.sh"
  ssh $sshopts "$host" "cd $dir && git fetch origin main && git pull origin main && bash scripts/server_update_and_restart.sh"
}

_run_remote "$LOBSTER_DEPLOY_HOST_TEST" "$REMOTE_DIR" "$SSH_OPTS_MAIN"
echo "[完成] 测试服务器已更新并重启"
