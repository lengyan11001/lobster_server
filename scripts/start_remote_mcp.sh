#!/usr/bin/env bash
# 在服务器上启动 MCP（无 systemd 时）；需 .env.deploy
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
set -a
# shellcheck source=../.env.deploy
. "$ROOT/.env.deploy"
set +a
AP="$(mktemp)"
{
  echo '#!/usr/bin/env sh'
  echo 'printf %s\\n "$LOBSTER_SSH_KEY_PASSPHRASE"'
} > "$AP"
chmod +x "$AP"
eval "$(ssh-agent -s)"
trap 'rm -f "$AP"; eval "$(ssh-agent -k)" 2>/dev/null || true' EXIT
export SSH_ASKPASS_REQUIRE=force
export SSH_ASKPASS="$AP"
export DISPLAY="${DISPLAY:-localhost:0}"
ssh-add "$LOBSTER_DEPLOY_SSH_KEY"
RDIR="${LOBSTER_DEPLOY_REMOTE_DIR:-/opt/lobster-server}"
# shellcheck disable=SC2029
ssh -o StrictHostKeyChecking=accept-new "$LOBSTER_DEPLOY_HOST" bash <<EOF
set +e
cd $(printf %q "$RDIR")
[ -f .env ] && set -a && . ./.env && set +a
export PYTHONPATH="\$PWD"
PY="\$PWD/.venv/bin/python"
pkill -f "mcp --port" 2>/dev/null
pkill -f "python -m mcp" 2>/dev/null
sleep 2
nohup "\$PY" -m mcp --port "\${MCP_PORT:-8001}" >> mcp.log 2>&1 &
sleep 2
pgrep -af "python -m mcp" || pgrep -af "mcp --port" || true
tail -2 mcp.log
EOF
