#!/usr/bin/env bash
# 必火听记录（note.bhzn.top）上线脚本 —— 只在用户明确说「发」后执行
set -euo pipefail

ROOT=/opt/lobster-server
cd "$ROOT"

install -m 644 deploy/lobster-note.service /etc/systemd/system/lobster-note.service
install -m 644 deploy/nginx-note.bhzn.top.conf /etc/nginx/sites-available/note.bhzn.top
ln -sfn /etc/nginx/sites-available/note.bhzn.top /etc/nginx/sites-enabled/note.bhzn.top

# 证书扩签（如果 note.bhzn.top 还不在证书 SAN 里）
if ! openssl x509 -in /etc/letsencrypt/live/bhzn.top/fullchain.pem -noout -text | grep -q 'DNS:note.bhzn.top'; then
  certbot --nginx -d bhzn.top -d www.bhzn.top -d h5.bhzn.top -d note.bhzn.top --non-interactive --agree-tos --redirect || true
fi

nginx -t
systemctl daemon-reload
systemctl enable --now lobster-note.service
systemctl restart lobster-note.service
systemctl reload nginx
systemctl is-active lobster-note.service
curl -sS -o /dev/null -w 'note_local=%{http_code}\n' http://127.0.0.1:8030/healthz
curl -sS -o /dev/null -w 'note_public=%{http_code}\n' https://note.bhzn.top/healthz
