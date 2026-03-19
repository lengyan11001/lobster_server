#!/usr/bin/env bash
# 在服务器上执行：拉取最新代码并重启 Backend + MCP
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "[更新] 拉取 origin main ..."
git fetch origin main
git pull origin main

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files --type=service | grep -q lobster-backend; then
  echo "[重启] systemctl restart lobster-backend lobster-mcp ..."
  sudo systemctl restart lobster-backend lobster-mcp
  sudo systemctl status lobster-backend lobster-mcp --no-pager || true
  echo "[完成] 服务已重启"
else
  echo "[提示] 未检测到 systemd 或 lobster-backend.service，请手动重启 Backend 与 MCP（如 ./scripts/server_start.sh 或重启对应进程）"
fi
