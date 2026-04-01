#!/usr/bin/env bash
# 本机无法 git push 时：用 .env.deploy 的密钥将 mcp/http_server.py 拷到服务器并重启 lobster-mcp
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ -f "$ROOT/.env.deploy" ]; then
  set -a
  # shellcheck source=../.env.deploy
  . "$ROOT/.env.deploy"
  set +a
fi
: "${LOBSTER_SSH_KEY_PASSPHRASE:?请在 .env.deploy 中配置 LOBSTER_SSH_KEY_PASSPHRASE}"
: "${LOBSTER_DEPLOY_HOST:?请在 .env.deploy 中配置 LOBSTER_DEPLOY_HOST}"
: "${LOBSTER_DEPLOY_SSH_KEY:?请在 .env.deploy 中配置 LOBSTER_DEPLOY_SSH_KEY}"
REMOTE_DIR="${LOBSTER_DEPLOY_REMOTE_DIR:-/opt/lobster-server}"
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
echo ">>> scp mcp/http_server.py -> $LOBSTER_DEPLOY_HOST:$REMOTE_DIR/mcp/http_server.py"
scp -o StrictHostKeyChecking=accept-new \
  "$ROOT/mcp/http_server.py" \
  "$LOBSTER_DEPLOY_HOST:$REMOTE_DIR/mcp/http_server.py"
echo ">>> restart MCP（优先 systemd，否则与 server_update_and_restart 一致的 nohup）"
ssh -o StrictHostKeyChecking=accept-new "$LOBSTER_DEPLOY_HOST" bash <<EOF
set +e
cd $(printf %q "$REMOTE_DIR")
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files --type=service 2>/dev/null | grep -qE 'lobster-mcp|lobster_mcp'; then
  sudo systemctl restart lobster-mcp 2>/dev/null || sudo systemctl restart lobster_mcp
  sudo systemctl is-active lobster-mcp 2>/dev/null || sudo systemctl is-active lobster_mcp || true
else
  echo ">>> nohup 启动 MCP"
  [ -f .env ] && set -a && . ./.env && set +a
  export PYTHONPATH="\$PWD"
  PY="\$PWD/.venv/bin/python"
  pkill -f "mcp --port" 2>/dev/null
  pkill -f "python -m mcp" 2>/dev/null
  sleep 2
  nohup "\$PY" -m mcp --port "\${MCP_PORT:-8001}" >> mcp.log 2>&1 &
  sleep 2
  pgrep -af "python -m mcp" || true
  tail -2 mcp.log
fi
EOF
echo "[完成] MCP http_server 已更新并重启"
