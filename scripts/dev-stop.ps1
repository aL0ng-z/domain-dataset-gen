<#
.SYNOPSIS
  停止 dev-start.ps1 启动的 API / Web 进程。

.DESCRIPTION
  读 logs\R1plus-*.pid，taskkill /T 杀整个进程树。
  Windows Terminal 会把多窗口合并成 tab，靠标题匹配不可靠，所以用 PID。

.PARAMETER All
  除了 API/Web 之外，还停止 Docker 基础设施。

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
    param(
        [string]$PidFile,
        [string]$Label
    )
    if (-not (Test-Path $PidFile)) {
        Write-Host "   ($Label 未启动，无 PID 文件)"
        return
    }
    $targetPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    if (-not $targetPid) {
        Write-Host "   ($Label PID 文件为空)"
        Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue
        return
    }
    Write-Host "   杀 $Label 进程树 PID=$targetPid ..."
    # /T 杀整个树：PowerShell → conda → python uvicorn / node
    taskkill /F /T /PID $targetPid 2>&1 | Out-Null
    Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue
}

Write-Host "==> 停止 API ..."
Stop-ByPidFile -PidFile (Join-Path $PidDir 'R1plus-API.pid') -Label 'R1plus-API'

Write-Host "==> 停止 Web ..."
Stop-ByPidFile -PidFile (Join-Path $PidDir 'R1plus-Web.pid') -Label 'R1plus-Web'

# 兜底：按端口杀残留（若 PID 文件丢失）
function Stop-ByPort {
    param([int]$Port, [string]$Label)
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            Write-Host "   兜底：端口 $Port 仍被 PID=$($c.OwningProcess) 占用（$Label），杀..."
            taskkill /F /T /PID $c.OwningProcess 2>&1 | Out-Null
        }
    } catch {}
}

Stop-ByPort -Port 8000 -Label 'API'
Stop-ByPort -Port 3000 -Label 'Web'

if ($All) {
    Write-Host "==> 停止 Docker 基础设施 ..."
    docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env down
}

Write-Host "==> 完成"
