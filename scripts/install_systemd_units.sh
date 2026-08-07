#!/usr/bin/env bash
# Install/update Lobster systemd units on the production server.
set -euo pipefail

ROOT="${1:-/opt/lobster-server}"
USER_NAME="${LOBSTER_SERVICE_USER:-ubuntu}"
PY="$ROOT/.venv/bin/python3"
NODE="$ROOT/.runtime/node/bin/node"

if [ ! -x "$PY" ]; then
  echo "[ERR] Python not found: $PY" >&2
  exit 1
fi
if [ ! -f "$ROOT/.env" ]; then
  echo "[ERR] Missing env file: $ROOT/.env" >&2
  exit 1
fi
if [ ! -x "$NODE" ]; then
  echo "[ERR] Node runtime not found: $NODE" >&2
  echo "Run: $ROOT/scripts/install_mastra_runtime.sh $ROOT" >&2
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
Environment=LOBSTER_BACKEND_AUTOSTART_MCP=0
EnvironmentFile=$ROOT/.env
# Uvicorn workers otherwise inherit the systemd soft default of 1024. A burst
# of clients that time out upstream must not exhaust the accept loop at 1024.
LimitNOFILE=65536
ExecStart=$PY -m backend.run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

sudo tee /etc/systemd/system/lobster-background.service >/dev/null <<UNIT
[Unit]
Description=Lobster Background Worker
After=network.target postgresql.service lobster-mcp.service lobster-backend.service lobster-mastra.service
Wants=lobster-mcp.service lobster-mastra.service

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

sudo tee /etc/systemd/system/lobster-mastra.service >/dev/null <<UNIT
[Unit]
Description=Lobster Mastra Orchestrator
After=network.target postgresql.service lobster-mcp.service lobster-backend.service
Wants=lobster-mcp.service lobster-backend.service

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$ROOT/mastra_server
EnvironmentFile=$ROOT/.env
Environment=NODE_ENV=production
Environment=NODE_OPTIONS=--max-old-space-size=768
Environment=LOBSTER_MASTRA_PORT=4111
ExecStart=$NODE $ROOT/mastra_server/.mastra/output/index.mjs
Restart=always
RestartSec=5
MemoryMax=1G

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
sudo systemctl enable lobster-backend lobster-background lobster-mcp lobster-mastra
echo "[OK] systemd units installed for $ROOT as user $USER_NAME"
