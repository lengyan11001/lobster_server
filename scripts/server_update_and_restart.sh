#!/usr/bin/env bash
# 在服务器上执行：拉取最新代码并重启 Backend + MCP + H5（若已安装）
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PREV_COMMIT="$(git rev-parse HEAD)"
echo "$PREV_COMMIT" > "$ROOT/.deploy_rollback_commit"
echo "[备份] 当前版本 $PREV_COMMIT 已记录到 .deploy_rollback_commit"

echo "[更新] 拉取 origin main ..."
git fetch origin main
git pull origin main

NEW_COMMIT="$(git rev-parse HEAD)"
echo "[版本] $PREV_COMMIT → $NEW_COMMIT"

if [ -x "$ROOT/.venv/bin/pip" ]; then
  echo "[依赖] .venv pip install -r requirements.txt ..."
  "$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt"
else
  echo "[ERR] 未找到 $ROOT/.venv/bin/pip，请先在本机创建虚拟环境并安装依赖后再部署。"
  exit 1
fi

echo "[日志] 清理 3 天前应用日志、诊断上传与 systemd journal"
mkdir -p "$ROOT/logs"
find "$ROOT/logs" -type f -name "*.log" -mtime +2 -delete 2>/dev/null || true
find "$ROOT" -maxdepth 1 -type f \( -name "mcp.log" -o -name "backend.log" -o -name "background.log" -o -name "h5.log" \) -mtime +2 -delete 2>/dev/null || true
find "$ROOT/diagnostics_uploads" -mindepth 2 -maxdepth 2 -type d -mtime +2 -exec rm -rf {} + 2>/dev/null || true
if command -v journalctl >/dev/null 2>&1; then
  sudo journalctl --vacuum-time=3d >/dev/null 2>&1 || true
fi

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files --type=service 2>/dev/null | grep -q lobster-backend; then
  echo "[重启] systemctl stop + 端口清理 + start ..."
  H5_UNIT=""
  if systemctl list-unit-files --type=service 2>/dev/null | grep -q '^lobster-h5\.service'; then
    H5_UNIT="lobster-h5"
  fi
  BG_UNIT=""
  if systemctl list-unit-files --type=service 2>/dev/null | grep -q '^lobster-background\.service'; then
    BG_UNIT="lobster-background"
  fi
  sudo systemctl stop $H5_UNIT $BG_UNIT lobster-mcp lobster-backend 2>/dev/null || true
  sleep 1
  # 确保 8001/8000 端口无残留进程
  for PORT in 8001 8000 8010; do
    PID_ON_PORT="$(sudo fuser "$PORT/tcp" 2>/dev/null | tr -d '[:space:]')" || true
    if [ -n "$PID_ON_PORT" ]; then
      echo "[清理] 端口 $PORT 仍被进程 $PID_ON_PORT 占用，强制结束"
      sudo fuser -k "$PORT/tcp" 2>/dev/null || true
      sleep 1
    fi
  done
  sudo systemctl start lobster-backend lobster-mcp
  if [ -n "$BG_UNIT" ]; then
    sudo systemctl start "$BG_UNIT"
  fi
  if [ -n "$H5_UNIT" ]; then
    sudo systemctl start "$H5_UNIT"
  fi
  sleep 2
  # 验证 MCP 是否成功监听
  MCP_OK=0
  for i in 1 2 3; do
    if sudo fuser 8001/tcp >/dev/null 2>&1; then
      MCP_OK=1; break
    fi
    echo "[等待] MCP 端口 8001 尚未就绪，等待 ${i}s..."
    sleep "$i"
  done
  if [ "$MCP_OK" = 0 ]; then
    echo "[WARN] MCP 可能未成功启动，检查 mcp.log"
  fi
  sudo systemctl status lobster-backend lobster-mcp $BG_UNIT $H5_UNIT --no-pager || true
  echo "[完成] 服务已重启"
else
  echo "[重启] 无 systemd，结束旧进程并后台启动 MCP + Backend ..."
  export PYTHONPATH="$ROOT"
  [ -f .env ] && set -a && . ./.env && set +a
  PY="$ROOT/.venv/bin/python"
  pkill -f "backend.run" 2>/dev/null || true
  pkill -f "backend.background_worker" 2>/dev/null || true
  pkill -f "backend.h5_run" 2>/dev/null || true
  pkill -f "mcp --port 8001" 2>/dev/null || true
  pkill -f "python -m mcp" 2>/dev/null || true
  sleep 2
  TODAY="$(date +%F)"
  mkdir -p "$ROOT/logs"
  nohup "$PY" -m mcp --port "${MCP_PORT:-8001}" >> "$ROOT/logs/mcp-$TODAY.log" 2>&1 &
  sleep 1
  nohup "$PY" -m backend.background_worker >> "$ROOT/logs/background-stdout-$TODAY.log" 2>&1 &
  nohup "$PY" -m backend.run >> "$ROOT/logs/backend-stdout-$TODAY.log" 2>&1 &
  nohup "$PY" -m backend.h5_run >> "$ROOT/logs/h5-stdout-$TODAY.log" 2>&1 &
  sleep 2
  echo "[完成] MCP、Backend、Background 与 H5 已后台启动，日志: logs/app-YYYY-MM-DD.log / logs/background-YYYY-MM-DD.log / logs/h5-YYYY-MM-DD.log"
fi
