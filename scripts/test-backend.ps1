<#
.SYNOPSIS
  Run backend quality gates in one command: ruff lint + pytest (with coverage).

.DESCRIPTION
  Runs from an already activated DatasetGen conda environment. Uses the
  current environment's python, so it never creates or switches virtualenvs.

  Environment variables (optional):
    PYTEST_ARGS   extra args passed through to pytest, e.g. "-k integration"
    COVERAGE      set to "0" / "false" to skip the coverage report

.EXAMPLE
  conda activate DatasetGen
  .\scripts\test-backend.ps1
  $env:PYTEST_ARGS = "-k integration"; .\scripts\test-backend.ps1
#>

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

function Invoke-NativeChecked {
    param(
        [string]$Exe,
        [string[]]$Arguments,
        [string]$FailureMessage,
        [string]$WorkDir = $RepoRoot
    )
    Push-Location -LiteralPath $WorkDir
    try {
        & $Exe @Arguments
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "$FailureMessage (exit code $exitCode)"
        }
    } finally {
        Pop-Location
    }
}

$currentEnv = $env:CONDA_DEFAULT_ENV
if ($currentEnv -ne "DatasetGen") {
    Write-Host "Current conda environment is '$currentEnv', expected 'DatasetGen'." -ForegroundColor Yellow
    Write-Host "Proceeding with the current python; if imports fail, run: conda activate DatasetGen" -ForegroundColor Yellow
}

Write-Host "==> [1/2] Ruff lint (apps/api libs tests)"
Invoke-NativeChecked -Exe "python" -Arguments @("-m", "ruff", "check", "apps/api", "libs", "tests") -FailureMessage "Ruff lint failed"

Write-Host "==> [2/2] Pytest (unit + integration + contract)"
$pytestArgs = @("-m", "pytest", "-q")
if ($env:PYTEST_ARGS) {
    $pytestArgs += ($env:PYTEST_ARGS -split " ")
}
$coverageEnabled = -not ($env:COVERAGE -in @("0", "false", "False"))
if ($coverageEnabled) {
    $pytestArgs += @("--cov=app", "--cov=domain", "--cov=storage", "--cov=parsing", "--cov=cleaning", "--cov=splitters", "--cov=llm", "--cov-report=term-missing:skip-covered", "--cov-report=xml:.coverage-reports/coverage.xml")
} else {
    $pytestArgs += "--cov=disabled"
}
Invoke-NativeChecked -Exe "python" -Arguments $pytestArgs -FailureMessage "Pytest failed"

Write-Host "Backend quality gates passed." -ForegroundColor Green
