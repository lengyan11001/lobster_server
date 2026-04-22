#!/usr/bin/env bash
set -e
echo "=== Restarting via server script ==="
cd /opt/lobster-server
sudo systemctl restart lobster-backend lobster-mcp
sleep 3
sudo systemctl status lobster-backend --no-pager -l | head -10
echo ""
sudo systemctl status lobster-mcp --no-pager -l | head -10
echo "=== Done ==="
