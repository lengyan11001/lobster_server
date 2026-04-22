#!/usr/bin/env bash
# 由 deploy_publish.sh / deploy_server.sh / deploy_from_local.sh **source** 加载（勿单独执行）。
# 当 lobster-server/.env.deploy 中 LOBSTER_DEPLOY_ONLY_TEST 或 LOBSTER_DEPLOY_TEST_MODE 为 true/1/yes 时，
# 禁止运行上述正式部署脚本，避免误发生产；测试脚本 deploy_*_test.sh 不受影响。

_is_lobster_deploy_only_test_enabled() {
  local v
  v=$(printf '%s' "${LOBSTER_DEPLOY_ONLY_TEST:-}" | tr '[:upper:]' '[:lower:]')
  case "$v" in 1|true|yes) return 0 ;; esac
  v=$(printf '%s' "${LOBSTER_DEPLOY_TEST_MODE:-}" | tr '[:upper:]' '[:lower:]')
  case "$v" in 1|true|yes) return 0 ;; esac
  return 1
}

lobster_deploy_refuse_if_only_test_mode() {
  if _is_lobster_deploy_only_test_enabled; then
    echo "[ERR] lobster-server/.env.deploy 已开启 LOBSTER_DEPLOY_ONLY_TEST 或 LOBSTER_DEPLOY_TEST_MODE（仅允许测试环境部署）。" >&2
    echo "[ERR] 已阻止执行正式脚本：deploy_publish.sh / deploy_server.sh / deploy_from_local.sh。" >&2
    echo "[ERR] 请改用：bash scripts/deploy_publish_test.sh（或 deploy_server_test.sh / deploy_from_local_test.sh）。" >&2
    exit 1
  fi
}
