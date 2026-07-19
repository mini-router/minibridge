#!/usr/bin/env bash
set -euo pipefail

HOST_URL="${MINIBRIDGE_HOST_URL:-http://127.0.0.1:18080}"
RUNNER_ID="${MINIBRIDGE_RUNNER_ID:-runner-1}"
RUNNER_ENDPOINT_URL="${MINIBRIDGE_RUNNER_ENDPOINT_URL:-http://127.0.0.1:18081}"

payload="$(python3 - "$RUNNER_ID" "$RUNNER_ENDPOINT_URL" <<'PY'
import json
import sys

payload = {
    "runner_id": sys.argv[1],
    "endpoint_url": sys.argv[2],
}
print(json.dumps(payload))
PY
)"

curl -sS -X POST "${HOST_URL%/}/register-runner" \
  -H "Content-Type: application/json" \
  -d "$payload"
echo
