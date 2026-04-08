#!/bin/bash
set -e
ASKPASS=$(mktemp); echo '#!/bin/sh' > "$ASKPASS"; echo 'echo lengyan2' >> "$ASKPASS"; chmod +x "$ASKPASS"
export SSH_ASKPASS="$ASKPASS" SSH_ASKPASS_REQUIRE=force DISPLAY=:0
eval $(ssh-agent -s) >/dev/null 2>&1; ssh-add /d/maczhuji >/dev/null 2>&1
cd /d/lobster-server
git add -A && git commit -m "perf: skip tool-weak models (deepseek) for tool_calls requests, use opus directly"
export GIT_SSH_COMMAND="ssh -p 443 -o StrictHostKeyChecking=no"
git remote set-url origin ssh://git@ssh.github.com:443/lengyan11001/lobster_server.git
git push origin main 2>&1
git remote set-url origin git@github.com:lengyan11001/lobster_server.git
echo "=== Deploying to China server ==="
ssh -o StrictHostKeyChecking=no root@47.120.39.220 \
  "cd /root/lobster_server && git fetch origin main && git pull origin main && bash scripts/server_update_and_restart.sh" 2>&1
kill $SSH_AGENT_PID 2>/dev/null || true; rm -f "$ASKPASS"
echo "=== DONE ==="
