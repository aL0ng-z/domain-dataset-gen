<#
.SYNOPSIS
  一键启动 R1 / R1+ 测试环境（基础设施 + 迁移 + API + Web）。

.DESCRIPTION
  在 PowerShell 中运行。执行：
    1. 检查 Docker Desktop
    2. docker compose up -d（postgres/redis/minio）
    3. 等待 postgres healthy + 确认 MinIO 桶
    4. alembic upgrade head（幂等）
    5. 在新 PowerShell 窗口启动 API 和 Web

.PARAMETER NoApi
  不启动后端。

.PARAMETER NoWeb
  不启动前端。

.PARAMETER InfraOnly
  仅启动基础设施 + 迁移。

.EXAMPLE
  .\scripts\dev-start.ps1
  .\scripts\dev-start.ps1 -NoWeb
  .\scripts\dev-start.ps1 -InfraOnly

.NOTES
  如果首次运行遇到执行策略错误：
    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
  或用一次性绕过：
    powershell -ExecutionPolicy Bypass -File .\scripts\dev-start.ps1
#>

[CmdletBinding()]
param(
    [switch]$NoApi,
    [switch]$NoWeb,
    [switch]$InfraOnly
)

# Continue on native-command stderr (docker/compose 会把版本警告写到 stderr，不是真错误)
$ErrorActionPreference = 'Continue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

Write-Host "==> 仓库根目录: $RepoRoot"

# ---------- [1/4] Docker 检查 ----------
Write-Host ""
Write-Host "==> [1/4] 检查 Docker Desktop 是否运行..."
try {
    docker info *>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "docker info 返回非零" }
    Write-Host "   ✓ Docker 已运行"
} catch {
    Write-Host "❌ Docker Desktop 未运行。请先启动 Docker Desktop，然后重试。" -ForegroundColor Red
    exit 1
}

# ---------- [2/4] 启动基础设施 ----------
Write-Host ""
Write-Host "==> [2/4] 启动 postgres / redis / minio ..."
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ docker compose up 失败" -ForegroundColor Red
    exit 1
}

Write-Host "   等待 postgres 就绪..."
$ready = $false
for ($i = 1; $i -le 30; $i++) {
    $health = (docker inspect --format='{{.State.Health.Status}}' docker-postgres-1 2>$null)
    if ($health -eq 'healthy') {
        Write-Host "   ✓ postgres healthy"
        $ready = $true
        break
    }
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    Write-Host "   ⚠ postgres 30 秒未 healthy，继续尝试迁移（可能失败）" -ForegroundColor Yellow
}

Write-Host "   确认 MinIO 桶..."
# docker compose 会把警告写到 stderr，用 2>&1 合并后用 Out-Null 吞掉
& docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up minio-init 2>&1 | Out-Null
Write-Host "   ✓ MinIO 桶就绪"

# ---------- [3/4] Alembic 迁移 ----------
Write-Host ""
Write-Host "==> [3/4] 应用数据库迁移 (alembic upgrade head) ..."
Push-Location -LiteralPath "$RepoRoot\apps\api"
try {
    $migrationOutput = conda run -n DatasetGen alembic upgrade head 2>&1
    $migrationOutput | Select-Object -Last 10 | ForEach-Object { Write-Host "   $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ 迁移失败" -ForegroundColor Red
        exit 1
    }
    Write-Host "   ✓ 迁移完成"
} finally {
    Pop-Location
}

if ($InfraOnly) {
    Write-Host ""
    Write-Host "==> 仅基础设施模式，完成。"
    Write-Host "   后端启动：cd apps\api ; conda activate DatasetGen ; uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
    Write-Host "   前端启动：cd apps\web ; npm run dev"
    exit 0
}

# ---------- [4/4] 启动 API / Web（新 PowerShell 窗口） ----------
Write-Host ""
Write-Host "==> [4/4] 启动应用进程..."

function Start-InNewPsWindow {
    param(
        [string]$Title,
        [string]$WorkDir,
        [string]$Cmd
    )
    # 反引号 ` 在 PowerShell 字符串中转义 $
    $psCommand = "`$Host.UI.RawUI.WindowTitle='$Title'; Set-Location -LiteralPath '$WorkDir'; $Cmd"
    # -PassThru 返回进程对象，用来记录 PID 供 stop 脚本按 PID 杀（Windows Terminal 会把多个窗口合并成 tab，靠标题找不可靠）
    $proc = Start-Process powershell -ArgumentList @('-NoExit', '-Command', $psCommand) -PassThru
    return $proc.Id
}

# PID 文件目录
$PidDir = Join-Path $RepoRoot 'logs'
if (-not (Test-Path $PidDir)) { New-Item -ItemType Directory -Path $PidDir | Out-Null }

if (-not $NoApi) {
    Write-Host "   启动 API（新 PowerShell 窗口：R1plus-API）..."
    $apiPid = Start-InNewPsWindow `
        -Title 'R1plus-API' `
        -WorkDir "$RepoRoot\apps\api" `
        -Cmd 'conda activate DatasetGen; uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload'
    $apiPid | Out-File -LiteralPath (Join-Path $PidDir 'R1plus-API.pid') -Encoding ASCII
    Write-Host "     PID=$apiPid 已记录到 logs\R1plus-API.pid"
}

if (-not $NoWeb) {
    Write-Host "   启动 Web（新 PowerShell 窗口：R1plus-Web）..."
    $webPid = Start-InNewPsWindow `
        -Title 'R1plus-Web' `
        -WorkDir "$RepoRoot\apps\web" `
        -Cmd 'npm run dev'
    $webPid | Out-File -LiteralPath (Join-Path $PidDir 'R1plus-Web.pid') -Encoding ASCII
    Write-Host "     PID=$webPid 已记录到 logs\R1plus-Web.pid"
}

# ---------- 汇总 ----------
Write-Host ""
Write-Host "===========================================" -ForegroundColor Green
Write-Host "✓ 启动完成" -ForegroundColor Green
Write-Host "==========================================="
Write-Host "  后端 API    : http://localhost:8000/api/health"
Write-Host "  API 文档     : http://localhost:8000/docs"
Write-Host "  前端 Web    : http://localhost:3000"
Write-Host "  MinIO 控制台: http://localhost:9003  (minioadmin / minioadmin123)"
Write-Host "  PostgreSQL  : localhost:5433  (datasetgen / datasetgen_dev_password)"
Write-Host "  Redis       : localhost:6380"
Write-Host ""
Write-Host "  管理员登录: admin / admin123"
Write-Host ""
Write-Host "  停止应用进程（保留 Docker）：.\scripts\dev-stop.ps1"
Write-Host "  停止 Docker：docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env down"
Write-Host "==========================================="
