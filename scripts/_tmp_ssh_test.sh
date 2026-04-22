#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ -f "$ROOT/.env.deploy" ]; then
  set -a
  . "$ROOT/.env.deploy"
  set +a
fi

HOST="${LOBSTER_DEPLOY_HOST_TEST}"
KEY="${LOBSTER_DEPLOY_SSH_KEY:-}"
PASSPHRASE="${LOBSTER_SSH_KEY_PASSPHRASE:-}"

echo "[test] Trying SSH to $HOST ..."

_ssh_agent_has_keys() {
  [ -n "${SSH_AUTH_SOCK:-}" ] && ssh-add -l >/dev/null 2>&1
}

if ! _ssh_agent_has_keys; then
  if [ -n "$KEY" ] && [ -r "$KEY" ] && [ -n "$PASSPHRASE" ]; then
    AP="$(mktemp)"
    echo '#!/usr/bin/env sh' > "$AP"
    echo 'printf %s\\n "$LOBSTER_SSH_KEY_PASSPHRASE"' >> "$AP"
    chmod +x "$AP"
    eval "$(ssh-agent -s)"
    export SSH_ASKPASS_REQUIRE=force
    export SSH_ASKPASS="$AP"
    export DISPLAY="${DISPLAY:-localhost:0}"
    ssh-add "$KEY"
    rm -f "$AP"
  fi
fi

SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
if ! _ssh_agent_has_keys && [ -n "$KEY" ] && [ -r "$KEY" ]; then
  SSH_OPTS="-i $KEY $SSH_OPTS"
fi

ssh $SSH_OPTS "$HOST" "echo SSH_OK; hostname; ls -la /opt/lobster-server/ 2>/dev/null || echo NO_REPO_AT_OPT; cat /etc/os-release | head -3"
