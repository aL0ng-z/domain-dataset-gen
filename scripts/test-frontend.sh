#!/usr/bin/env bash
# Run frontend quality gates in one command: lint + tsc + test + build.
# Assumes frontend dependencies are installed (npm ci).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$REPO_ROOT/apps/web"

if [[ ! -d "$WEB_DIR/node_modules" ]]; then
    echo "[error] Frontend dependencies missing. Run once: cd apps/web && npm ci" >&2
    exit 1
fi

cd "$WEB_DIR"

echo "==> [1/4] npm run lint"
npm run lint

echo "==> [2/4] tsc --noEmit"
npm exec tsc -- --noEmit

echo "==> [3/4] npm test -- --run"
npm test -- --run

echo "==> [4/4] npm run build"
npm run build

echo "Frontend quality gates passed."
