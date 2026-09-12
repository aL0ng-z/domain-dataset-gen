<#
.SYNOPSIS
  Run frontend quality gates in one command: lint + tsc + test + build.

.DESCRIPTION
  Assumes frontend dependencies are installed (npm ci). Runs every gate in the
  same order CI uses.

.EXAMPLE
  .\scripts\test-frontend.ps1
#>

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$WebDir = Join-Path $RepoRoot "apps\web"

if (-not (Test-Path -LiteralPath (Join-Path $WebDir "node_modules"))) {
    Write-Host "Frontend dependencies missing. Run once: cd apps\web; npm ci" -ForegroundColor Red
    exit 1
}

. (Join-Path $PSScriptRoot "native-command.ps1")

Write-Host "==> [1/4] npm run lint"
Invoke-NativeChecked -WorkDir $WebDir -Exe "npm.cmd" -Arguments @("run", "lint") -FailureMessage "ESLint failed"

Write-Host "==> [2/4] tsc --noEmit"
Invoke-NativeChecked -WorkDir $WebDir -Exe "npm.cmd" -Arguments @("exec", "tsc", "--", "--noEmit") -FailureMessage "TypeScript check failed"

Write-Host "==> [3/4] npm test -- --run"
Invoke-NativeChecked -WorkDir $WebDir -Exe "npm.cmd" -Arguments @("test", "--", "--run") -FailureMessage "Vitest failed"

Write-Host "==> [4/4] npm run build"
Invoke-NativeChecked -WorkDir $WebDir -Exe "npm.cmd" -Arguments @("run", "build") -FailureMessage "Next.js build failed"

Write-Host "Frontend quality gates passed." -ForegroundColor Green
