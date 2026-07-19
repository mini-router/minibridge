#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT"

if [[ -z "${OPENROUTER_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
  echo "warning: OPENROUTER_API_KEY is not set" >&2
fi

docker compose -f docker-compose.runner.yml up --build -d

if [[ -n "${MINIBRIDGE_HOST_URL:-}" ]]; then
  MINIBRIDGE_HOST_URL="${MINIBRIDGE_HOST_URL}" \
  MINIBRIDGE_RUNNER_ID="${MINIBRIDGE_RUNNER_ID:-runner-1}" \
  MINIBRIDGE_RUNNER_ENDPOINT_URL="${MINIBRIDGE_RUNNER_ENDPOINT_URL:-http://127.0.0.1:18081}" \
  ./scripts/register-runner.sh
fi

echo "runner_api=http://127.0.0.1:18081"
