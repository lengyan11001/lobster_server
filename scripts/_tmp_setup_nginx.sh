#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ -f "$ROOT/.env.deploy" ]; then
  set -a; . "$ROOT/.env.deploy"; set +a
fi
: "${LOBSTER_DEPLOY_HOST:?missing}"

SSH_BASE="-o StrictHostKeyChecking=accept-new"
_ssh_agent_has_keys() { [ -n "${SSH_AUTH_SOCK:-}" ] && ssh-add -l >/dev/null 2>&1; }
if ! _ssh_agent_has_keys; then
  if [ -n "${LOBSTER_DEPLOY_SSH_KEY:-}" ] && [ -r "$LOBSTER_DEPLOY_SSH_KEY" ] && [ -n "${LOBSTER_SSH_KEY_PASSPHRASE:-}" ]; then
    AP="$(mktemp)"; { echo '#!/usr/bin/env sh'; echo 'printf %s\\n "$LOBSTER_SSH_KEY_PASSPHRASE"'; } > "$AP"; chmod +x "$AP"
    trap 'rm -f "$AP"' EXIT
    eval "$(ssh-agent -s)"
    export SSH_ASKPASS_REQUIRE=force SSH_ASKPASS="$AP" DISPLAY="${DISPLAY:-localhost:0}"
    ssh-add "$LOBSTER_DEPLOY_SSH_KEY"
    rm -f "$AP"
  fi
fi

SSH_OPTS="$SSH_BASE"
if ! _ssh_agent_has_keys; then
  [ -n "${LOBSTER_DEPLOY_SSH_KEY:-}" ] && [ -r "$LOBSTER_DEPLOY_SSH_KEY" ] && SSH_OPTS="-i $LOBSTER_DEPLOY_SSH_KEY $SSH_BASE"
fi

ssh $SSH_OPTS "$LOBSTER_DEPLOY_HOST" 'bash -s' << 'REMOTE_SCRIPT'
cat > /tmp/nginx_lobster << 'NGINXCONF'
server {
    listen 80;
    server_name bhzn.top;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name bhzn.top;

    ssl_certificate /etc/letsencrypt/live/bhzn.top/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bhzn.top/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
NGINXCONF

sudo cp /tmp/nginx_lobster /etc/nginx/sites-available/lobster
sudo ln -sf /etc/nginx/sites-available/lobster /etc/nginx/sites-enabled/lobster
sudo nginx -t && sudo systemctl reload nginx
echo "Nginx reloaded successfully"
REMOTE_SCRIPT
