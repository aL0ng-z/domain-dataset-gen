<#
.SYNOPSIS
  Run backend quality gates in one command: ruff lint + test-schema migration + pytest (with coverage).

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

. (Join-Path $PSScriptRoot "native-command.ps1")

$currentEnv = $env:CONDA_DEFAULT_ENV
if ($currentEnv -ne "DatasetGen") {
    Write-Host "Current conda environment is '$currentEnv', expected 'DatasetGen'." -ForegroundColor Yellow
    Write-Host "Proceeding with the current python; if imports fail, run: conda activate DatasetGen" -ForegroundColor Yellow
}

function Use-IsolatedTestDatabase {
    $expected = @{
        TESTING = "1"; POSTGRES_HOST = "localhost"; POSTGRES_PORT = "55432"
        POSTGRES_DB = "datasetgen_test"; POSTGRES_USER = "datasetgen_test"
        POSTGRES_PASSWORD = "datasetgen_test_password"
    }
    $previous = @{}
    foreach ($name in $expected.Keys) {
        $current = [Environment]::GetEnvironmentVariable($name, "Process")
        if ($current -and $current -ne $expected[$name]) {
            throw "$name=$current is not the isolated test value $($expected[$name]); refusing to run migrations."
        }
        $previous[$name] = $current
        if (-not $current) { Set-Item -Path "Env:$name" -Value $expected[$name] }
    }
    return $previous
}

function Restore-TestEnvironment([hashtable]$Previous) {
    foreach ($name in $Previous.Keys) {
        if ($null -eq $Previous[$name]) { Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue }
        else { Set-Item -Path "Env:$name" -Value $Previous[$name] }
    }
}

$testEnvironment = Use-IsolatedTestDatabase
try {
Write-Host "==> [1/3] Ruff lint (apps/api libs tests)"
Invoke-NativeChecked -Exe "python" -Arguments @("-m", "ruff", "check", "apps/api", "libs", "tests") -FailureMessage "Ruff lint failed"

Write-Host "==> [2/3] Upgrade isolated test schema"
$resetSchema = @"
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(
        host=os.environ['POSTGRES_HOST'], port=int(os.environ['POSTGRES_PORT']),
        user=os.environ['POSTGRES_USER'], password=os.environ['POSTGRES_PASSWORD'],
        database=os.environ['POSTGRES_DB'],
    )
    await conn.execute('DROP SCHEMA IF EXISTS public CASCADE')
    await conn.execute('CREATE SCHEMA public')
    await conn.close()
asyncio.run(main())
"@
Invoke-NativeChecked -Exe "python" -Arguments @("-c", $resetSchema) -FailureMessage "Test schema reset failed"
Invoke-NativeChecked -Exe "python" -Arguments @("-m", "alembic", "upgrade", "head") -FailureMessage "Test schema migration failed" -WorkDir (Join-Path $RepoRoot "apps\api")

Write-Host "==> [3/3] Pytest (unit + integration + contract)"
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
} finally {
    Restore-TestEnvironment $testEnvironment
}
