#!/usr/bin/env bash
cd "$(dirname "$0")/.." || exit 1
source .env.deploy 2>/dev/null || true
SSH_KEY="${LOBSTER_DEPLOY_SSH_KEY:-/d/maczhuji}"
HOST="${LOBSTER_DEPLOY_HOST:-ubuntu@42.194.209.150}"
export LOBSTER_SSH_KEY_PASSPHRASE="${LOBSTER_SSH_KEY_PASSPHRASE:-}"

eval "$(ssh-agent -s)" > /dev/null 2>&1
if [ -n "$LOBSTER_SSH_KEY_PASSPHRASE" ]; then
  DISPLAY= SSH_ASKPASS_REQUIRE=force SSH_ASKPASS="$(mktemp)" bash -c "echo '#!/bin/sh'; echo 'echo \"\$LOBSTER_SSH_KEY_PASSPHRASE\"'" > /tmp/_sshask.sh
  chmod +x /tmp/_sshask.sh
  DISPLAY= SSH_ASKPASS=/tmp/_sshask.sh SSH_ASKPASS_REQUIRE=force ssh-add "$SSH_KEY" < /dev/null 2>/dev/null
else
  ssh-add "$SSH_KEY" 2>/dev/null
fi

ssh -o StrictHostKeyChecking=no "$HOST" 'bash -s' <<'REMOTE'
cd /opt/lobster-server
source .env 2>/dev/null
BIHUO_TOKEN=$(echo "$SUTUI_SERVER_TOKENS_BIHUO" | cut -d, -f1 | tr -d " ")
echo "=== Checking xskill balance for bihuo pool ==="
if [ -n "$BIHUO_TOKEN" ]; then
  curl -sS -H "Authorization: Bearer $BIHUO_TOKEN" https://api.xskill.ai/v1/dashboard/billing/usage 2>&1 | head -500
  echo ""
  echo "---"
  curl -sS -H "Authorization: Bearer $BIHUO_TOKEN" https://api.xskill.ai/v1/dashboard/billing/subscription 2>&1 | head -500
  echo ""
  echo "---"
  curl -sS -H "Authorization: Bearer $BIHUO_TOKEN" https://api.xskill.ai/v1/billing/balance 2>&1 | head -500
else
  echo "BIHUO TOKEN not found"
fi
REMOTE
