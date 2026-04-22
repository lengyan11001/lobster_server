#!/usr/bin/env bash
echo "=== Last 50 lines from journalctl ==="
journalctl -u lobster-backend --no-pager -n 50 2>/dev/null | tail -30
echo "=== Active connections (netstat) ==="
ss -tnp | grep -E ':8000|:8001' | head -10
