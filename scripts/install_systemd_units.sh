#!/usr/bin/env bash
# Install/update Lobster systemd units on the production server.
set -euo pipefail

ROOT="${1:-/opt/lobster-server}"
USER_NAME="${LOBSTER_SERVICE_USER:-ubuntu}"
PY="$ROOT/.venv/bin/python3"

if [ ! -x "$PY" ]; then
  echo "[ERR] Python not found: $PY" >&2
  exit 1
fi
if [ ! -f "$ROOT/.env" ]; then
  echo "[ERR] Missing env file: $ROOT/.env" >&2
  exit 1
fi

sudo tee /etc/systemd/system/lobster-backend.service >/dev/null <<UNIT
[Unit]
Description=Lobster Backend API
After=network.target postgresql.service lobster-mcp.service
Wants=lobster-mcp.service

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$ROOT
Environment=PYTHONPATH=$ROOT
Environment=BACKEND_WORKERS=2
EnvironmentFile=$ROOT/.env
ExecStart=$PY -m backend.run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

sudo tee /etc/systemd/system/lobster-background.service >/dev/null <<UNIT
[Unit]
Description=Lobster Background Worker
After=network.target postgresql.service lobster-mcp.service lobster-backend.service
Wants=lobster-mcp.service

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$ROOT
Environment=PYTHONPATH=$ROOT
EnvironmentFile=$ROOT/.env
ExecStart=$PY -m backend.background_worker
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

sudo tee /etc/systemd/system/lobster-mcp.service >/dev/null <<UNIT
[Unit]
Description=Lobster MCP Server
After=network.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$ROOT
Environment=PYTHONPATH=$ROOT
EnvironmentFile=$ROOT/.env
ExecStart=$PY -m mcp --port 8001
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable lobster-backend lobster-background lobster-mcp
echo "[OK] systemd units installed for $ROOT as user $USER_NAME"
