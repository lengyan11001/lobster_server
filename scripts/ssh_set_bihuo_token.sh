#!/usr/bin/env bash
# 更新大陆机 .env：仅 SUTUI_SERVER_TOKENS_BIHUO（无品牌用户不得走 USER 兜底），并重启
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOK="${1:?用法: bash scripts/ssh_set_bihuo_token.sh sk-...}"
bash "$ROOT/scripts/ssh_run_remote.sh" "cd /root/lobster_server && cp -a .env .env.bak.bihuo_token.\$(date +%s) && .venv/bin/python3 -c \"
import re
from pathlib import Path
tok = '''${TOK}'''
p = Path('.env')
t = p.read_text(encoding='utf-8')
for key in ('SUTUI_SERVER_TOKENS_BIHUO',):
    if re.search(r'^' + re.escape(key) + '=', t, re.M):
        t = re.sub(r'^' + re.escape(key) + '=.*$', key + '=' + tok, t, flags=re.M)
    else:
        t += '\\n' + key + '=' + tok + '\\n'
p.write_text(t, encoding='utf-8')
print('OK bihuo token')
\" && bash scripts/server_update_and_restart.sh"
