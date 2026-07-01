<#
.SYNOPSIS
  一键启动本地开发环境：Docker 基础设施、依赖、迁移、种子数据、API、Web。

.EXAMPLE
  .\scripts\dev-start.ps1
  .\scripts\dev-start.ps1 -NoWeb
  .\scripts\dev-start.ps1 -InfraOnly
  .\scripts\dev-start.ps1 -SkipInstall
  .\scripts\dev-start.ps1 -WithMinerU  # 兼容旧命令，MinerU 现已默认可用

.NOTES
  如果首次运行遇到执行策略错误：
    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
  或一次性绕过：
    powershell -ExecutionPolicy Bypass -File .\scripts\dev-start.ps1
#>

[CmdletBinding()]
param(
    [switch]$NoApi,
    [switch]$NoWeb,
    [switch]$InfraOnly,
    [switch]$SkipInstall,
    [switch]$WithMinerU
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$DockerEnv = Join-Path $RepoRoot 'infra\docker\.env'
$DockerEnvExample = Join-Path $RepoRoot 'infra\docker\.env.example'
$ApiEnv = Join-Path $RepoRoot 'apps\api\.env'
$WebEnv = Join-Path $RepoRoot 'apps\web\.env.local'
$LogDir = Join-Path $RepoRoot 'logs'
$PythonVersion = '3.11'
$WebPort = $null
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

function Assert-Command {
    param([string]$Name, [string]$Hint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Write-Host "缺少命令：$Name" -ForegroundColor Red
        Write-Host "请先安装：$Hint"
        exit 1
    }
}

function Get-EnvValue {
    param([string]$Key, [string]$Fallback)
    if (-not (Test-Path $DockerEnv)) { return $Fallback }
    $line = Get-Content -LiteralPath $DockerEnv | Where-Object { $_ -match "^$Key=" } | Select-Object -Last 1
    if (-not $line) { return $Fallback }
    return ($line -replace "^$Key=", '')
}

function Test-PortInUse {
    param([int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        return $null -ne $conn
    } catch {
        return $false
    }
}

function Get-FreeWebPort {
    $preferred = if ($env:WEB_PORT) { [int]$env:WEB_PORT } else { 3000 }
    $candidates = @($preferred, 3000, 3001, 3002, 3003, 3004, 3005) | Select-Object -Unique
    foreach ($p in $candidates) {
        if (-not (Test-PortInUse -Port $p)) { return $p }
    }
    Write-Host "No free frontend port found in 3000-3005." -ForegroundColor Red
    exit 1
}

function New-LocalEnvFiles {
    Write-Host "==> [1/7] 准备本地环境变量..."

    if (-not (Test-Path $DockerEnv)) {
        Copy-Item -LiteralPath $DockerEnvExample -Destination $DockerEnv
        Write-Host "   ✓ 已创建 infra\docker\.env"
    } else {
        Write-Host "   ✓ infra\docker\.env 已存在"
    }

    $pgPort = Get-EnvValue 'POSTGRES_PORT' '5432'
    $redisPort = Get-EnvValue 'REDIS_PORT' '6379'
    $minioPort = Get-EnvValue 'MINIO_HOST_PORT' '9000'
    $pgDb = Get-EnvValue 'POSTGRES_DB' 'datasetgen'
    $pgUser = Get-EnvValue 'POSTGRES_USER' 'datasetgen'
    $pgPassword = Get-EnvValue 'POSTGRES_PASSWORD' 'datasetgen_dev_password'
    $minioAccessKey = Get-EnvValue 'MINIO_ACCESS_KEY' 'minioadmin'
    $minioSecretKey = Get-EnvValue 'MINIO_SECRET_KEY' 'minioadmin123'
    $bucketDocuments = Get-EnvValue 'MINIO_BUCKET_DOCUMENTS' 'documents'
    $bucketOutputs = Get-EnvValue 'MINIO_BUCKET_OUTPUTS' 'outputs'

    if (-not (Test-Path $ApiEnv)) {
        @"
POSTGRES_HOST=localhost
POSTGRES_PORT=$pgPort
POSTGRES_DB=$pgDb
POSTGRES_USER=$pgUser
POSTGRES_PASSWORD=$pgPassword

REDIS_HOST=localhost
REDIS_PORT=$redisPort

MINIO_ENDPOINT=localhost:$minioPort
MINIO_ACCESS_KEY=$minioAccessKey
MINIO_SECRET_KEY=$minioSecretKey
MINIO_BUCKET_DOCUMENTS=$bucketDocuments
MINIO_BUCKET_OUTPUTS=$bucketOutputs
MINIO_SECURE=false

JWT_SECRET_KEY=dev-secret-key-change-in-production
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
MINERU_API_TOKEN=
PADDLEOCR_API_TOKEN=
API_HOST=0.0.0.0
API_PORT=8000
"@ | Set-Content -LiteralPath $ApiEnv -Encoding UTF8
        Write-Host "   ✓ 已创建 apps\api\.env（供本机 FastAPI 连接 Docker 服务）"
    } else {
        Write-Host "   ✓ apps\api\.env 已存在"
    }

    if (-not (Test-Path $WebEnv)) {
        @"
NEXT_PUBLIC_API_URL=http://localhost:8000/api
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
"@ | Set-Content -LiteralPath $WebEnv -Encoding UTF8
        Write-Host "   ✓ 已创建 apps\web\.env.local"
    } else {
        Write-Host "   ✓ apps\web\.env.local 已存在"
    }
}

function Wait-ServiceReady {
    param([string]$Service, [string]$Label)
    Write-Host "   等待 $Label 就绪..."
    for ($i = 1; $i -le 45; $i++) {
        $cid = (& docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env ps -q $Service 2>$null)
        if ($cid) {
            $health = (& docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $cid 2>$null)
            if ($health -eq 'healthy' -or $health -eq 'running') {
                Write-Host "   ✓ $Label ready"
                return
            }
        }
        Start-Sleep -Seconds 1
    }
    Write-Host "   ⚠ $Label 45 秒内未确认 ready，继续执行（后续步骤若失败请先检查 Docker）" -ForegroundColor Yellow
}

function Test-FrontendDependenciesNeedInstall {
    $webDir = Join-Path $RepoRoot 'apps\web'
    $nodeModules = Join-Path $webDir 'node_modules'
    $installedLock = Join-Path $nodeModules '.package-lock.json'

    if (-not (Test-Path $nodeModules)) {
        return $true
    }

    if (-not (Test-Path $installedLock)) {
        return $true
    }

    $installedAt = (Get-Item -LiteralPath $installedLock).LastWriteTimeUtc
    $manifestFiles = @(
        (Join-Path $webDir 'package.json'),
        (Join-Path $webDir 'package-lock.json')
    )

    foreach ($manifest in $manifestFiles) {
        if ((Test-Path $manifest) -and (Get-Item -LiteralPath $manifest).LastWriteTimeUtc -gt $installedAt) {
            return $true
        }
    }

    Push-Location -LiteralPath $webDir
    try {
        & npm ls --depth=0 *>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            return $true
        }
    } finally {
        Pop-Location
    }

    return $false
}

function Install-Dependencies {
    Write-Host "==> [3/7] 检查并安装依赖..."
    if ($SkipInstall) {
        Write-Host "   跳过依赖安装 (-SkipInstall)"
        return
    }

    Assert-Command 'uv' 'https://docs.astral.sh/uv/getting-started/installation/'
    Assert-Command 'npm' '安装 Node.js 20/22 LTS，其中包含 npm。'

    $apiVenv = Join-Path $RepoRoot '.venv'
    $pyvenvCfg = Join-Path $apiVenv 'pyvenv.cfg'
    if (Test-Path $pyvenvCfg) {
        $versionLine = Get-Content -LiteralPath $pyvenvCfg | Where-Object { $_ -match '^version_info = ' } | Select-Object -First 1
        if ($versionLine) {
            $venvVersion = $versionLine -replace '^version_info = ', ''
            if (-not $venvVersion.StartsWith($PythonVersion)) {
                Write-Host "   当前 .venv 使用 Python $venvVersion，但项目要求 Python $PythonVersion。" -ForegroundColor Yellow
                Write-Host "   请执行：Remove-Item -Recurse -Force .venv"
                Write-Host "   然后重新运行：.\scripts\dev-start.ps1"
                exit 1
            }
        }
    }

    $syncArgs = @('sync', '--python', $PythonVersion, '--extra', 'dev')
    Write-Host "   同步后端依赖（含 MinerU 本地推理运行库；模型仅在解析任务中加载）..."
    Push-Location -LiteralPath (Join-Path $RepoRoot 'apps\api')
    try { & uv @syncArgs } finally { Pop-Location }

    if (Test-FrontendDependenciesNeedInstall) {
        Write-Host "   同步前端 Node 依赖 (npm ci) ..."
        Push-Location -LiteralPath (Join-Path $RepoRoot 'apps\web')
        try { npm ci } finally { Pop-Location }
    } else {
        Write-Host "   ✓ 前端 Node 依赖已是最新"
    }
}

function Invoke-DatabaseSetup {
    Write-Host "==> [4/7] 应用数据库迁移..."
    Push-Location -LiteralPath (Join-Path $RepoRoot 'apps\api')
    try {
        & uv run --python $PythonVersion alembic upgrade head
        Write-Host "   ✓ 数据库表结构已更新到最新版本"

        Write-Host "==> [5/7] 初始化种子数据..."
        & uv run --python $PythonVersion python ../../scripts/init_seed.py
        Write-Host "   ✓ 默认管理员、项目和基础配置已确认"
    } finally {
        Pop-Location
    }
}

function Start-InNewPsWindow {
    param([string]$Title, [string]$WorkDir, [string]$Cmd)
    $psCommand = "`$Host.UI.RawUI.WindowTitle='$Title'; Set-Location -LiteralPath '$WorkDir'; $Cmd"
    $proc = Start-Process powershell -ArgumentList @('-NoExit', '-Command', $psCommand) -PassThru
    return $proc.Id
}

Write-Host "==> 仓库根目录: $RepoRoot"

New-LocalEnvFiles

Write-Host "==> [2/7] 启动 Docker 基础设施..."
Assert-Command 'docker' '安装并启动 Docker Desktop。'
try {
    docker info *>&1 | Out-Null
} catch {
    Write-Host "Docker Desktop 未运行。请先启动 Docker Desktop，然后重试。" -ForegroundColor Red
    exit 1
}
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up -d
Wait-ServiceReady -Service 'postgres' -Label 'PostgreSQL'
Wait-ServiceReady -Service 'redis' -Label 'Redis'
Wait-ServiceReady -Service 'minio' -Label 'MinIO'
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up minio-init *>&1 | Out-Null
Write-Host "   ✓ MinIO bucket 已确认"

Install-Dependencies
Invoke-DatabaseSetup

if ($InfraOnly) {
    Write-Host "==> 仅基础设施模式完成。"
    exit 0
}

Write-Host "==> [6/7] 启动应用进程..."
$WebPort = Get-FreeWebPort
if (-not $NoWeb -and $WebPort -ne 3000) {
    Write-Host "   ⚠ localhost:3000 已被占用，前端将使用 localhost:$WebPort" -ForegroundColor Yellow
}

if (-not $NoApi) {
    Write-Host "   启动 API（新 PowerShell 窗口：R1plus-API）..."
    $apiPid = Start-InNewPsWindow -Title 'R1plus-API' -WorkDir (Join-Path $RepoRoot 'apps\api') -Cmd "uv run --python $PythonVersion uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
    $apiPid | Out-File -LiteralPath (Join-Path $LogDir 'R1plus-API.pid') -Encoding ASCII
    Write-Host "     PID=$apiPid 已记录到 logs\R1plus-API.pid"
}

if (-not $NoWeb) {
    Write-Host "   启动 Web（新 PowerShell 窗口：R1plus-Web）..."
    $webPid = Start-InNewPsWindow -Title 'R1plus-Web' -WorkDir (Join-Path $RepoRoot 'apps\web') -Cmd "npm run dev -- -p $WebPort"
    $webPid | Out-File -LiteralPath (Join-Path $LogDir 'R1plus-Web.pid') -Encoding ASCII
    Write-Host "     PID=$webPid 已记录到 logs\R1plus-Web.pid"
}

$minioConsolePort = Get-EnvValue 'MINIO_CONSOLE_PORT' '9001'
$minioAccess = Get-EnvValue 'MINIO_ACCESS_KEY' 'minioadmin'
$minioSecret = Get-EnvValue 'MINIO_SECRET_KEY' 'minioadmin123'

Write-Host "==> [7/7] 启动完成" -ForegroundColor Green
Write-Host "==========================================="
Write-Host "后端 API    : http://localhost:8000/api/health"
Write-Host "API 文档    : http://localhost:8000/docs"
Write-Host "前端 Web    : http://localhost:$WebPort"
Write-Host "MinIO 控制台: http://localhost:$minioConsolePort  ($minioAccess / $minioSecret)"
Write-Host "管理员登录  : admin / admin123"
Write-Host "本地 MinerU  : 可用（仅在选择本地解析任务时加载模型）"
Write-Host ""
Write-Host "停止应用进程（保留 Docker）：.\scripts\dev-stop.ps1"
Write-Host "停止全部（含 Docker）      ：.\scripts\dev-stop.ps1 -All"
Write-Host "==========================================="
