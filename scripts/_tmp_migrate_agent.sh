#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/ssh_run_remote.sh "cd /opt/lobster-server && python3 -c \"
import sqlite3
conn = sqlite3.connect('lobster.db')
c = conn.cursor()

# Check if columns exist
c.execute('PRAGMA table_info(users)')
cols = [r[1] for r in c.fetchall()]
print('Current columns:', cols)

if 'is_agent' not in cols:
    c.execute('ALTER TABLE users ADD COLUMN is_agent BOOLEAN NOT NULL DEFAULT 0')
    print('Added is_agent column')
else:
    print('is_agent already exists')

if 'parent_user_id' not in cols:
    c.execute('ALTER TABLE users ADD COLUMN parent_user_id INTEGER')
    c.execute('CREATE INDEX IF NOT EXISTS ix_users_parent_user_id ON users(parent_user_id)')
    print('Added parent_user_id column + index')
else:
    print('parent_user_id already exists')

conn.commit()

# Verify
c.execute('PRAGMA table_info(users)')
print('Updated columns:', [r[1] for r in c.fetchall()])
conn.close()
\""
