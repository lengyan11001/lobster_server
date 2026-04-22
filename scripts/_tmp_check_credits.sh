#!/usr/bin/env bash
echo "=== Recent credit/billing logs ==="
tail -200 /opt/lobster-server/logs/app.log 2>/dev/null | grep -iE "credit|balance|402|insufficient|不足|deduct|comfly.*路由" | tail -20
echo ""
echo "=== User 4 current balance ==="
tail -200 /opt/lobster-server/logs/app.log 2>/dev/null | grep -iE "user_lobster_credits|balance_after" | tail -5
