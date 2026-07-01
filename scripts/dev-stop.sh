#!/usr/bin/env bash
# dev-stop.sh — stop application processes and any on-demand local parser services.
#
# Usage:
#   ./scripts/dev-stop.sh        # stop API/Web, keep Docker services
#   ./scripts/dev-stop.sh --all  # stop API/Web and Docker services

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

STOP_DOCKER=0
for arg in "$@"; do
  case "$arg" in
    --all) STOP_DOCKER=1 ;;
    -h|--help)
      sed -n '2,7p' "${BASH_SOURCE[0]}" | sed 's/^# //; s/^#$//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg"
      exit 1
      ;;
  esac
done

LOG_DIR="$REPO_ROOT/logs"

is_windows() {
  case "$(uname -s 2>/dev/null)" in
    CYGWIN*|MINGW*|MSYS*) return 0 ;;
    *) return 1 ;;
  esac
}

stop_by_pid_file() {
  local label="$1"
  local pid_file="$LOG_DIR/${label}.pid"
  local pid

  if [ ! -f "$pid_file" ]; then
    echo "   ($label 未找到 PID 文件，跳过 PID 停止)"
    return
  fi

  pid="$(tr -d '[:space:]' < "$pid_file")"
  rm -f "$pid_file"

  if [ -z "$pid" ]; then
    echo "   ($label PID 文件为空)"
    return
  fi

  echo "   停止 $label PID=$pid ..."
  if is_windows; then
    taskkill //F //T //PID "$pid" 2>/dev/null || true
  else
    pkill -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
  fi
}

echo "==> 停止 API / Web / 本地解析服务进程..."
stop_by_pid_file "R1plus-API"
stop_by_pid_file "R1plus-Web"
stop_by_pid_file "MinerU-Service"
stop_by_pid_file "PaddleOCR-API"
stop_by_pid_file "PaddleOCR-VLM"

if [ "$STOP_DOCKER" = "1" ]; then
  echo "==> 停止 Docker 基础设施..."
  docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env down
fi

echo "==> 完成"
