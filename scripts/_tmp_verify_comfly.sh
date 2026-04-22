#!/usr/bin/env bash
echo "=== Test Comfly configured ==="
tail -50 /opt/lobster-server/logs/app.log 2>/dev/null | grep -iE "comfly|Comfly路由|Lobster API" | tail -5
echo ""
echo "=== Quick health check ==="
curl -s http://127.0.0.1:8000/api/health | head -200
