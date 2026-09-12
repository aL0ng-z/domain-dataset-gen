<#
.SYNOPSIS
  Starts isolated browser-review services: API :18000, runner, LLM stub and Web :3100.

.DESCRIPTION
  Uses only datasetgen_e2e in the isolated test PostgreSQL container and an
  e2e/<run-id>/ MinIO prefix. It never connects to the development database.
  After it reports ready, use the Playwright CLI: open the Web page, sign in
  as admin/admin123, then run tests/e2e/review-workflow.js.
#>

[CmdletBinding()]
param(
    [string]$Database = "datasetgen_e2e"
)

$ErrorActionPreference = "Stop"
if ($Database -ne "datasetgen_e2e") { throw "E2E database must be exactly datasetgen_e2e." }
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $repoRoot
if ($env:CONDA_DEFAULT_ENV -ne "DatasetGen") { throw "Run: conda activate DatasetGen" }
$backendPaths = @(
    (Join-Path $repoRoot "apps\api"),
    (Join-Path $repoRoot "libs\domain"),
    (Join-Path $repoRoot "libs\storage"),
    (Join-Path $repoRoot "libs\parsing"),
    (Join-Path $repoRoot "libs\cleaning"),
    (Join-Path $repoRoot "libs\splitters"),
    (Join-Path $repoRoot "libs\llm")
)
$env:PYTHONPATH = $backendPaths -join [IO.Path]::PathSeparator

& (Join-Path $repoRoot "scripts\test-infra.ps1")
$container = (& docker ps --filter "name=datasetgen-test-postgres-1" --format "{{.Names}}" | Select-Object -First 1)
if (-not $container) { throw "Isolated test PostgreSQL container is unavailable." }
$existsOutput = & docker exec $container psql -U datasetgen_test -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$Database'"
$exists = (($existsOutput | Out-String).Trim())
if (-not $exists) { & docker exec $container createdb -U datasetgen_test $Database }

$runId = [guid]::NewGuid().ToString("N").Substring(0, 12)
$env:POSTGRES_HOST = "127.0.0.1"; $env:POSTGRES_PORT = "55432"; $env:POSTGRES_DB = $Database
$env:POSTGRES_USER = "datasetgen_test"; $env:POSTGRES_PASSWORD = "datasetgen_test_password"
$env:REDIS_HOST = "127.0.0.1"; $env:REDIS_PORT = "56379"
$env:MINIO_ENDPOINT = "127.0.0.1:19000"; $env:MINIO_ACCESS_KEY = "testminioadmin"; $env:MINIO_SECRET_KEY = "testminioadmin123"
$env:MINIO_BUCKET_DOCUMENTS = "documents-test"; $env:MINIO_BUCKET_OUTPUTS = "outputs-test"; $env:MINIO_KEY_PREFIX = "e2e/$runId/"
$env:JWT_SECRET_KEY = "e2e-isolated-secret-$runId"; $env:API_CORS_ORIGINS = '["http://127.0.0.1:3100","http://localhost:3100"]'

Push-Location (Join-Path $repoRoot "apps\api")
try { & python -m alembic upgrade head; & python ..\..\scripts\init_seed.py } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "Migration or seed failed." }

$logDir = Join-Path $repoRoot "logs"; New-Item -ItemType Directory -Force -Path $logDir | Out-Null
function Stop-OwnedE2EPort([int]$Port, [string]$ExpectedCommand) {
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
        if ($process -and $process.CommandLine -like "*$ExpectedCommand*") {
            Stop-Process -Id $listener.OwningProcess -Force
        } elseif ($process) {
            throw "Port $Port is already used by an unrelated process: $($process.CommandLine)"
        }
    }
}
Stop-OwnedE2EPort 18765 "tests.e2e.llm_stub"
Stop-OwnedE2EPort 18000 "uvicorn app.main:app"
Stop-OwnedE2EPort 3100 "domain-dataset-gen\apps\web\node_modules\next"
function Start-E2EProcess([string]$Name, [string]$Directory, [string]$Command) {
    $log = Join-Path $logDir "E2E-$Name.log"
    # Start-Process inherits this script's fully isolated environment. Rebuilding
    # assignments as a command string corrupts JSON-valued settings such as CORS.
    $script = "`$ErrorActionPreference='Continue'; Set-Location -LiteralPath '$($Directory.Replace("'", "''"))'; $Command *>> '$($log.Replace("'", "''"))'"
    return Start-Process powershell -WindowStyle Hidden -ArgumentList @("-NoProfile","-Command",$script) -PassThru
}

$stub = Start-E2EProcess "Stub" $repoRoot "python -m uvicorn tests.e2e.llm_stub:app --host 127.0.0.1 --port 18765"
$api = Start-E2EProcess "API" (Join-Path $repoRoot "apps\api") "python -m uvicorn app.main:app --host 127.0.0.1 --port 18000"
$runner = Start-E2EProcess "Runner" (Join-Path $repoRoot "apps\api") "python -m app.workers.runner"
$webScript = "`$env:NEXT_PUBLIC_API_URL='http://127.0.0.1:18000/api'; `$env:NEXT_PUBLIC_WS_URL='ws://127.0.0.1:18000/ws'; npm.cmd run dev -- -p 3100"
$web = Start-E2EProcess "Web" (Join-Path $repoRoot "apps\web") $webScript
@{ stub = $stub.Id; api = $api.Id; runner = $runner.Id; web = $web.Id } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logDir "E2E-review-pids.json") -Encoding UTF8

for ($i = 0; $i -lt 60; $i++) {
    try { if ((Invoke-WebRequest -UseBasicParsing http://127.0.0.1:18000/api/health -TimeoutSec 2).StatusCode -eq 200) { break } } catch {}
    Start-Sleep -Seconds 1
    if ($i -eq 59) { throw "E2E API did not become ready. See logs/E2E-API.log." }
}
Write-Host "E2E ready: http://localhost:3100 (admin/admin123)"
Write-Host "Run ID: $runId; database: $Database; PIDs: stub=$($stub.Id), api=$($api.Id), runner=$($runner.Id), web=$($web.Id)"
Write-Host "Stop services: .\tests\e2e\stop-review-workflow.ps1"
