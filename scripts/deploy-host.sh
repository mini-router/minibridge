#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT"
docker compose up --build -d

if [[ -n "${MINIBRIDGE_RUNNER_ENDPOINT_URL:-}" ]]; then
  MINIBRIDGE_HOST_URL="${MINIBRIDGE_HOST_URL:-http://127.0.0.1:18080}" \
  MINIBRIDGE_RUNNER_ID="${MINIBRIDGE_RUNNER_ID:-runner-1}" \
  MINIBRIDGE_RUNNER_ENDPOINT_URL="${MINIBRIDGE_RUNNER_ENDPOINT_URL}" \
  ./scripts/register-runner.sh
fi

echo "host_api=http://127.0.0.1:18080"
echo "web_ui=http://127.0.0.1:5173"
echo "runner_template=docker-compose.runner.yml"
