#!/usr/bin/env bash
# dev-start.sh — one-command local startup for the compressor knowledge platform.
#
# Usage:
#   ./scripts/dev-start.sh              # infra + env + deps + migrations + seed + API + Web
#   ./scripts/dev-start.sh --no-web     # skip frontend
#   ./scripts/dev-start.sh --no-api     # skip backend process
#   ./scripts/dev-start.sh --infra-only # only infra + env + deps + migrations + seed
#   ./scripts/dev-start.sh --skip-install
#   ./scripts/dev-start.sh --with-mineru # 兼容旧命令；MinerU 现已默认可用
#   ./scripts/dev-start.sh --with-mineru-service # 兼容旧命令；本地部署服务现按解析任务自动启动
#
# Stop:
#   ./scripts/dev-stop.sh        # stop API/Web, keep Docker services
#   ./scripts/dev-stop.sh --all  # stop API/Web and Docker services

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

START_API=1
START_WEB=1
INFRA_ONLY=0
SKIP_INSTALL=0

for arg in "$@"; do
  case "$arg" in
    --no-api) START_API=0 ;;
    --no-web) START_WEB=0 ;;
    --infra-only) INFRA_ONLY=1; START_API=0; START_WEB=0 ;;
    --skip-install) SKIP_INSTALL=1 ;;
    --with-mineru) ;; # 兼容早期操作说明。
    --with-mineru-service) ;; # 兼容早期操作说明；服务现由解析任务按需启动。
    -h|--help)
      sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# //; s/^#$//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg"
      exit 1
      ;;
  esac
done

DOCKER_ENV="$REPO_ROOT/infra/docker/.env"
DOCKER_ENV_EXAMPLE="$REPO_ROOT/infra/docker/.env.example"
API_ENV="$REPO_ROOT/apps/api/.env"
WEB_ENV="$REPO_ROOT/apps/web/.env.local"
LOG_DIR="$REPO_ROOT/logs"
PYTHON_VERSION="3.11"
WEB_PORT=""
PLATFORM_PYTHON="$REPO_ROOT/.venv/bin/python"
MINERU_MODEL_FILE="$REPO_ROOT/models/MinerU2.5-Pro-2604-1.2B/model.safetensors"
MINERU_SERVICE_BIN="$REPO_ROOT/.venv-mineru-service/bin/mineru-api"
MINERU_CONFIG_FILE="$REPO_ROOT/logs/mineru-local-service/mineru.json"
PADDLEOCR_MODEL_FILE="$REPO_ROOT/models/PaddleOCR-VL-1.5-0.9B/model.safetensors"
PADDLEOCR_SERVICE_BIN="$REPO_ROOT/.venv-paddleocr-service/bin/paddlex"
PADDLEOCR_VLM_PYTHON="$REPO_ROOT/.venv-paddleocr-mlx-service/bin/python"
PADDLEOCR_LAYOUT_FILES=(
  "$REPO_ROOT/models/PP-DocLayoutV3/inference.json"
  "$REPO_ROOT/models/PP-DocLayoutV3/inference.pdiparams"
  "$REPO_ROOT/models/PP-DocLayoutV3/inference.yml"
)
PADDLEOCR_MLX_MODEL_FILE="$REPO_ROOT/logs/paddleocr-local-service/models/PaddleOCR-VL-1.5-MLX/model.safetensors"
PADDLEOCR_CONFIG_FILE="$REPO_ROOT/logs/paddleocr-local-service/config/PaddleOCR-VL-1.5.yaml"
mkdir -p "$LOG_DIR"

require_cmd() {
  local cmd="$1"
  local hint="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing command: $cmd"
    echo "Install it first: $hint"
    exit 1
  fi
}

read_env_value() {
  local key="$1"
  local fallback="$2"
  local value
  value="$(grep -E "^${key}=" "$DOCKER_ENV" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  if [ -z "$value" ]; then
    value="$fallback"
  fi
  printf '%s' "$value"
}

port_in_use() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:$port" >/dev/null 2>&1
    return $?
  fi
  if command -v nc >/dev/null 2>&1; then
    nc -z localhost "$port" >/dev/null 2>&1
    return $?
  fi
  return 1
}

pick_web_port() {
  local preferred="${WEB_PORT:-3000}"
  local port
  for port in "$preferred" 3000 3001 3002 3003 3004 3005; do
    if ! port_in_use "$port"; then
      printf '%s' "$port"
      return 0
    fi
  done
  echo "No free frontend port found in 3000-3005." >&2
  exit 1
}

create_env_files() {
  echo "==> [1/7] 准备本地环境变量..."

  if [ ! -f "$DOCKER_ENV" ]; then
    cp "$DOCKER_ENV_EXAMPLE" "$DOCKER_ENV"
    echo "   ✓ 已创建 infra/docker/.env"
  else
    echo "   ✓ infra/docker/.env 已存在"
  fi

  local pg_port redis_port minio_port minio_console_port pg_db pg_user pg_password
  local minio_access_key minio_secret_key bucket_documents bucket_outputs
  pg_port="$(read_env_value POSTGRES_PORT 5432)"
  redis_port="$(read_env_value REDIS_PORT 6379)"
  minio_port="$(read_env_value MINIO_HOST_PORT 9000)"
  minio_console_port="$(read_env_value MINIO_CONSOLE_PORT 9001)"
  pg_db="$(read_env_value POSTGRES_DB datasetgen)"
  pg_user="$(read_env_value POSTGRES_USER datasetgen)"
  pg_password="$(read_env_value POSTGRES_PASSWORD datasetgen_dev_password)"
  minio_access_key="$(read_env_value MINIO_ACCESS_KEY minioadmin)"
  minio_secret_key="$(read_env_value MINIO_SECRET_KEY minioadmin123)"
  bucket_documents="$(read_env_value MINIO_BUCKET_DOCUMENTS documents)"
  bucket_outputs="$(read_env_value MINIO_BUCKET_OUTPUTS outputs)"

  if [ ! -f "$API_ENV" ]; then
    cat > "$API_ENV" <<EOF
POSTGRES_HOST=localhost
POSTGRES_PORT=$pg_port
POSTGRES_DB=$pg_db
POSTGRES_USER=$pg_user
POSTGRES_PASSWORD=$pg_password

REDIS_HOST=localhost
REDIS_PORT=$redis_port

MINIO_ENDPOINT=localhost:$minio_port
MINIO_ACCESS_KEY=$minio_access_key
MINIO_SECRET_KEY=$minio_secret_key
MINIO_BUCKET_DOCUMENTS=$bucket_documents
MINIO_BUCKET_OUTPUTS=$bucket_outputs
MINIO_SECURE=false

JWT_SECRET_KEY=dev-secret-key-change-in-production
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
MINERU_API_TOKEN=
PADDLEOCR_API_TOKEN=
API_HOST=0.0.0.0
API_PORT=8000
EOF
    echo "   ✓ 已创建 apps/api/.env（供本机 FastAPI 连接 Docker 服务）"
  else
    echo "   ✓ apps/api/.env 已存在"
  fi

  if [ ! -f "$WEB_ENV" ]; then
    cat > "$WEB_ENV" <<EOF
NEXT_PUBLIC_API_URL=http://localhost:8000/api
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
EOF
    echo "   ✓ 已创建 apps/web/.env.local"
  else
    echo "   ✓ apps/web/.env.local 已存在"
  fi
}

wait_for_container_health() {
  local service="$1"
  local label="$2"
  local cid health

  echo "   等待 $label 就绪..."
  for _ in $(seq 1 45); do
    cid="$(docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env ps -q "$service" 2>/dev/null || true)"
    if [ -n "$cid" ]; then
      health="$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || true)"
      if [ "$health" = "healthy" ] || [ "$health" = "running" ]; then
        echo "   ✓ $label ready"
        return 0
      fi
    fi
    sleep 1
  done

  echo "   ⚠ $label 45 秒内未确认 ready，继续执行（后续步骤若失败请先检查 Docker）"
}

frontend_dependencies_need_install() {
  local web_dir="$REPO_ROOT/apps/web"
  local node_modules="$web_dir/node_modules"
  local installed_lock="$node_modules/.package-lock.json"

  if [ ! -d "$node_modules" ]; then
    return 0
  fi

  if [ ! -f "$installed_lock" ]; then
    return 0
  fi

  if [ "$web_dir/package.json" -nt "$installed_lock" ]; then
    return 0
  fi

  if [ "$web_dir/package-lock.json" -nt "$installed_lock" ]; then
    return 0
  fi

  if ! (cd "$web_dir" && npm ls --depth=0 >/dev/null 2>&1); then
    return 0
  fi

  return 1
}

install_dependencies() {
  echo "==> [3/7] 检查并安装依赖..."

  if [ "$SKIP_INSTALL" = "1" ]; then
    echo "   跳过依赖安装 (--skip-install)"
    return
  fi

  require_cmd uv "https://docs.astral.sh/uv/getting-started/installation/"
  require_cmd npm "Install Node.js 20/22 LTS, which includes npm."

  if [ -f "$REPO_ROOT/.venv/pyvenv.cfg" ]; then
    local venv_version
    venv_version="$(grep -E '^version_info = ' "$REPO_ROOT/.venv/pyvenv.cfg" | sed 's/^version_info = //' || true)"
    if [ -n "$venv_version" ] && [[ "$venv_version" != 3.11* ]]; then
      echo "   当前 .venv 使用 Python $venv_version，但项目要求 Python $PYTHON_VERSION。"
      echo "   请执行：rm -rf .venv"
      echo "   然后重新运行：./scripts/dev-start.sh"
      exit 1
    fi
  fi

  echo "   同步后端依赖（含 MinerU 本地推理运行库；模型仅在解析任务中加载）..."
  (cd "$REPO_ROOT/apps/api" && uv sync --python "$PYTHON_VERSION" --extra dev)

  if frontend_dependencies_need_install; then
    echo "   同步前端 Node 依赖 (npm ci) ..."
    (cd "$REPO_ROOT/apps/web" && npm ci)
  else
    echo "   ✓ 前端 Node 依赖已是最新"
  fi

  ensure_local_parser_services
}

all_files_exist() {
  local file
  for file in "$@"; do
    if [ ! -f "$file" ]; then
      return 1
    fi
  done
  return 0
}

ensure_platform_python() {
  if [ ! -x "$PLATFORM_PYTHON" ]; then
    echo "   ⚠ 未找到平台 Python：$PLATFORM_PYTHON"
    echo "     请先完成平台依赖安装，或取消 --skip-install 后重新运行 dev-start。"
    return 1
  fi
  return 0
}

ensure_mineru_local_service_environment() {
  echo "   检查 MinerU 本地部署服务环境..."
  if [ ! -f "$MINERU_MODEL_FILE" ]; then
    echo "   ⚠ 缺少 MinerU 本地模型：$MINERU_MODEL_FILE"
    echo "     此大模型需要预先放入 models/MinerU2.5-Pro-2604-1.2B；跳过 MinerU 服务环境自动安装。"
    return
  fi

  if [ ! -x "$MINERU_SERVICE_BIN" ]; then
    echo "   安装 MinerU 独立服务环境（首次可能较慢）..."
    "$PLATFORM_PYTHON" scripts/mineru_local_service.py setup
  else
    echo "   ✓ MinerU 独立服务环境已存在"
  fi

  echo "   确认 MinerU 本地服务配置..."
  "$PLATFORM_PYTHON" scripts/mineru_local_service.py config
}

ensure_paddleocr_local_service_environment() {
  echo "   检查 PaddleOCR-VL 本地部署服务环境..."
  if [ ! -f "$PADDLEOCR_MODEL_FILE" ]; then
    echo "   ⚠ 缺少 PaddleOCR-VL 原始模型：$PADDLEOCR_MODEL_FILE"
    echo "     此大模型需要预先放入 models/PaddleOCR-VL-1.5-0.9B；跳过 PaddleOCR 服务环境自动安装。"
    return
  fi

  if [ ! -x "$PADDLEOCR_SERVICE_BIN" ] || [ ! -x "$PADDLEOCR_VLM_PYTHON" ]; then
    echo "   安装 PaddleOCR / MLX-VLM 独立服务环境（首次可能较慢）..."
    "$PLATFORM_PYTHON" scripts/paddleocr_local_service.py setup
  else
    echo "   ✓ PaddleOCR / MLX-VLM 独立服务环境已存在"
  fi

  if ! all_files_exist "${PADDLEOCR_LAYOUT_FILES[@]}"; then
    echo "   下载 PP-DocLayoutV3 版面检测模型..."
    "$PLATFORM_PYTHON" scripts/paddleocr_local_service.py download-layout
  else
    echo "   ✓ PP-DocLayoutV3 本地模型已存在"
  fi

  if [ ! -f "$PADDLEOCR_MLX_MODEL_FILE" ]; then
    echo "   转换 PaddleOCR-VL 为 MLX 服务权重（首次可能较慢）..."
    "$PLATFORM_PYTHON" scripts/paddleocr_local_service.py convert
  else
    echo "   ✓ PaddleOCR-VL MLX 转换产物已存在"
  fi

  echo "   确认 PaddleOCR 本地 API 配置..."
  "$PLATFORM_PYTHON" scripts/paddleocr_local_service.py config
}

ensure_local_parser_services() {
  echo "   检查本地解析服务环境（按需启动前置条件）..."
  if ! ensure_platform_python; then
    return
  fi
  ensure_mineru_local_service_environment
  ensure_paddleocr_local_service_environment
}

run_database_setup() {
  local uv_run_args=(run --python "$PYTHON_VERSION")
  echo "==> [4/7] 应用数据库迁移..."
  (cd "$REPO_ROOT/apps/api" && uv "${uv_run_args[@]}" alembic upgrade head)
  echo "   ✓ 数据库表结构已更新到最新版本"

  echo "==> [5/7] 初始化种子数据..."
  (cd "$REPO_ROOT/apps/api" && uv "${uv_run_args[@]}" python ../../scripts/init_seed.py)
  echo "   ✓ 默认管理员、项目和基础配置已确认"
}

is_windows() {
  case "$(uname -s 2>/dev/null)" in
    CYGWIN*|MINGW*|MSYS*) return 0 ;;
    *) return 1 ;;
  esac
}

start_process() {
  local title="$1"
  local workdir="$2"
  local command="$3"
  local pid_file="$LOG_DIR/${title}.pid"
  local log_file="$LOG_DIR/${title}.log"

  if is_windows; then
    local workdir_win
    workdir_win="$(cygpath -w "$workdir" 2>/dev/null || echo "$workdir")"
    local full_ps="\$Host.UI.RawUI.WindowTitle='$title'; Set-Location -LiteralPath '$workdir_win'; $command"
    cmd //c start "" powershell -NoExit -Command "$full_ps"
    echo "   ✓ $title 已在新 PowerShell 窗口启动"
  else
    (cd "$workdir" && exec sh -lc "$command") >"$log_file" 2>&1 &
    echo $! > "$pid_file"
    echo "   ✓ $title 已后台启动，PID=$(cat "$pid_file")，日志: $log_file"
  fi
}

echo "==> 仓库根目录: $REPO_ROOT"

create_env_files

echo "==> [2/7] 启动 Docker 基础设施..."
require_cmd docker "Install and start Docker Desktop."
if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop 未运行。请先启动 Docker Desktop，然后重试。"
  exit 1
fi
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up -d
wait_for_container_health postgres PostgreSQL
wait_for_container_health redis Redis
wait_for_container_health minio MinIO
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up minio-init >/dev/null 2>&1 || true
echo "   ✓ MinIO bucket 已确认"

install_dependencies
run_database_setup

if [ "$INFRA_ONLY" = "1" ]; then
  echo "==> 仅基础设施模式完成。"
  exit 0
fi

echo "==> [6/7] 启动应用进程..."
WEB_PORT="$(pick_web_port)"
if [ "$START_WEB" = "1" ] && [ "$WEB_PORT" != "3000" ]; then
  echo "   ⚠ localhost:3000 已被占用，前端将使用 localhost:$WEB_PORT"
fi

if [ "$START_API" = "1" ]; then
  start_process "R1plus-API" "$REPO_ROOT/apps/api" "uv run --python $PYTHON_VERSION uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
fi

if [ "$START_WEB" = "1" ]; then
  start_process "R1plus-Web" "$REPO_ROOT/apps/web" "npm run dev -- -p $WEB_PORT"
fi

echo "==> [7/7] 启动完成"
echo "==========================================="
echo "后端 API    : http://localhost:8000/api/health"
echo "API 文档    : http://localhost:8000/docs"
echo "前端 Web    : http://localhost:$WEB_PORT"
echo "MinIO 控制台: http://localhost:$(read_env_value MINIO_CONSOLE_PORT 9001)  ($(read_env_value MINIO_ACCESS_KEY minioadmin) / $(read_env_value MINIO_SECRET_KEY minioadmin123))"
echo "管理员登录  : admin / admin123"
echo "本地 MinerU  : 可用（仅在选择本地解析任务时加载模型）"
echo "MinerU 服务   : 按需启动（选择本地部署服务解析时，Mac 自动使用 MLX）"
echo "PaddleOCR 服务: 按需启动（选择本地部署服务解析时，使用 9020 API + 9021 MLX）"
echo ""
echo "停止应用进程（保留 Docker）：./scripts/dev-stop.sh"
echo "停止全部（含 Docker）      ：./scripts/dev-stop.sh --all"
echo "==========================================="
