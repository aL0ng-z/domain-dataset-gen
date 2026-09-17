<#
.SYNOPSIS
  Stop API / Worker / Web processes started by dev-start-conda.ps1.

.EXAMPLE
  .\scripts\dev-stop.ps1
  .\scripts\dev-stop.ps1 -All
#>

[CmdletBinding()]
param(
    [switch]$All
)

$ErrorActionPreference = 'Continue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot
$PidDir = Join-Path $RepoRoot 'logs'

function Stop-ByPidFile {
    param([string]$PidFile, [string]$Label)
    if (-not (Test-Path $PidFile)) {
        Write-Host "   ($Label PID file not found; skipping.)"
        return
    }

    $targetPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue

    if (-not $targetPid) {
        Write-Host "   ($Label PID file is empty.)"
        return
    }

    Write-Host "   Stopping $Label process tree PID=$targetPid ..."
    taskkill /F /T /PID $targetPid 2>&1 | Out-Null
}

Write-Host "==> Stopping API / Worker / Web processes..."
Stop-ByPidFile -PidFile (Join-Path $PidDir 'R1plus-API.pid') -Label 'R1plus-API'
Stop-ByPidFile -PidFile (Join-Path $PidDir 'R1plus-Web.pid') -Label 'R1plus-Web'
Stop-ByPidFile -PidFile (Join-Path $PidDir 'R1plus-Worker.pid') -Label 'R1plus-Worker'

if ($All) {
    Write-Host "==> Stopping Docker infrastructure containers..."
    docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env --profile worker stop
}

Write-Host "==> Done"
