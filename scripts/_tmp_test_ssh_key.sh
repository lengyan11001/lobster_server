#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
set -a; . "$ROOT/.env.deploy"; set +a

eval "$(ssh-agent -s)"
AP="$(mktemp)"
{
  echo '#!/usr/bin/env sh'
  echo 'printf %s\\n "$LOBSTER_SSH_KEY_PASSPHRASE"'
} > "$AP"
chmod +x "$AP"
export SSH_ASKPASS_REQUIRE=force SSH_ASKPASS="$AP" DISPLAY="${DISPLAY:-localhost:0}"
ssh-add /d/maczhuji
rm -f "$AP"

ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "$LOBSTER_DEPLOY_HOST_TEST" "echo SSH_KEY_AUTH_OK; hostname"
eval "$(ssh-agent -k)" 2>/dev/null || true
