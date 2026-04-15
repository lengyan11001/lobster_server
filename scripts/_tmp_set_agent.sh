#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/ssh_run_remote.sh "cd /opt/lobster-server && python3 -c \"
import sqlite3
conn = sqlite3.connect('lobster.db')
c = conn.cursor()
c.execute('UPDATE users SET is_agent = 1 WHERE email = ?', ('z717010460',))
print('Updated rows:', c.rowcount)
conn.commit()
c.execute('SELECT id, email, is_agent, parent_user_id FROM users WHERE email = ?', ('z717010460',))
print('User:', c.fetchone())
conn.close()
\""
