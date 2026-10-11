#!/usr/bin/env bash
# 安装 manage 飞书播报定时：每天 09:00 今日任务 / 17:00 逾期提醒。
set -euo pipefail
ROOT="${1:-/opt/lobster-server}"
USER_NAME="${LOBSTER_SERVICE_USER:-ubuntu}"
PY="$ROOT/.venv/bin/python3"
[ -x "$PY" ] || { echo "[ERR] python not found: $PY" >&2; exit 1; }

sudo tee /etc/systemd/system/lobster-manage-notify.service >/dev/null <<UNIT
[Unit]
Description=Lobster Manage feishu notify (0900 digest / 1700 overdue)
After=network.target postgresql.service

[Service]
Type=oneshot
User=$USER_NAME
WorkingDirectory=$ROOT
Environment=PYTHONPATH=$ROOT
EnvironmentFile=$ROOT/.env
ExecStart=$PY -m backend.manage_notify_cron
UNIT

sudo tee /etc/systemd/system/lobster-manage-notify.timer >/dev/null <<UNIT
[Unit]
Description=Run Lobster Manage feishu notify at 09:00 and 17:00

[Timer]
OnCalendar=*-*-* 09,17:05:00
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now lobster-manage-notify.timer >/dev/null
systemctl list-timers lobster-manage-notify.timer --no-pager | head -3