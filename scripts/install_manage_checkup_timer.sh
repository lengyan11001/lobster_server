#!/usr/bin/env bash
# 安装定时项目体检：每天 09:00 / 17:00（服务器本地时区）各跑一次。
set -euo pipefail
ROOT="${1:-/opt/lobster-server}"
USER_NAME="${LOBSTER_SERVICE_USER:-ubuntu}"
PY="$ROOT/.venv/bin/python3"
[ -x "$PY" ] || { echo "[ERR] python not found: $PY" >&2; exit 1; }

sudo tee /etc/systemd/system/lobster-manage-checkup.service >/dev/null <<UNIT
[Unit]
Description=Lobster Manage daily project checkup (0900/1700)
After=network.target postgresql.service

[Service]
Type=oneshot
User=$USER_NAME
WorkingDirectory=$ROOT
Environment=PYTHONPATH=$ROOT
EnvironmentFile=$ROOT/.env
ExecStart=$PY -m backend.manage_checkup_cron
UNIT

sudo tee /etc/systemd/system/lobster-manage-checkup.timer >/dev/null <<UNIT
[Unit]
Description=Run Lobster Manage project checkup at 09:00 and 17:00

[Timer]
OnCalendar=*-*-* 09,17:00:00
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now lobster-manage-checkup.timer >/dev/null
systemctl list-timers lobster-manage-checkup.timer --no-pager | head -3
