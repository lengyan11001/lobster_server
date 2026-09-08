#!/usr/bin/env bash
# Install the ToDesk relay on its own hostname without touching the main site
# server block or any of the Lobster application services.
set -euo pipefail

DOMAIN="${REMOTE_SUPPORT_DOMAIN:-todesk.bhzn.top}"
UPSTREAM="${REMOTE_SUPPORT_UPSTREAM:-127.0.0.1:38080}"
CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
SITE="/etc/nginx/sites-available/lobster-remote-support"

sudo tee "$SITE" >/dev/null <<NGINX
server {
    listen 80;
    server_name ${DOMAIN};
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\$host\$request_uri; }
}
NGINX
sudo ln -sfn "$SITE" /etc/nginx/sites-enabled/lobster-remote-support
for MAIN_SITE in /etc/nginx/sites-enabled/lobster /etc/nginx/sites-available/lobster; do
    if [ -f "$MAIN_SITE" ]; then
        sudo sed -i '\|include /etc/nginx/snippets/lobster-remote-support.conf;|d' "$MAIN_SITE"
    fi
done
sudo nginx -t
sudo systemctl reload nginx

if [ ! -s "$CERT_DIR/fullchain.pem" ] || [ ! -s "$CERT_DIR/privkey.pem" ]; then
    if ! command -v certbot >/dev/null 2>&1; then
        echo "[ERR] certbot is required to issue ${DOMAIN}" >&2
        exit 1
    fi
    sudo certbot certonly --webroot -w /var/www/html --non-interactive --agree-tos --register-unsafely-without-email -d "$DOMAIN"
fi

sudo tee "$SITE" >/dev/null <<NGINX
server {
    listen 80;
    server_name ${DOMAIN};
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\$host\$request_uri; }
}

server {
    listen 443 ssl;
    server_name ${DOMAIN};
    ssl_certificate ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
    client_max_body_size 200M;

    location / {
        proxy_pass http://${UPSTREAM};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
    }
    location /ws {
        proxy_pass http://${UPSTREAM}/ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
    }
}
NGINX
sudo nginx -t
sudo systemctl reload nginx
echo "[OK] ToDesk relay is isolated at https://${DOMAIN} -> ${UPSTREAM}"
