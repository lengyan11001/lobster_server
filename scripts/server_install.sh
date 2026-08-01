#!/usr/bin/env bash
# 云服务器首次：安装 Python 依赖（创建 venv + pip install）
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -f "requirements.txt" ]; then
  echo "[ERR] 请在 lobster 项目根目录执行，或确保 requirements.txt 存在"
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[1/2] 创建虚拟环境 .venv ..."
  python3 -m venv .venv
fi
echo "[2/2] 安装依赖 pip install -r requirements.txt ..."
"$ROOT/.venv/bin/pip" install -r requirements.txt

echo "[Mastra] 安装独立 Node 运行时并构建调度服务 ..."
"$ROOT/scripts/install_mastra_runtime.sh" "$ROOT"
export PATH="$ROOT/.runtime/node/bin:$PATH"
"$ROOT/.runtime/node/bin/npm" --prefix "$ROOT/mastra_server" ci
"$ROOT/.runtime/node/bin/npm" --prefix "$ROOT/mastra_server" run build

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "已复制 .env.example → .env，请编辑 .env 填写 SECRET_KEY、SUTUI_SERVER_TOKEN、微信/支付等后执行："
  echo "  ./scripts/server_start.sh"
  echo "（若 lobster_server 仓库带了一键配置脚本，可运行: python3 scripts/config_env.py）"
  exit 0
fi

echo "安装完成。启动: ./scripts/server_start.sh"
