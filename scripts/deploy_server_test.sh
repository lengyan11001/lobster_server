#!/usr/bin/env bash
# 一键：推送 main → 仅 SSH 测试机 deploy_from_local_test.sh（不碰生产 / 海外）
# 依赖：.env.deploy 中的 LOBSTER_DEPLOY_HOST_TEST（见 ../.env.deploy.example）
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "[deploy_server_test] git push origin main ..."
git push origin main
echo "[deploy_server_test] 仅部署测试环境 ..."
exec bash "$ROOT/scripts/deploy_from_local_test.sh"
