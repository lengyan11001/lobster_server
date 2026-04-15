#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ -f "$ROOT/.env.deploy" ]; then
  set -a
  . "$ROOT/.env.deploy"
  set +a
fi

export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="ssh -i $LOBSTER_DEPLOY_SSH_KEY -o StrictHostKeyChecking=accept-new"

echo "=== Pushing to origin main ==="
git push origin main 2>&1
echo "=== Push done ==="
