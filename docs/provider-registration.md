# Provider registration

`minibridge` treats provider backends as registry entries with a stable `provider_id`.

The service accepts:

- `POST /register-provider`
- `GET /providers`
- `GET /providers/{provider_id}`
- `POST /providers/{provider_id}/call`

The provider object is intentionally small:

```json
{
  "provider_id": "openrouter-prod",
  "provider_kind": "openrouter",
  "endpoint_url": "https://openrouter.ai/api/v1/chat/completions",
  "payload_style": "chat-completions",
  "auth_header": "Authorization",
  "auth_scheme": "Bearer",
  "extra_headers": {
    "HTTP-Referer": "https://example.com",
    "X-OpenRouter-Title": "Minibridge"
  },
  "timeout_seconds": 30
}
```

Examples below use the JSON shape that `POST /register-provider` accepts.

## OpenAI

OpenAI publishes the official API reference and the Responses API surface.

```json
{
  "provider_id": "openai-prod",
  "provider_kind": "openai",
  "endpoint_url": "https://api.openai.com/v1/responses",
  "payload_style": "responses",
  "auth_header": "Authorization",
  "auth_scheme": "Bearer",
  "extra_headers": {},
  "timeout_seconds": 30
}
```

Use this when you want the service to call the OpenAI API directly.

## OpenRouter

OpenRouter documents a unified API and OpenAI-SDK compatibility.

```json
{
  "provider_id": "openrouter-prod",
  "provider_kind": "openrouter",
  "endpoint_url": "https://openrouter.ai/api/v1/chat/completions",
  "payload_style": "chat-completions",
  "auth_header": "Authorization",
  "auth_scheme": "Bearer",
  "extra_headers": {
    "HTTP-Referer": "https://example.com",
    "X-OpenRouter-Title": "Minibridge"
  },
  "timeout_seconds": 30
}
```

Use this when the upstream endpoint is OpenAI-compatible chat completions.

## Chutes

Chutes documents an OpenAI-compatible endpoint at `https://llm.chutes.ai/v1`.

```json
{
  "provider_id": "chutes-prod",
  "provider_kind": "chutes",
  "endpoint_url": "https://llm.chutes.ai/v1",
  "payload_style": "responses",
  "auth_header": "Authorization",
  "auth_scheme": "Bearer",
  "extra_headers": {},
  "timeout_seconds": 30
}
```

If you are targeting a Chutes deployment that expects a different OpenAI-compatible path, set `endpoint_url` accordingly.

## Mock

The mock provider is for local development only.

```json
{
  "provider_id": "mock",
  "provider_kind": "mock",
  "endpoint_url": null
}
```

## Example call payload

Once registered, the service can route a request through a provider-specific endpoint:

```json
{
  "request_id": "req_001",
  "provider_id": "openrouter-prod",
  "caller_id": "minirouter-maintainer",
  "owner_id": "minirouter-miners",
  "key_id": "minirouter-miners-openrouter-key",
  "model": "qwen/qwen3-coder-30b-a3b-instruct",
  "messages": [
    {"role": "system", "content": "You are a proof engine."},
    {"role": "user", "content": "Summarize the billing policy."}
  ],
  "parameters": {
    "temperature": 0.0,
    "max_output_tokens": 256
  },
  "nonce": "nonce-001",
  "expires_at": "2026-07-14T18:00:00+00:00"
}
```
