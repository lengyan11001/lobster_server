#!/usr/bin/env bash
# 开发机一键：推送 lobster_server 当前分支 → SSH 远端 git pull → 重启 Backend+MCP
# 依赖：仓库已 git commit；本机已配置 .env.deploy（见 .env.deploy.example）
# ── 硬闸门：生产发布必须由用户明确说「发」，AI 会话不得绕过 ──
if [ "${LOBSTER_DEPLOY_CONFIRM:-}" != "发" ]; then
  echo "[BLOCKED] 未经用户授权：部署/发布已拦截。请让用户明确说「发」，然后：" >&2
  echo "  LOBSTER_DEPLOY_CONFIRM=发 bash $0 $*" >&2
  exit 3
fi
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# On Windows, Python parses .env.deploy without shell-evaluating passwords and
# converts Git Bash key paths such as /d/maczhuji before opening the key.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    exec python "$ROOT/scripts/deploy_from_local.py" --push
    ;;
esac

if [ -f "$ROOT/.env.deploy" ]; then
  set -a
  # shellcheck source=../.env.deploy
  . "$ROOT/.env.deploy"
  set +a
fi
# shellcheck source=_deploy_guard_production.sh
. "$ROOT/scripts/_deploy_guard_production.sh"
lobster_deploy_refuse_if_only_test_mode

echo "[deploy_server] git push origin main ..."
git push origin main
echo "[deploy_server] SSH 拉取并重启 ..."
exec bash "$ROOT/scripts/deploy_from_local.sh"
