#!/usr/bin/env bash
# dev-start.sh — 一键启动 R1 / R1+ 测试环境
#
# 用法：
#   ./scripts/dev-start.sh              # 默认启动全部（基础设施 + 迁移 + API + Web）
#   ./scripts/dev-start.sh --no-web     # 不启动前端
#   ./scripts/dev-start.sh --no-api     # 不启动后端
#   ./scripts/dev-start.sh --infra-only # 只启动基础设施 + 迁移
#
# 后端 / 前端会在新终端窗口打开（Windows 的 cmd start 命令）。
# 配套 stop 脚本：./scripts/dev-stop.sh
#
# 环境前提：
#   - Docker Desktop 已启动
#   - conda 环境 DatasetGen 已安装（apps/api 依赖）
#   - apps/web 依赖已 npm install

set -e

# 找到仓库根（脚本所在目录的上一级）
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

START_API=1
START_WEB=1
INFRA_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --no-api)     START_API=0 ;;
    --no-web)     START_WEB=0 ;;
    --infra-only) INFRA_ONLY=1; START_API=0; START_WEB=0 ;;
    -h|--help)
      sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# //; s/^#$//'
      exit 0 ;;
  esac
done

echo "==> 仓库根目录: $REPO_ROOT"

# ---------- Step 1: Docker 状态检查 ----------
echo ""
echo "==> [1/4] 检查 Docker Desktop 是否运行..."
if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker Desktop 未运行。请先启动 Docker Desktop，然后重试。"
  exit 1
fi
echo "   ✓ Docker 已运行"

# ---------- Step 2: 启动基础设施 ----------
echo ""
echo "==> [2/4] 启动 postgres / redis / minio ..."
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up -d

# 等待 postgres healthy（最多 30 秒）
echo "   等待 postgres 就绪..."
for i in $(seq 1 30); do
  HEALTH=$(docker inspect --format='{{.State.Health.Status}}' docker-postgres-1 2>/dev/null || echo "none")
  if [ "$HEALTH" = "healthy" ]; then
    echo "   ✓ postgres healthy"
    break
  fi
  sleep 1
  if [ "$i" = "30" ]; then
    echo "   ⚠ postgres 30 秒未 healthy，继续但可能迁移失败"
  fi
done

# 确保 MinIO 桶存在（幂等）
echo "   确认 MinIO 桶..."
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up minio-init >/dev/null 2>&1 || true
echo "   ✓ MinIO 桶就绪"

# ---------- Step 3: 应用 Alembic 迁移 ----------
echo ""
echo "==> [3/4] 应用数据库迁移 (alembic upgrade head) ..."
(
  cd apps/api
  # 优先使用 conda run 避免激活 shell；用户也可以提前 conda activate
  if command -v conda >/dev/null 2>&1; then
    conda run -n DatasetGen alembic upgrade head 2>&1 | tail -10
  else
    alembic upgrade head 2>&1 | tail -10
  fi
)
echo "   ✓ 迁移完成"

# 如果 infra-only 就到此为止
if [ "$INFRA_ONLY" = "1" ]; then
  echo ""
  echo "==> 仅基础设施模式，完成。"
  echo "   后端启动：cd apps/api && conda activate DatasetGen && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
  echo "   前端启动：cd apps/web && npm run dev"
  exit 0
fi

# ---------- Step 4: 启动 API / Web ----------
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"

# 在 Windows 下用 cmd //c start 打开新终端窗口（git bash 支持 //c 转义）；否则后台 nohup + 日志。
is_windows() {
  case "$(uname -s 2>/dev/null)" in
    CYGWIN*|MINGW*|MSYS*) return 0 ;;
    *) return 1 ;;
  esac
}

start_in_new_terminal() {
  local title="$1"
  local workdir_win="$2"  # Windows 风格路径
  local ps_cmd="$3"       # PowerShell 命令（用 ; 连接多条）
  if is_windows; then
    # 构造 PowerShell 命令：设窗口标题 + 切目录 + 执行
    # \$ 防止 bash 展开 $Host；Set-Location -LiteralPath 避免路径含空格或特殊字符问题
    local full_ps="\$Host.UI.RawUI.WindowTitle='$title'; Set-Location -LiteralPath '$workdir_win'; $ps_cmd"
    # cmd /c start "" 的空标题避免 start 把后续参数当标题
    cmd //c start "" powershell -NoExit -Command "$full_ps"
  else
    # 非 Windows：后台运行
    local logfile="$LOG_DIR/${title}.log"
    echo "   （非 Windows 环境，后台运行，日志: $logfile）"
    ( cd "$(cygpath -u "$workdir_win" 2>/dev/null || echo "$workdir_win")" && eval "$ps_cmd" ) >"$logfile" 2>&1 &
    echo $! > "$LOG_DIR/${title}.pid"
  fi
}

echo ""
echo "==> [4/4] 启动应用进程..."

if [ "$START_API" = "1" ]; then
  echo "   启动 API（新 PowerShell 窗口）..."
  start_in_new_terminal "R1plus-API" \
    "$(cygpath -w "$REPO_ROOT/apps/api" 2>/dev/null || echo "$REPO_ROOT/apps/api")" \
    "conda activate DatasetGen; uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
fi

if [ "$START_WEB" = "1" ]; then
  echo "   启动 Web（新 PowerShell 窗口）..."
  start_in_new_terminal "R1plus-Web" \
    "$(cygpath -w "$REPO_ROOT/apps/web" 2>/dev/null || echo "$REPO_ROOT/apps/web")" \
    "npm run dev"
fi

# ---------- 汇总 ----------
echo ""
echo "==========================================="
echo "✓ 启动完成"
echo "==========================================="
echo "  后端 API    : http://localhost:8000/api/health"
echo "  API 文档     : http://localhost:8000/docs"
echo "  前端 Web    : http://localhost:3000"
echo "  MinIO 控制台: http://localhost:9003  (minioadmin / minioadmin123)"
echo "  PostgreSQL  : localhost:5433  (datasetgen / datasetgen_dev_password)"
echo "  Redis       : localhost:6380"
echo ""
echo "  管理员登录: admin / admin123"
echo ""
echo "  停止所有应用进程（保留 Docker）：  ./scripts/dev-stop.sh"
echo "  停止 Docker：docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env down"
echo "==========================================="
