#!/usr/bin/env bash
echo "=== Comfly env vars ==="
grep -i comfly /opt/lobster-server/.env 2>/dev/null || echo "(no comfly in .env)"
echo ""
echo "=== Recent Comfly routing logs ==="
tail -300 /opt/lobster-server/logs/app.log 2>/dev/null | grep -iE "comfly|Comfly路由" | tail -10
