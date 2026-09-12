<#
.SYNOPSIS
  Start / stop the isolated test infrastructure (PostgreSQL/Redis/MinIO).

.DESCRIPTION
  Uses infra/docker/docker-compose.test.yml with infra/docker/.env.test.
  These services use dedicated ports and a separate database / buckets, so they
  never touch development data.

.PARAMETER Stop
  Stop the test infrastructure instead of starting it.

.EXAMPLE
  .\scripts\test-infra.ps1
  .\scripts\test-infra.ps1 -Stop
#>

param(
    [switch]$Stop
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$ComposeFile = "infra/docker/docker-compose.test.yml"
$EnvFile = "infra/docker/.env.test"
$EnvExample = "infra/docker/.env.test.example"

if (-not (Test-Path -LiteralPath $EnvFile)) {
    Copy-Item -LiteralPath $EnvExample -Destination $EnvFile
    Write-Host "   Created $EnvFile from example." -ForegroundColor Green
}

function Invoke-DockerCompose {
    param([string[]]$Arguments)
    & docker compose -f $ComposeFile --env-file $EnvFile @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed (exit code $LASTEXITCODE)"
    }
}

if ($Stop) {
    Write-Host "==> Stopping test infrastructure..."
    Invoke-DockerCompose @("down")
    Write-Host "Test infrastructure stopped." -ForegroundColor Green
    exit 0
}

Write-Host "==> Starting test infrastructure..."
# minio-init is a one-shot container. Including it in `up --wait` makes
# Compose return failure after its successful zero exit, so wait only for the
# long-running services and execute initialization separately.
Invoke-DockerCompose @("up", "-d", "--wait", "postgres", "redis", "minio")
Invoke-DockerCompose @("up", "--no-deps", "minio-init")
Write-Host "Test infrastructure ready." -ForegroundColor Green
Write-Host "  PostgreSQL: localhost:55432 (db: datasetgen_test)"
Write-Host "  Redis     : localhost:56379"
Write-Host "  MinIO     : localhost:19000 / console 19001 (testminioadmin / testminioadmin123)"
