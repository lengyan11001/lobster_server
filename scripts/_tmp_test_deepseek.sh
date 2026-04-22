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
echo "=== Testing DeepSeek direct connection ==="
echo "Key length: $(echo -n "$DEEPSEEK_API_KEY" | wc -c)"
echo "Key prefix: $(echo "$DEEPSEEK_API_KEY" | cut -c1-8)..."
RESP=$(curl -sS -w "\n__HTTP_CODE__:%{http_code}" \
  -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"hi"}],"max_tokens":5}' \
  https://api.deepseek.com/v1/chat/completions 2>&1)
HTTP_CODE=$(echo "$RESP" | grep '__HTTP_CODE__' | sed 's/__HTTP_CODE__://')
BODY=$(echo "$RESP" | grep -v '__HTTP_CODE__')
echo "HTTP Status: $HTTP_CODE"
echo "Response: $(echo "$BODY" | head -500)"
REMOTE
