#!/usr/bin/env bash
# Start / stop the isolated test infrastructure (PostgreSQL/Redis/MinIO).
# Usage:
#   ./scripts/test-infra.sh
#   ./scripts/test-infra.sh --stop
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="infra/docker/docker-compose.test.yml"
ENV_FILE="infra/docker/.env.test"
ENV_EXAMPLE="infra/docker/.env.test.example"

if [[ ! -f "$ENV_FILE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo "   Created $ENV_FILE from example."
fi

if [[ "${1:-}" == "--stop" ]]; then
    echo "==> Stopping test infrastructure..."
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down
    echo "Test infrastructure stopped."
    exit 0
fi

echo "==> Starting test infrastructure..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --wait
echo "Test infrastructure ready."
echo "  PostgreSQL: localhost:55432 (db: datasetgen_test)"
echo "  Redis     : localhost:56379"
echo "  MinIO     : localhost:19000 / console 19001 (testminioadmin / testminioadmin123)"
