#!/usr/bin/env bash
# Run backend quality gates: ruff lint + isolated-schema migration + pytest.
# Usage:
#   conda activate DatasetGen
#   ./scripts/test-backend.sh
# Optional env:
#   PYTEST_ARGS  extra args passed through to pytest, e.g. "-k integration"
#   COVERAGE=0   skip the coverage report
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ "${CONDA_DEFAULT_ENV:-}" != "DatasetGen" ]]; then
    echo "[warn] Current conda env is '${CONDA_DEFAULT_ENV:-}'; expected 'DatasetGen'." >&2
    echo "[warn] Proceeding with the current python." >&2
fi

TEST_ENV=(
  TESTING=1 POSTGRES_HOST=localhost POSTGRES_PORT=55432
  POSTGRES_DB=datasetgen_test POSTGRES_USER=datasetgen_test
  POSTGRES_PASSWORD=datasetgen_test_password
)
for pair in "${TEST_ENV[@]}"; do
  name="${pair%%=*}"; expected="${pair#*=}"
  if [[ -n "${!name:-}" && "${!name}" != "$expected" ]]; then
    echo "$name=${!name} is not the isolated test value $expected; refusing to run migrations." >&2
    exit 1
  fi
done

echo "==> [1/3] Ruff lint (apps/api libs tests)"
python -m ruff check apps/api libs tests

echo "==> [2/3] Upgrade isolated test schema"
( env "${TEST_ENV[@]}" python - <<'PY'
import asyncio, os, asyncpg

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
)
(cd apps/api && env "${TEST_ENV[@]}" python -m alembic upgrade head)

echo "==> [3/3] Pytest (unit + integration + contract)"
PYTEST_ARGS_ARR=(-m pytest -q)
if [[ -n "${PYTEST_ARGS:-}" ]]; then
    read -r -a EXTRA <<<"$PYTEST_ARGS"
    PYTEST_ARGS_ARR+=("${EXTRA[@]}")
fi
if [[ "${COVERAGE:-1}" != "0" ]]; then
    PYTEST_ARGS_ARR+=(--cov=app --cov=domain --cov=storage --cov=parsing --cov=cleaning --cov=splitters --cov=llm --cov-report=term-missing:skip-covered --cov-report=xml:.coverage-reports/coverage.xml)
fi
env "${TEST_ENV[@]}" python "${PYTEST_ARGS_ARR[@]}"

echo "Backend quality gates passed."
