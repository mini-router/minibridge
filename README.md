# minibridge

Minibridge is a proof-and-metering service for LLM API calls.

It sits between a caller and an upstream LLM provider, keeps the API key inside a trusted boundary, executes the call, returns the response to the caller, and emits a tamper-evident receipt that proves the request, the response, and the billable usage.

The cost bearer is the provider / key owner, not the caller. The caller submits work; Minibridge proves what was spent.

The service boundary is narrow:

- it receives a request for an LLM call,
- keeps the API key inside a trusted boundary,
- forwards the call to the model provider,
- records the exact request/response metadata and token usage,
- computes a cost from a pinned pricing table,
- signs a tamper-evident receipt.

The current code is TEE-ready, but it is not tied to a specific hardware enclave yet. The design is meant to be moved behind a TEE or HSM later.

The service is split into two layers:

- the untrusted outer service: HTTP, orchestration, storage, admin flow
- the trusted inner boundary: API key handling, LLM provider calls, receipt signing, attestation evidence

The HTTP surface supports both generic and provider-specific calls:

- `POST /call`
- `POST /providers/{provider_id}/call`
- `GET /providers`
- `POST /register-provider`

## What it proves

- the request hash
- the response hash
- the model name and parameters
- token usage returned by the provider
- the computed cost under a pinned pricing table
- that the receipt was signed by the service
- one-time request IDs, expiry times, and caller allowlists
- a typed key policy rather than ad hoc enrollment fields

## What it does not prove

- that the provider independently signed the same bill
- that the agent was correct outside the service boundary
- that the host machine was trustworthy unless the service runs inside a real TEE

## Repo layout

- `src/llm_api_proof/models.py` — request, response, usage, and receipt dataclasses
- `src/llm_api_proof/pricing.py` — model pricing tables and cost computation
- `src/llm_api_proof/signing.py` — Ed25519 signing utilities for receipts
- `src/llm_api_proof/service.py` — the proof service that records and signs calls
- `src/llm_api_proof/verifier.py` — offline receipt verification
- `src/llm_api_proof/attestation.py` — pluggable attestation providers for mock or real TEE evidence
- `src/llm_api_proof/http_server.py` — a minimal stdlib HTTP interface for the service
- `src/llm_api_proof/provider.py` — provider registry, mock backend, and OpenAI-compatible backend helpers
- `src/llm_api_proof/state.py` — JSON persistence for providers, keys, and receipts in prototype mode
- `examples/mock_run.py` — end-to-end demo using the mock provider
- `examples/http_demo.py` — end-to-end demo using the HTTP service
- `docs/provider-registration.md` — provider registration payloads for OpenAI, OpenRouter, Chutes, and mock
- `configs/minibridge.demo.json` — demo bootstrap config for local or container use
- `Dockerfile` and `docker-compose.yml` — containerized demo runtime
- `web/` — separate frontend app boundary for the Minibridge dashboard

## Run the demo

```bash
python3 examples/mock_run.py
```

The demo prints a signed receipt and verifies it locally.

To run the HTTP service demo:

```bash
python3 examples/http_demo.py
```

## CLI

Install the project in editable mode and use the `minibridge` command:

```bash
pip install -e .
minibridge --help
```

Run a local service with a generated signing key:

```bash
minibridge serve
```

That writes a private signing key file and a matching public key file, then starts the HTTP service.

By default it also writes `.minibridge-state.json`, which persists providers, keys, and receipts across restarts. Use `--state-file ""` to disable persistence.

Register a provider and a key against a running service:

```bash
minibridge providers add --payload - --server http://127.0.0.1:8080
minibridge keys add --payload - --server http://127.0.0.1:8080
```

The provider payload examples are in [docs/provider-registration.md](docs/provider-registration.md).

Submit a request through the service:

```bash
minibridge call --payload docs/request.json --server http://127.0.0.1:8080
```

Verify a receipt offline:

```bash
minibridge verify --receipt docs/receipt.json --public-key-file .minibridge-signing.key.pub
```

For unattended startup, use `minibridge serve --config <file>.json` with a bootstrap file that includes `pricing_table`, optional `providers`, and optional `keys`.

For a containerized demo, run:

```bash
docker compose up --build
```

That starts Minibridge on `http://127.0.0.1:8080` using `configs/minibridge.demo.json` and persists state under `./data`.

The web UI lives under `web/` and talks only to the HTTP API. It can be run independently of the Python package.

With the included compose stack, the API is published on `http://127.0.0.1:18080` and the UI on `http://127.0.0.1:5173`.

## Limitation that matters

The receipt proves metered usage observed by the trusted service. It does not prove the upstream provider’s internal invoice unless the provider participates in the protocol.

The attestation layer is intentionally abstracted so you can start with `MockAttestationProvider` and later swap in a real TEE attestation backend without changing the service API or receipt format.

The attestation record is structured, not just a free-form blob. In the current MVP it carries:

- attestation mode
- backend identifier
- service instance ID
- context hash
- optional measurement / quote / report-data fields
- extra claims

The service also enforces a basic anti-replay boundary:

- `request_id` is accepted once per `(owner_id, key_id)`
- optional `expires_at` rejects stale calls
- optional `allowed_callers` limits which agent or validator may spend a key
- `KeyPolicy` is the enrollment object used by the service and HTTP API

OpenRouter and Chutes are documented as OpenAI-compatible surfaces, and OpenAI itself publishes the official API reference. In this repo, the provider registry treats them as OpenAI-compatible backends with configurable endpoint URLs and payload styles rather than hard-coding one transport shape for every vendor.

See [docs/provider-registration.md](docs/provider-registration.md) for copy-pastable `POST /register-provider` payloads and a provider-specific call example.

## Implementation roadmap

1. Lock the public API.

   Freeze the request, receipt, provider, and policy schemas so downstream users can rely on them.

2. Harden the trusted boundary.

   Move the key store, provider call path, and receipt signer into a real TEE-backed runtime and wire attestation verification into enrollment.

3. Add provider-specific adapters.

   Keep OpenAI, OpenRouter, and Chutes as explicit backend profiles with clear request shaping and response normalization.

4. Add settlement primitives.

   Track per-key spend, per-caller policy, expiry, and revocation so receipts can be used for reimbursement or audit.

5. Add operator tooling.

   Provide a CLI and small admin surface for provider registration, key enrollment, and receipt inspection.

6. Make the caller-facing path explicit.

   The caller should receive the LLM response through Minibridge, not by talking to the upstream provider directly.

7. Add failure-mode tests.

   Cover replay, bypass, stale requests, bad provider config, provider outages, and attestation mismatch.

That is still useful for:

- reimbursement
- validator settlement
- audit trails
- metered API usage proof

The caller-facing path is explicit now: the caller submits a request to Minibridge and receives the response back from Minibridge, along with the receipt that proves the billable call.
