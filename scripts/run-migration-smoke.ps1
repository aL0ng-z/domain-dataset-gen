# Requires UTF-8 BOM for PowerShell 5.1 to parse non-ASCII comments correctly.
# This file is saved as UTF-8 with BOM.

<#
.SYNOPSIS
  Alembic migration smoke test: reset schema, upgrade head, downgrade, upgrade head.

.DESCRIPTION
  Uses the isolated test database (default datasetgen_test). Overrides
  app.config.settings via environment variables, so it never touches dev data.
  The public schema is dropped and recreated first so the smoke test always runs
  against an empty database.

.EXAMPLE
  .\scripts\run-migration-smoke.ps1
#>

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ApiDir = Join-Path $RepoRoot "apps\api"

# Default to isolated test database
if (-not $env:POSTGRES_HOST) { $env:POSTGRES_HOST = "localhost" }
if (-not $env:POSTGRES_PORT) { $env:POSTGRES_PORT = "55432" }
if (-not $env:POSTGRES_DB) { $env:POSTGRES_DB = "datasetgen_test" }
if (-not $env:POSTGRES_USER) { $env:POSTGRES_USER = "datasetgen_test" }
if (-not $env:POSTGRES_PASSWORD) { $env:POSTGRES_PASSWORD = "datasetgen_test_password" }

if ($env:POSTGRES_DB -ne "datasetgen_test") {
    Write-Host "Migration smoke test requires datasetgen_test DB; POSTGRES_DB=$($env:POSTGRES_DB)" -ForegroundColor Red
    exit 1
}

# Backend python path entries (kept in sync with dev-start-conda.ps1).
$env:PYTHONPATH = (@(
    $ApiDir,
    (Join-Path $RepoRoot "libs\domain"),
    (Join-Path $RepoRoot "libs\storage"),
    (Join-Path $RepoRoot "libs\parsing"),
    (Join-Path $RepoRoot "libs\cleaning"),
    (Join-Path $RepoRoot "libs\splitters"),
    (Join-Path $RepoRoot "libs\llm")
) -join [IO.Path]::PathSeparator)

function Invoke-Alembic {
    param([string[]]$AlembicArgs)
    Push-Location -LiteralPath $ApiDir
    try {
        & python -m alembic @AlembicArgs
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            throw "alembic $AlembicArgs failed (exit code $code)"
        }
    } finally {
        Pop-Location
    }
}

Write-Host "==> [0/3] Reset public schema for empty DB"
$py = @"
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
& python -c $py
if ($LASTEXITCODE -ne 0) {
    throw "Failed to reset public schema"
}

Write-Host "==> [1/3] upgrade head on empty DB"
Invoke-Alembic @("upgrade", "head")

Write-Host "==> [2/3] downgrade to previous revision"
Invoke-Alembic @("downgrade", "-1")

Write-Host "==> [3/3] upgrade head again"
Invoke-Alembic @("upgrade", "head")

Write-Host "Alembic migration smoke test passed." -ForegroundColor Green
