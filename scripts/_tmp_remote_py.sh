#!/usr/bin/env bash
# Run a python snippet on the remote server
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# reuse ssh_run_remote.sh's SSH setup
exec bash "$ROOT/scripts/ssh_run_remote.sh" "cd /opt/lobster-server && .venv/bin/python3 << 'PYEOF'
import sys; sys.path.insert(0, '.')
from backend.app.db import SessionLocal
from backend.app.models import User
db = SessionLocal()
u = db.query(User).filter(User.id == 4).first()
if u:
    print('user_id=4 credits=' + str(u.credits))
else:
    print('not found')
db.close()
PYEOF"
