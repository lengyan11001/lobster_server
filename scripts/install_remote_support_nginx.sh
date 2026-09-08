#!/usr/bin/env bash
# Install only the /remote reverse proxy on the primary Lobster host.
set -euo pipefail

cat >/tmp/lobster-remote-support-nginx.conf <<'NGINX'
location = /remote {
    return 302 /remote/;
}

location /ws {
    proxy_pass http://127.0.0.1:38080/ws;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_buffering off;
}

location /remote/ {
    proxy_pass http://127.0.0.1:38080/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_buffering off;
}
NGINX

sudo install -m 0644 /tmp/lobster-remote-support-nginx.conf /etc/nginx/snippets/lobster-remote-support.conf
if ! sudo grep -q 'include /etc/nginx/snippets/lobster-remote-support.conf;' /etc/nginx/sites-available/lobster; then
  sudo sed -i '/server_name bhzn.top www.bhzn.top;/a\    include /etc/nginx/snippets/lobster-remote-support.conf;' /etc/nginx/sites-available/lobster
fi
sudo nginx -t
sudo systemctl reload nginx
