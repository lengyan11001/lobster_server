#!/usr/bin/env bash
# 安装/更新 manage（项目管理与 AI 赋能）独立站的服务单元。
# 只写 lobster-manage.service，不动其它 unit。
set -euo pipefail

ROOT="${1:-/opt/lobster-server}"
USER_NAME="${LOBSTER_SERVICE_USER:-ubuntu}"
PY="$ROOT/.venv/bin/python3"

if [ ! -x "$PY" ]; then echo "[ERR] python not found: $PY" >&2; exit 1; fi
if [ ! -f "$ROOT/.env" ]; then echo "[ERR] missing env: $ROOT/.env" >&2; exit 1; fi

sudo tee /etc/systemd/system/lobster-manage.service >/dev/null <<UNIT
[Unit]
Description=Lobster Manage (项目管理与 AI 赋能)
After=network.target postgresql.service lobster-backend.service

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$ROOT
Environment=PYTHONPATH=$ROOT
Environment=MANAGE_HOST=127.0.0.1
Environment=MANAGE_PORT=8020
Environment=MANAGE_SEED_OWNER_EMAIL=${MANAGE_SEED_OWNER_EMAIL:-}
EnvironmentFile=$ROOT/.env
ExecStart=$PY -m backend.manage_run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable lobster-manage >/dev/null 2>&1 || true
sudo systemctl restart lobster-manage
sleep 2
systemctl is-active lobster-manage
echo "[ok] lobster-manage: http://127.0.0.1:8020"
