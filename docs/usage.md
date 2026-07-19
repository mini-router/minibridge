# Minibridge usage guide

This guide explains how to run Minibridge, send a request through it, and prove the result to a third party.

Minibridge has three practical parts:

- the host control plane, which manages providers, keys, proofs, and bundles
- the runner, which performs the actual LLM call and signs receipts
- the web UI, which is a separate frontend boundary for operators

The caller always receives the LLM response back from Minibridge, not directly from the upstream model provider.

## 1. What Minibridge proves

For each call, Minibridge records and signs:

- the request payload
- the response payload
- the model name and provider identity
- the token usage returned by the provider
- the computed cost from the pinned pricing table
- the service signature over the receipt
- the service attestation, when running in a TEE-backed environment

This gives you a proof artifact that other people can verify without having the upstream API key.

## 2. Local development setup

Install the package in editable mode:

```bash
pip install -e .
```

If you run tests or scripts directly from the repo root, set `PYTHONPATH=src`:

```bash
export PYTHONPATH=src
```

## 3. Run the host control plane

The host control plane is the administrative service. It stores providers, key policies, and exported proofs.

```bash
minibridge host serve --host 0.0.0.0 --port 7070
```

Common endpoints:

- `GET /health`
- `GET /providers`
- `POST /register-provider`
- `POST /register-key`
- `POST /call`
- `POST /prove`
- `GET /proofs`
- `GET /proofs/{proof_id}`
- `GET /bundle`
- `GET /bundle/export`
- `GET /bundle/manifest`

## 4. Register a provider

Providers are registry entries with a stable `provider_id`.

See [provider-registration.md](provider-registration.md) for copy-pastable payloads for OpenAI, OpenRouter, Chutes, and the mock backend.

Example:

```bash
curl -X POST http://127.0.0.1:7070/register-provider \
  -H 'Content-Type: application/json' \
  -d '{
    "provider_id": "openrouter-prod",
    "provider_kind": "openrouter",
    "endpoint_url": "https://openrouter.ai/api/v1/chat/completions",
    "payload_style": "chat-completions",
    "auth_header": "Authorization",
    "auth_scheme": "Bearer",
    "extra_headers": {
      "HTTP-Referer": "https://github.com/mini-router/minibridge",
      "X-OpenRouter-Title": "Minibridge"
    },
    "timeout_seconds": 30
  }'
```

## 5. Register a key policy

The upstream API key stays inside the trusted boundary. The service stores the key and associates it with a policy.

Example:

```bash
curl -X POST http://127.0.0.1:7070/register-key \
  -H 'Content-Type: application/json' \
  -d '{
    "owner_id": "provider-owner",
    "key_id": "openrouter-prod-key",
    "api_key": "YOUR_SECRET_KEY",
    "policy": {
      "allowed_callers": ["tester"],
      "allowed_models": ["openai/gpt-4o-mini"],
      "require_nonce": true,
      "require_expiry": true
    }
  }'
```

The key is never meant to be shared with the caller. Minibridge uses it internally and returns a signed proof of what happened.

## 6. Submit a proof-producing call

Use `POST /prove` when you want the response plus the signed proof.

Example:

```bash
curl -X POST http://127.0.0.1:7070/prove \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "req-001",
    "provider_id": "openrouter-prod",
    "caller_id": "tester",
    "owner_id": "provider-owner",
    "key_id": "openrouter-prod-key",
    "model": "openai/gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Reply with exactly: ok"}
    ],
    "nonce": "nonce-001",
    "expires_at": "2026-07-19T18:00:00Z"
  }'
```

The response includes:

- `response` — the provider reply
- `receipt` — the signed accounting record
- `proof` — the proof object the caller can archive or share

If you only want the response without proof packaging, use `POST /call`.

## 7. Verify a proof bundle

Minibridge can export a bundle that third parties can verify offline.

Export the current bundle:

```bash
curl http://127.0.0.1:7070/bundle/export > bundle.json
```

Or write the bundle to disk through the CLI:

```bash
minibridge bundle create --server http://127.0.0.1:7070 --bundle bundles/run-001
```

Verify it on another machine:

```bash
minibridge bundle verify --bundle bundles/run-001
```

The bundle contains:

- `manifest.json`
- `trajectories_raw.jsonl`
- `trajectories.jsonl`
- `validation_report.jsonl`
- `attestation.json` when the runner has attestation

Verification checks:

- receipt signature
- request hash
- response hash
- cost computation
- proof identity
- service identity
- public key fingerprint
- Merkle root over verified proofs
- attestation consistency when present

## 8. Run the runner in a CPU TEE

The runner is the execution boundary that performs the provider call and signs the receipt.

Local runner:

```bash
minibridge serve --host 0.0.0.0 --port 8000 --config configs/minibridge.demo.json --state-file ""
```

Phala/dstack runner:

```bash
minibridge serve --host 0.0.0.0 --port 8000 --config configs/minibridge.runner.phala.json --state-file ""
```

If you use the included Phala compose file, the container listens on `8000` and the public URL uses host port `18081`:

```bash
curl https://<app-id>-18081.dstack-pha-prod9.phala.network/health
```

The Phala runner template expects `OPENROUTER_API_KEY` in the TEE environment.

## 9. Web UI

The web UI is a separate app boundary under `web/`.

```bash
cd web
npm install
npm run dev
```

You can point it at the host control plane with:

```bash
VITE_MINIBRIDGE_HOST_URL=http://127.0.0.1:7070 npm run dev
```

## 10. Deployment model

The recommended production layout is:

- host control plane outside the TEE, or on a separate trusted management machine
- runner inside a CPU TEE
- web UI served separately

This keeps the trusted boundary small:

- the runner handles secret key material and provider calls
- the host manages metadata and proof export
- the UI is only a client of the host API

## 11. Common problems

If `unittest` cannot import `llm_api_proof`, set `PYTHONPATH=src`.

If a Phala URL returns nothing, check the host port in the compose mapping. The public URL uses the host port, not the container port.

If `/prove` fails, check:

- the provider is registered
- the key policy allows the caller and model
- the API key is present in the TEE environment
- the pricing table contains the requested model

If attestation is missing, the service still works, but the proof will only be as strong as the current attestation backend.
