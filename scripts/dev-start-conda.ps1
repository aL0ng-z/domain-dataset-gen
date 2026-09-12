<#
.SYNOPSIS
  Windows + conda one-click startup for local testing.

.EXAMPLE
  .\scripts\dev-start-conda.ps1

.NOTES
  Run this script from an already activated DatasetGen conda environment:
    conda activate DatasetGen
    .\scripts\dev-start-conda.ps1

  This script does not create or use .venv, and it does not switch conda
  environments for you. Backend commands use the current environment's python.
  API and Web are started as hidden background PowerShell processes and write
  logs under ./logs.
#>

$ErrorActionPreference = "Stop"

if ($args.Count -gt 0) {
    Write-Host "dev-start-conda.ps1 does not accept parameters." -ForegroundColor Red
    Write-Host "Please run:"
    Write-Host "  .\scripts\dev-start-conda.ps1"
    exit 1
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$DockerEnv = Join-Path $RepoRoot "infra\docker\.env"
$DockerEnvExample = Join-Path $RepoRoot "infra\docker\.env.example"
$ApiEnv = Join-Path $RepoRoot "apps\api\.env"
$ApiEnvExample = Join-Path $RepoRoot "apps\api\.env.example"
$WebEnv = Join-Path $RepoRoot "apps\web\.env.local"
$LogDir = Join-Path $RepoRoot "logs"
$ApiPort = 8000
$WebPort = $null
$ExpectedEnvName = "DatasetGen"
$PythonVersion = "3.11"
$BackendPythonPathEntries = @(
    (Join-Path $RepoRoot "apps\api"),
    (Join-Path $RepoRoot "libs\domain"),
    (Join-Path $RepoRoot "libs\storage"),
    (Join-Path $RepoRoot "libs\parsing"),
    (Join-Path $RepoRoot "libs\cleaning"),
    (Join-Path $RepoRoot "libs\splitters"),
    (Join-Path $RepoRoot "libs\llm")
)

if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

function Assert-Command {
    param([string]$Name, [string]$Hint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Write-Host "Missing command: $Name" -ForegroundColor Red
        Write-Host "Please install: $Hint"
        exit 1
    }
}

function Quote-PsLiteral {
    param([string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

function Set-BackendPythonPath {
    $entries = @()
    foreach ($entry in $BackendPythonPathEntries) {
        if (Test-Path -LiteralPath $entry) {
            $entries += $entry
        }
    }
    if ($env:PYTHONPATH) {
        $entries += ($env:PYTHONPATH -split [IO.Path]::PathSeparator)
    }
    $env:PYTHONPATH = (($entries | Where-Object { $_ } | Select-Object -Unique) -join [IO.Path]::PathSeparator)
    Write-Host "   PYTHONPATH includes local backend packages."
}

function Assert-RuntimeReady {
    Write-Host "==> Checking local runtime dependencies..."
    Invoke-NativeChecked `
        -Exe $script:PythonExe `
        -Arguments @("-c", "import app.main, domain, storage, parsing, cleaning, splitters, llm") `
        -FailureMessage "Backend imports failed. Check that Python dependencies are installed in the current conda environment."

    Assert-Command "npm.cmd" "Node.js 20/22 LTS"
    $nextCmd = Join-Path $RepoRoot "apps\web\node_modules\.bin\next.cmd"
    if (-not (Test-Path -LiteralPath $nextCmd)) {
        Write-Host "Frontend dependencies are missing: apps\web\node_modules\.bin\next.cmd was not found." -ForegroundColor Red
        Write-Host "This startup script does not install dependencies. Run once:"
        Write-Host "  cd apps\web"
        Write-Host "  npm ci"
        exit 1
    }
}

function Invoke-NativeChecked {
    param(
        [string]$Exe,
        [string[]]$Arguments,
        [string]$FailureMessage,
        [string]$WorkDir = $RepoRoot,
        [switch]$Quiet
    )
    Push-Location -LiteralPath $WorkDir
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Native tools such as Docker Compose may write progress to stderr even
        # on success. Judge native command failure by exit code instead.
        $ErrorActionPreference = "Continue"
        if ($Quiet) {
            & $Exe @Arguments *>&1 | Out-Null
        } else {
            & $Exe @Arguments
        }
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "$FailureMessage (exit code $exitCode)"
        }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
        Pop-Location
    }
}

function Get-CurrentPythonMinor {
    $output = & $script:PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to inspect Python version."
    }
    return (($output | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 1).Trim())
}

function Assert-CurrentCondaEnv {
    Write-Host "==> [2/7] Checking current conda environment..."
    Assert-Command "python" "Conda environment '$ExpectedEnvName' with Python $PythonVersion"

    $pythonCommand = Get-Command "python" -ErrorAction Stop
    $script:PythonExe = if ($pythonCommand.Source) { $pythonCommand.Source } else { "python" }

    $currentEnv = $env:CONDA_DEFAULT_ENV
    if ($currentEnv -ne $ExpectedEnvName) {
        Write-Host "Current conda environment is '$currentEnv', expected '$ExpectedEnvName'." -ForegroundColor Red
        Write-Host "Please run:"
        Write-Host "  conda activate $ExpectedEnvName"
        Write-Host "  .\scripts\dev-start-conda.ps1"
        exit 1
    }

    $actualVersion = Get-CurrentPythonMinor
    if ($actualVersion -ne $PythonVersion) {
        Write-Host "Current Python is $actualVersion, but this project requires Python $PythonVersion." -ForegroundColor Red
        Write-Host "Python executable: $script:PythonExe"
        exit 1
    }

    Write-Host "   Environment: $currentEnv"
    Write-Host "   Python: $script:PythonExe ($actualVersion)"
}

function Get-EnvValue {
    param([string]$Key, [string]$Fallback)
    if (-not (Test-Path -LiteralPath $DockerEnv)) { return $Fallback }
    $line = Get-Content -LiteralPath $DockerEnv | Where-Object { $_ -match "^$Key=" } | Select-Object -Last 1
    if (-not $line) { return $Fallback }
    return ($line -replace "^$Key=", "")
}

function New-LocalEnvFiles {
    Write-Host "==> [1/7] Preparing local environment files..."

    if (-not (Test-Path -LiteralPath $DockerEnv)) {
        Copy-Item -LiteralPath $DockerEnvExample -Destination $DockerEnv
        Write-Host "   Created infra\docker\.env"
    } else {
        Write-Host "   infra\docker\.env exists."
    }

    $pgPort = Get-EnvValue "POSTGRES_PORT" "5432"
    $redisPort = Get-EnvValue "REDIS_PORT" "6379"
    $minioPort = Get-EnvValue "MINIO_HOST_PORT" "9000"
    $pgDb = Get-EnvValue "POSTGRES_DB" "datasetgen"
    $pgUser = Get-EnvValue "POSTGRES_USER" "datasetgen"
    $pgPassword = Get-EnvValue "POSTGRES_PASSWORD" "datasetgen_dev_password"
    $minioAccessKey = Get-EnvValue "MINIO_ACCESS_KEY" "minioadmin"
    $minioSecretKey = Get-EnvValue "MINIO_SECRET_KEY" "minioadmin123"
    $bucketDocuments = Get-EnvValue "MINIO_BUCKET_DOCUMENTS" "documents"
    $bucketOutputs = Get-EnvValue "MINIO_BUCKET_OUTPUTS" "outputs"

    if (-not (Test-Path -LiteralPath $ApiEnv)) {
        if (Test-Path -LiteralPath $ApiEnvExample) {
            Copy-Item -LiteralPath $ApiEnvExample -Destination $ApiEnv
        }
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
JWT_ALGORITHM=HS256

MINERU_API_TOKEN=
PADDLEOCR_API_TOKEN=

API_HOST=0.0.0.0
API_PORT=8000
"@ | Set-Content -LiteralPath $ApiEnv -Encoding UTF8
        Write-Host "   Created apps\api\.env"
    } else {
        Write-Host "   apps\api\.env exists."
    }

    if (-not (Test-Path -LiteralPath $WebEnv)) {
        @"
NEXT_PUBLIC_API_URL=http://localhost:8000/api
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
"@ | Set-Content -LiteralPath $WebEnv -Encoding UTF8
        Write-Host "   Created apps\web\.env.local"
    } else {
        Write-Host "   apps\web\.env.local exists."
    }
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
    foreach ($port in $candidates) {
        if (-not (Test-PortInUse -Port $port)) { return $port }
    }
    Write-Host "No free frontend port found in 3000-3005." -ForegroundColor Red
    exit 1
}

function Wait-ServiceReady {
    param([string]$Service, [string]$Label)
    Write-Host "   Waiting for $Label..."
    for ($i = 1; $i -le 45; $i++) {
        $cid = (& docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env ps -q $Service 2>$null)
        if ($cid) {
            $health = (& docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $cid 2>$null)
            if ($health -eq "healthy" -or $health -eq "running") {
                Write-Host "   $Label ready."
                return
            }
        }
        Start-Sleep -Seconds 1
    }
    Write-Host "   $Label was not confirmed ready within 45 seconds. Continuing." -ForegroundColor Yellow
}

function Start-DockerInfra {
    Write-Host "==> [3/7] Starting Docker infrastructure..."
    Assert-Command "docker" "Docker Desktop"
    try {
        Invoke-NativeChecked -Exe "docker" -Arguments @("info") -FailureMessage "Docker Desktop is not running" -Quiet
    } catch {
        Write-Host "Docker Desktop is not running. Start Docker Desktop and retry." -ForegroundColor Red
        exit 1
    }

    Invoke-NativeChecked `
        -Exe "docker" `
        -Arguments @("compose", "-f", "infra/docker/docker-compose.yml", "--env-file", "infra/docker/.env", "stop", "worker") `
        -FailureMessage "Could not stop the Docker worker before local startup"
    Invoke-NativeChecked `
        -Exe "docker" `
        -Arguments @("compose", "-f", "infra/docker/docker-compose.yml", "--env-file", "infra/docker/.env", "up", "-d", "postgres", "redis", "minio", "minio-init") `
        -FailureMessage "Docker infrastructure startup failed"
    Wait-ServiceReady -Service "postgres" -Label "PostgreSQL"
    Wait-ServiceReady -Service "redis" -Label "Redis"
    Wait-ServiceReady -Service "minio" -Label "MinIO"
    Invoke-NativeChecked `
        -Exe "docker" `
        -Arguments @("compose", "-f", "infra/docker/docker-compose.yml", "--env-file", "infra/docker/.env", "up", "minio-init") `
        -FailureMessage "MinIO bucket initialization failed" `
        -Quiet
    Write-Host "   MinIO buckets confirmed."
}

function Invoke-DatabaseSetup {
    Write-Host "==> [4/7] Applying database migrations..."
    $apiDir = Join-Path $RepoRoot "apps\api"
    Invoke-NativeChecked `
        -Exe $script:PythonExe `
        -Arguments @("-m", "alembic", "upgrade", "head") `
        -FailureMessage "Alembic migration failed" `
        -WorkDir $apiDir

    Write-Host "==> [5/7] Seeding default data..."
    Invoke-NativeChecked `
        -Exe $script:PythonExe `
        -Arguments @("../../scripts/init_seed.py") `
        -FailureMessage "Seed script failed" `
        -WorkDir $apiDir
}

function Stop-ByPidFile {
    param([string]$PidFile, [string]$Label)
    if (-not (Test-Path -LiteralPath $PidFile)) { return }
    $targetPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue
    if (-not $targetPid) { return }
    Write-Host "   Stopping previous $Label process tree PID=$targetPid..."
    taskkill /F /T /PID $targetPid 2>&1 | Out-Null
}

function Start-BackgroundPowerShell {
    param(
        [string]$Title,
        [string]$WorkDir,
        [string]$Command,
        [string]$PidFile,
        [string]$LogFile
    )

    "===== $(Get-Date -Format o) $Title =====" | Add-Content -LiteralPath $LogFile -Encoding UTF8
    $titleQuoted = Quote-PsLiteral $Title
    $workDirQuoted = Quote-PsLiteral $WorkDir
    $logFileQuoted = Quote-PsLiteral $LogFile
    $pythonPathQuoted = Quote-PsLiteral $env:PYTHONPATH
    $script = @"
`$Host.UI.RawUI.WindowTitle = $titleQuoted
`$env:PYTHONPATH = $pythonPathQuoted
Set-Location -LiteralPath $workDirQuoted
$Command *>> $logFileQuoted
"@
    $proc = Start-Process powershell -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $script) -PassThru -WindowStyle Hidden
    $proc.Id | Out-File -LiteralPath $PidFile -Encoding ASCII
    Write-Host "   $Title PID=$($proc.Id), log=$LogFile"
}

function Wait-HttpReady {
    param([string]$Url, [string]$Label, [int]$TimeoutSeconds = 90)
    Write-Host "   Waiting for $Label at $Url..."
    Assert-Command "curl.exe" "Windows curl"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & curl.exe --noproxy "*" --max-time 2 --silent --show-error --fail $Url 1>$null 2>$null
            $exitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        if ($exitCode -eq 0) {
            Write-Host "   $Label ready."
            return $true
        }
        Start-Sleep -Seconds 1
    }
    Write-Host "   $Label was not ready within $TimeoutSeconds seconds. Check logs." -ForegroundColor Yellow
    return $false
}

function Start-ApplicationProcesses {
    Write-Host "==> [6/7] Starting API, Worker and Web..."
    $apiPidFile = Join-Path $LogDir "R1plus-API.pid"
    $webPidFile = Join-Path $LogDir "R1plus-Web.pid"
    $apiLog = Join-Path $LogDir "R1plus-API.log"
    $webLog = Join-Path $LogDir "R1plus-Web.log"
    $workerPidFile = Join-Path $LogDir "R1plus-Worker.pid"
    $workerLog = Join-Path $LogDir "R1plus-Worker.log"

    Stop-ByPidFile -PidFile $apiPidFile -Label "API"
    Stop-ByPidFile -PidFile $webPidFile -Label "Web"
    Stop-ByPidFile -PidFile $workerPidFile -Label "Worker"

    if (Test-PortInUse -Port $ApiPort) {
        Write-Host "Port $ApiPort is already in use. Stop the existing process and retry." -ForegroundColor Red
        exit 1
    }

    $pythonQuoted = Quote-PsLiteral $script:PythonExe
    $apiCommand = "& $pythonQuoted -m uvicorn app.main:app --host 0.0.0.0 --port $ApiPort"
    Start-BackgroundPowerShell `
        -Title "R1plus-API-Conda" `
        -WorkDir (Join-Path $RepoRoot "apps\api") `
        -Command $apiCommand `
            -PidFile $apiPidFile `
            -LogFile $apiLog

    if (-not (Wait-HttpReady -Url "http://127.0.0.1:$ApiPort/api/health" -Label "API" -TimeoutSeconds 90)) {
        Write-Host "API failed to become ready. Last API log lines:" -ForegroundColor Red
        Get-Content -LiteralPath $apiLog -Tail 80 -ErrorAction SilentlyContinue
        exit 1
    }

    Start-BackgroundPowerShell `
        -Title "R1plus-Worker-Conda" `
        -WorkDir (Join-Path $RepoRoot "apps\api") `
        -Command "& $pythonQuoted -m app.workers.runner" `
        -PidFile $workerPidFile `
        -LogFile $workerLog
    Start-Sleep -Seconds 2
    $workerProcessId = [int](Get-Content -LiteralPath $workerPidFile -Raw).Trim()
    if (-not (Get-Process -Id $workerProcessId -ErrorAction SilentlyContinue)) {
        Get-Content -LiteralPath $workerLog -Tail 60
        throw "Worker exited during startup. Check logs/R1plus-Worker.log."
    }

    $WebPort = Get-FreeWebPort

    $webCommand = "& npm.cmd run dev -- -p $WebPort"
    Start-BackgroundPowerShell `
        -Title "R1plus-Web" `
        -WorkDir (Join-Path $RepoRoot "apps\web") `
        -Command $webCommand `
            -PidFile $webPidFile `
            -LogFile $webLog

    if (-not (Wait-HttpReady -Url "http://127.0.0.1:$WebPort" -Label "Web" -TimeoutSeconds 120)) {
        Write-Host "Web failed to become ready. Last Web log lines:" -ForegroundColor Red
        Get-Content -LiteralPath $webLog -Tail 80 -ErrorAction SilentlyContinue
        exit 1
    }

    return $WebPort
}

Write-Host "==> Repository root: $RepoRoot"

New-LocalEnvFiles
Assert-CurrentCondaEnv
Set-BackendPythonPath
Assert-RuntimeReady
Stop-ByPidFile -PidFile (Join-Path $LogDir "R1plus-Worker.pid") -Label "Worker"
Start-DockerInfra
Invoke-DatabaseSetup

$startedWebPort = Start-ApplicationProcesses

$minioConsolePort = Get-EnvValue "MINIO_CONSOLE_PORT" "9001"
$minioAccess = Get-EnvValue "MINIO_ACCESS_KEY" "minioadmin"
$minioSecret = Get-EnvValue "MINIO_SECRET_KEY" "minioadmin123"

Write-Host "==> [7/7] Ready for testing" -ForegroundColor Green
Write-Host "==========================================="
Write-Host "Backend API     : http://localhost:$ApiPort/api/health"
Write-Host "API docs        : http://localhost:$ApiPort/docs"
Write-Host "Frontend Web    : http://localhost:$startedWebPort"
Write-Host "MinIO console   : http://localhost:$minioConsolePort  ($minioAccess / $minioSecret)"
Write-Host "Admin login     : admin / admin123"
Write-Host "Conda env       : $env:CONDA_DEFAULT_ENV"
Write-Host "Python          : $script:PythonExe"
Write-Host "API log         : logs\R1plus-API.log"
Write-Host "Worker log      : logs\R1plus-Worker.log"
Write-Host "Web log         : logs\R1plus-Web.log"
Write-Host ""
Write-Host "Stop app only   : .\scripts\dev-stop.ps1"
Write-Host "Stop everything : .\scripts\dev-stop.ps1 -All"
Write-Host "==========================================="
