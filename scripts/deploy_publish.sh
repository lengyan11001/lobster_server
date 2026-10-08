#!/usr/bin/env bash
# 开发机：有未提交改动则提交 → push main → SSH 服务器 pull 并重启
# 需：git 已配置 remote；本机 lobster-server/.env.deploy（见 .env.deploy.example）
# ── 硬闸门：生产发布必须由用户明确说「发」，AI 会话不得绕过 ──
if [ "${LOBSTER_DEPLOY_CONFIRM:-}" != "发" ]; then
  echo "[BLOCKED] 未经用户授权：部署/发布已拦截。请让用户明确说「发」，然后：" >&2
  echo "  LOBSTER_DEPLOY_CONFIRM=发 bash $0 $*" >&2
  exit 3
fi
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ -f "$ROOT/.env.deploy" ]; then
  set -a
  # shellcheck source=../.env.deploy
  . "$ROOT/.env.deploy"
  set +a
fi
# shellcheck source=_deploy_guard_production.sh
. "$ROOT/scripts/_deploy_guard_production.sh"
lobster_deploy_refuse_if_only_test_mode

if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
  git add -A
  git commit -m "chore: deploy $(date +%Y%m%d-%H%M%S)"
fi

exec bash "$ROOT/scripts/deploy_server.sh"
