#!/usr/bin/env bash
# Alembic migration smoke test: reset schema, upgrade head, downgrade, upgrade head.
#
# Uses the isolated test database (default datasetgen_test). Overrides
# app.config.settings via environment variables, so it never touches dev data.
# The public schema is dropped and recreated first so the smoke test always runs
# against an empty database.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$REPO_ROOT/apps/api"

export POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
export POSTGRES_PORT="${POSTGRES_PORT:-55432}"
export POSTGRES_DB="${POSTGRES_DB:-datasetgen_test}"
export POSTGRES_USER="${POSTGRES_USER:-datasetgen_test}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-datasetgen_test_password}"

if [[ "$POSTGRES_DB" != "datasetgen_test" ]]; then
    echo "[error] Migration smoke test requires datasetgen_test DB; POSTGRES_DB=$POSTGRES_DB" >&2
    exit 1
fi

export PYTHONPATH="$API_DIR:$REPO_ROOT/libs/domain:$REPO_ROOT/libs/storage:$REPO_ROOT/libs/parsing:$REPO_ROOT/libs/cleaning:$REPO_ROOT/libs/splitters:$REPO_ROOT/libs/llm"

echo "==> [0/3] Reset public schema for empty DB"
python - <<'PY'
import asyncio
import os
import asyncpg

async def main():
    conn = await asyncpg.connect(
        host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]),
        user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
        database=os.environ["POSTGRES_DB"],
    )
    await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
    await conn.execute("CREATE SCHEMA public")
    await conn.close()

asyncio.run(main())
PY

cd "$API_DIR"

echo "==> [1/3] upgrade head on empty DB"
python -m alembic upgrade head

echo "==> [2/3] downgrade to previous revision"
python -m alembic downgrade -1

echo "==> [3/3] upgrade head again"
python -m alembic upgrade head

echo "Alembic migration smoke test passed."
