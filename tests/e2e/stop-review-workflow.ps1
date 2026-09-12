<# Stops only review-workflow processes on its dedicated local ports. #>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pidFile = Join-Path $repoRoot "logs\E2E-review-pids.json"
if (Test-Path -LiteralPath $pidFile) {
    $pids = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    foreach ($pidValue in @($pids.stub, $pids.api, $pids.runner, $pids.web)) {
        if ($pidValue -and (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)) {
            & taskkill.exe /F /T /PID $pidValue | Out-Null
        }
    }
    Remove-Item -LiteralPath $pidFile -Force
}
function Stop-OwnedE2EPort([int]$Port, [string]$ExpectedCommand) {
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
        if ($process -and $process.CommandLine -like "*$ExpectedCommand*") {
            & taskkill.exe /F /T /PID $listener.OwningProcess | Out-Null
            Write-Host "Stopped E2E process on port $Port."
        } elseif ($process) {
            throw "Port $Port is owned by an unrelated process: $($process.CommandLine)"
        }
    }
}
Stop-OwnedE2EPort 18765 "tests.e2e.llm_stub"
Stop-OwnedE2EPort 18000 "uvicorn app.main:app"
Stop-OwnedE2EPort 3100 "domain-dataset-gen\apps\web\node_modules\next"
