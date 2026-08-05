#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
MASTRA_ROOT="$ROOT/mastra_server"
RUNTIME_ROOT="$ROOT/.runtime"
NODE="$RUNTIME_ROOT/node/bin/node"
NPM="$RUNTIME_ROOT/node/bin/npm"
OUTPUT="$MASTRA_ROOT/.mastra/output/index.mjs"
FINGERPRINT_FILE="$RUNTIME_ROOT/mastra-build.sha256"

_truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

_mastra_source_fingerprint() {
  {
    find "$MASTRA_ROOT" -type f \
      ! -path "$MASTRA_ROOT/node_modules/*" \
      ! -path "$MASTRA_ROOT/.mastra/*" \
      -print0 \
      | sort -z \
      | while IFS= read -r -d '' file; do
          printf '%s  %s\n' "$(sha256sum "$file" | awk '{print $1}')" "${file#"$ROOT/"}"
        done
    for build_input in \
      "$ROOT/scripts/install_mastra_runtime.sh" \
      "$ROOT/scripts/build_mastra_if_needed.sh"; do
      printf '%s  %s\n' "$(sha256sum "$build_input" | awk '{print $1}')" "${build_input#"$ROOT/"}"
    done
  } | sha256sum | awk '{print $1}'
}

mkdir -p "$RUNTIME_ROOT"
CURRENT_FINGERPRINT="$(_mastra_source_fingerprint)"
CACHED_FINGERPRINT="$(cat "$FINGERPRINT_FILE" 2>/dev/null || true)"

if ! _truthy "${LOBSTER_FORCE_MASTRA_BUILD:-}" \
  && [ "$CURRENT_FINGERPRINT" = "$CACHED_FINGERPRINT" ] \
  && [ -x "$NODE" ] \
  && [ -x "$NPM" ] \
  && [ -s "$OUTPUT" ]; then
  echo "[Mastra] 源码与构建依赖未变化，复用现有构建产物。"
  exit 0
fi

if _truthy "${LOBSTER_FORCE_MASTRA_BUILD:-}"; then
  echo "[Mastra] LOBSTER_FORCE_MASTRA_BUILD 已启用，执行完整重建。"
elif [ -z "$CACHED_FINGERPRINT" ]; then
  echo "[Mastra] 尚无有效构建指纹，执行完整构建。"
elif [ "$CURRENT_FINGERPRINT" != "$CACHED_FINGERPRINT" ]; then
  echo "[Mastra] 源码或构建依赖已变化，执行完整重建。"
else
  echo "[Mastra] 构建产物或 Node 运行时不完整，执行修复重建。"
fi

"$ROOT/scripts/install_mastra_runtime.sh" "$ROOT"
export PATH="$RUNTIME_ROOT/node/bin:$PATH"
"$NPM" --prefix "$MASTRA_ROOT" ci
"$NPM" --prefix "$MASTRA_ROOT" run typecheck
"$NPM" --prefix "$MASTRA_ROOT" run build

if [ ! -s "$OUTPUT" ]; then
  echo "[ERR] Mastra 构建结束但产物不存在: $OUTPUT" >&2
  exit 1
fi

TMP_FINGERPRINT="$FINGERPRINT_FILE.tmp.$$"
printf '%s\n' "$CURRENT_FINGERPRINT" > "$TMP_FINGERPRINT"
mv -f "$TMP_FINGERPRINT" "$FINGERPRINT_FILE"
echo "[Mastra] 构建完成，已记录源码指纹 ${CURRENT_FINGERPRINT:0:12}。"
