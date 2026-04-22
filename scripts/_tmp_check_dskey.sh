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
source /opt/lobster-server/.env 2>/dev/null
echo "DEEPSEEK_API_KEY exists: $([ -n "$DEEPSEEK_API_KEY" ] && echo YES_len=$(echo -n "$DEEPSEEK_API_KEY" | wc -c) || echo NO)"
echo "DEEPSEEK_API_BASE: ${DEEPSEEK_API_BASE:-not_set}"
REMOTE
