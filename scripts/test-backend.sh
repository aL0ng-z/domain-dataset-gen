#!/usr/bin/env bash
# Run backend quality gates in one command: ruff lint + pytest (with coverage).
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

echo "==> [1/2] Ruff lint (apps/api libs tests)"
python -m ruff check apps/api libs tests

echo "==> [2/2] Pytest (unit + integration + contract)"
PYTEST_ARGS_ARR=(-m pytest -q)
if [[ -n "${PYTEST_ARGS:-}" ]]; then
    read -r -a EXTRA <<<"$PYTEST_ARGS"
    PYTEST_ARGS_ARR+=("${EXTRA[@]}")
fi
if [[ "${COVERAGE:-1}" != "0" ]]; then
    PYTEST_ARGS_ARR+=(--cov=app --cov=domain --cov=storage --cov=parsing --cov=cleaning --cov=splitters --cov=llm --cov-report=term-missing:skip-covered --cov-report=xml:.coverage-reports/coverage.xml)
fi
python "${PYTEST_ARGS_ARR[@]}"

echo "Backend quality gates passed."
