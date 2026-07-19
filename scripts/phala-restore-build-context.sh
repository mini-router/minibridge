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
      "qwen/qwen3-coder-30b-a3b-instruct": {
        "input_per_1k": "0.00007",
        "output_per_1k": "0.00027"
      },
      "openai/gpt-oss-120b": {
        "input_per_1k": "0.000036",
        "output_per_1k": "0.00018"
      },
      "google/gemma-3-4b-it": {
        "input_per_1k": "0.00005",
        "output_per_1k": "0.00010"
      },
      "nvidia/nemotron-3-ultra-550b-a55b": {
        "input_per_1k": "0.00050",
        "output_per_1k": "0.00220"
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
      "owner_id": "minirouter-miners",
      "key_id": "minirouter-miners-openrouter-key",
      "api_key_env": "OPENROUTER_API_KEY",
      "policy": {
        "allowed_callers": ["minirouter-maintainer"],
        "allowed_models": [
          "qwen/qwen3-coder-30b-a3b-instruct",
          "openai/gpt-oss-120b",
          "google/gemma-3-4b-it",
          "nvidia/nemotron-3-ultra-550b-a55b"
        ],
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
