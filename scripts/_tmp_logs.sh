#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/ssh_run_remote.sh 'journalctl -u lobster-backend --since "3 minutes ago" --no-pager -n 300 2>/dev/null | grep -i "chat_trace\|DSML\|text_calls\|tool_call\|invoke_cap\|search_models\|enforce\|小狗\|puppy\|error" || echo "NO_MATCHING_LOGS"'
