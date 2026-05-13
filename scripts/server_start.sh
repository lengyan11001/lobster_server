#!/usr/bin/env bash
# 云服务器：启动 MCP + Backend（拉取代码后快速启动）
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT"

# 若未安装则先安装
if [ ! -d ".venv" ] || [ ! -x ".venv/bin/python" ]; then
  echo "未检测到 .venv，先执行安装：./scripts/server_install.sh"
  exit 1
fi

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "已复制 .env.example → .env，请编辑 .env 填写 SECRET_KEY、SUTUI_SERVER_TOKEN 后重新运行本脚本"
  exit 1
fi

PY="$ROOT/.venv/bin/python"
PORT="${PORT:-8000}"
MCP_PORT="${MCP_PORT:-8001}"
START_BACKGROUND="${START_BACKGROUND:-1}"

find "$ROOT/logs" -type f -name "*.log" -mtime +2 -delete 2>/dev/null || true
find "$ROOT" -maxdepth 1 -type f \( -name "mcp.log" -o -name "backend.log" -o -name "background.log" -o -name "h5.log" \) -mtime +2 -delete 2>/dev/null || true
find "$ROOT/diagnostics_uploads" -mindepth 2 -maxdepth 2 -type d -mtime +2 -exec rm -rf {} + 2>/dev/null || true
TODAY="$(date +%F)"
mkdir -p "$ROOT/logs"

# 若 8001 已在监听则跳过，否则后台启动 MCP
start_mcp() {
  if "$PY" -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(1)
try:
  s.connect(('127.0.0.1', $MCP_PORT))
  s.close()
  exit(0)
except Exception:
  exit(1)
" 2>/dev/null; then
    echo "[MCP] 端口 $MCP_PORT 已在监听，跳过"
    return
  fi
  echo "[MCP] 启动 MCP 端口 $MCP_PORT ..."
  nohup "$PY" -m mcp --port "$MCP_PORT" >> "$ROOT/logs/mcp-$TODAY.log" 2>&1 &
  sleep 1
}

start_mcp

if [ "$START_BACKGROUND" != "0" ]; then
  echo "[Background] 启动单例后台任务进程 ..."
  nohup "$PY" -m backend.background_worker >> "$ROOT/logs/background-stdout-$TODAY.log" 2>&1 &
fi

echo "[Backend] 启动 Backend 端口 $PORT ..."
echo "访问: http://0.0.0.0:$PORT"
exec "$PY" -m backend.run
