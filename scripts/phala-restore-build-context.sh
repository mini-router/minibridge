#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="${1:-/dstack/persistent/minibridge-src}"
TARGET_ROOT="${2:-/dstack}"

mkdir -p "$TARGET_ROOT/src" "$TARGET_ROOT/configs"

cat > "$SOURCE_ROOT/configs/minibridge.runner.phala.json" <<'JSON'
{
  "service_id": "minibridge-runner-phala",
  "tee_mode": "cpu-tee",
  "attestation_provider": {
    "kind": "dstack",
    "socket_path": "/var/run/dstack.sock",
    "mode": "cpu-tee",
    "backend": "dstack-socket"
  },
  "pricing_table": {
    "pricing_table_id": "minibridge-phala",
    "models": {
      "openai/gpt-4o-mini": {
        "input_per_1k": "0.1500",
        "output_per_1k": "0.6000"
      }
    }
  },
  "providers": [
    {
      "provider_id": "openrouter-prod",
      "provider_kind": "openrouter",
      "endpoint_url": "https://openrouter.ai/api/v1/chat/completions",
      "payload_style": "chat-completions",
      "extra_headers": {
        "HTTP-Referer": "https://github.com/mini-router/minibridge",
        "X-OpenRouter-Title": "Minibridge"
      },
      "timeout_seconds": 30
    }
  ],
  "keys": [
    {
      "owner_id": "provider-owner",
      "key_id": "openrouter-prod-key",
      "api_key_env": "OPENROUTER_API_KEY",
      "policy": {
        "allowed_models": ["openai/gpt-4o-mini"],
        "require_nonce": true,
        "require_expiry": true
      }
    }
  ]
}
JSON

cp -a "$SOURCE_ROOT/Dockerfile" "$TARGET_ROOT/Dockerfile"
cp -a "$SOURCE_ROOT/pyproject.toml" "$TARGET_ROOT/pyproject.toml"
cp -a "$SOURCE_ROOT/README.md" "$TARGET_ROOT/README.md"
cp -a "$SOURCE_ROOT/src/." "$TARGET_ROOT/src/"
cp -a "$SOURCE_ROOT/configs/." "$TARGET_ROOT/configs/"
