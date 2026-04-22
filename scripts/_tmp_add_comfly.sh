#!/usr/bin/env bash
set -e
ENV=/opt/lobster-server/.env
echo "=== Adding Comfly config ==="
echo '' >> "$ENV"
echo '# Comfly API' >> "$ENV"
echo 'COMFLY_API_BASE=https://ai.comfly.chat/v1' >> "$ENV"
echo 'COMFLY_API_KEY=sk-ajXMziHoNmrc35fdtrFCg3jFaFwnQFpsWxS1FUO6czNJeLZ7' >> "$ENV"
echo 'COMFLY_API_KEY_PREMIUM=sk-FtluOvNh9Vu1w3DMpfquIpcKxK5jbkPI1YZmMm0Hci24oymp' >> "$ENV"
echo "=== Verify ==="
grep COMFLY "$ENV"
echo "=== Restarting ==="
systemctl restart lobster-backend lobster-mcp
sleep 3
systemctl status lobster-backend --no-pager -l | head -15
echo "=== Done ==="
