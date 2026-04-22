#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ -f "$ROOT/.env.deploy" ]; then
  set -a; . "$ROOT/.env.deploy"; set +a
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

ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
  "ubuntu@${LOBSTER_DEPLOY_HOST}" \
  'cd /opt/lobster-server && .venv/bin/python3 -c "
import sys; sys.path.insert(0,\".\")
from backend.app.db import SessionLocal
from backend.app.models import User
db=SessionLocal()
u=db.query(User).filter(User.id==4).first()
print(f\"user_id=4 credits={u.credits}\") if u else print(\"not found\")
db.close()
"'
