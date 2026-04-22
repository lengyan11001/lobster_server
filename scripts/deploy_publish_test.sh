#!/usr/bin/env bash
# 测试环境一键发布：有未提交改动则提交 → push main → 仅 SSH 测试机拉取并重启
# 切勿与 deploy_publish.sh 混淆：后者会按 LOBSTER_DEPLOY_HOST 部署生产（+可选海外）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
  git add -A
  git commit -m "chore: deploy-test $(date +%Y%m%d-%H%M%S)"
fi

exec bash "$ROOT/scripts/deploy_server_test.sh"
