#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ -f "$ROOT/.env.deploy" ]; then
  set -a
  . "$ROOT/.env.deploy"
  set +a
fi
: "${LOBSTER_DEPLOY_HOST:?missing}"

SSH_KEY="${LOBSTER_DEPLOY_SSH_KEY:-}"
if [ -n "$SSH_KEY" ] && [ -f "$SSH_KEY" ]; then
  eval "$(ssh-agent -s)" >/dev/null 2>&1
  if [ -n "${LOBSTER_SSH_KEY_PASSPHRASE:-}" ]; then
    export SSH_ASKPASS_REQUIRE=force
    export SSH_ASKPASS="$ROOT/scripts/_echo_pass.sh"
    export LOBSTER_SSH_KEY_PASSPHRASE
    ssh-add "$SSH_KEY" </dev/null 2>/dev/null
  else
    ssh-add "$SSH_KEY" 2>/dev/null
  fi
fi

echo "HOST=$LOBSTER_DEPLOY_HOST"
ssh -v -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
  "ubuntu@${LOBSTER_DEPLOY_HOST}" \
  "echo 'connected'; sudo journalctl -u lobster-mcp --since '15 min ago' --no-pager -n 50 2>&1 | head -30"
