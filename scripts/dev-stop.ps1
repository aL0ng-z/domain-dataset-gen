<#
.SYNOPSIS
  停止 dev-start.ps1 启动的 API / Web 进程。

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
        Write-Host "   ($Label 未找到 PID 文件，跳过 PID 停止)"
        return
    }

    $targetPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue

    if (-not $targetPid) {
        Write-Host "   ($Label PID 文件为空)"
        return
    }

    Write-Host "   停止 $Label 进程树 PID=$targetPid ..."
    taskkill /F /T /PID $targetPid 2>&1 | Out-Null
}

Write-Host "==> 停止 API / Web 进程..."
Stop-ByPidFile -PidFile (Join-Path $PidDir 'R1plus-API.pid') -Label 'R1plus-API'
Stop-ByPidFile -PidFile (Join-Path $PidDir 'R1plus-Web.pid') -Label 'R1plus-Web'

if ($All) {
    Write-Host "==> 停止 Docker 基础设施 ..."
    docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env down
}

Write-Host "==> 完成"
