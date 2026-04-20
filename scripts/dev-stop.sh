#!/usr/bin/env bash
# dev-stop.sh — 停止 dev-start.sh 启动的 API / Web 进程
#
# 用法：
#   ./scripts/dev-stop.sh             # 停止 API 和 Web（保留 Docker）
#   ./scripts/dev-stop.sh --all       # 上面 + 停止 Docker 基础设施

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

STOP_DOCKER=0
for arg in "$@"; do
  case "$arg" in
    --all) STOP_DOCKER=1 ;;
  esac
done

is_windows() {
  case "$(uname -s 2>/dev/null)" in
    CYGWIN*|MINGW*|MSYS*) return 0 ;;
    *) return 1 ;;
  esac
}

echo "==> 停止 API (PowerShell 窗口 R1plus-API) ..."
if is_windows; then
  # 按窗口标题杀掉（PowerShell 窗口内部 $Host.UI.RawUI.WindowTitle 设置过）
  # 加 /T 杀整个进程树（PowerShell → conda → python uvicorn）
  taskkill //F //T //FI "WINDOWTITLE eq R1plus-API*" 2>/dev/null || true
else
  if [ -f "$REPO_ROOT/logs/R1plus-API.pid" ]; then
    kill "$(cat "$REPO_ROOT/logs/R1plus-API.pid")" 2>/dev/null || true
    rm -f "$REPO_ROOT/logs/R1plus-API.pid"
  fi
fi

echo "==> 停止 Web (PowerShell 窗口 R1plus-Web) ..."
if is_windows; then
  taskkill //F //T //FI "WINDOWTITLE eq R1plus-Web*" 2>/dev/null || true
else
  if [ -f "$REPO_ROOT/logs/R1plus-Web.pid" ]; then
    kill "$(cat "$REPO_ROOT/logs/R1plus-Web.pid")" 2>/dev/null || true
    rm -f "$REPO_ROOT/logs/R1plus-Web.pid"
  fi
fi

if [ "$STOP_DOCKER" = "1" ]; then
  echo "==> 停止 Docker 基础设施 ..."
  docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env down
fi

echo "==> 完成"
