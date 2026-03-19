#!/usr/bin/env bash
# 在本地开发机执行：推送后通过 SSH 在服务器上拉取并重启（需配置 LOBSTER_DEPLOY_HOST）
# 用法: LOBSTER_DEPLOY_HOST=user@服务器IP bash scripts/deploy_from_local.sh
# 或: export LOBSTER_DEPLOY_HOST=user@服务器IP 后直接 bash scripts/deploy_from_local.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -z "$LOBSTER_DEPLOY_HOST" ]; then
  echo "未设置 LOBSTER_DEPLOY_HOST，无法远程执行。"
  echo "请在服务器上执行: cd /opt/lobster-server && bash scripts/server_update_and_restart.sh"
  echo "或设置 LOBSTER_DEPLOY_HOST=user@服务器IP 后重新运行本脚本以从本机 SSH 完成拉取与重启。"
  exit 1
fi

REMOTE_DIR="${LOBSTER_DEPLOY_REMOTE_DIR:-/opt/lobster-server}"
echo "[部署] SSH $LOBSTER_DEPLOY_HOST → cd $REMOTE_DIR && bash scripts/server_update_and_restart.sh"
ssh "$LOBSTER_DEPLOY_HOST" "cd $REMOTE_DIR && bash scripts/server_update_and_restart.sh"
echo "[完成] 服务器已更新并重启"
