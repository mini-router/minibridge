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
- `POST /prove`
- `POST /providers/{provider_id}/call`
- `POST /providers/{provider_id}/prove`
- `GET /providers`
- `GET /proofs`
- `GET /proofs/{proof_id}`
- `POST /register-provider`

For a full usage walkthrough, see [docs/usage.md](docs/usage.md).

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

- `src/minibridge/core/` — shared domain objects: requests, receipts, proofs, pricing, signing, attestation, verification
- `src/minibridge/providers/` — provider registry and OpenAI-compatible provider adapters
- `src/minibridge/proof/` — the proof service and call/prove orchestration
- `src/minibridge/transport/` — HTTP server and request handlers
- `src/minibridge/app/` — CLI and local state persistence
- `src/llm_api_proof/` — compatibility package that re-exports the Minibridge modules
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

Capture a public proof bundle:

```bash
minibridge prove --payload docs/request.json --server http://127.0.0.1:8080
```

Create a SparkProof-style offline bundle from a running service:

```bash
minibridge bundle create --server http://127.0.0.1:8080 --bundle bundles/run-001
```

Verify that bundle on any CPU host:

```bash
minibridge bundle verify --bundle bundles/run-001
```

The HTTP API also exposes:

- `GET /bundle`
- `GET /bundle/export`
- `GET /bundle/manifest`

Verify a receipt offline:

```bash
minibridge verify --receipt docs/receipt.json --public-key-file .minibridge-signing.key.pub
```

You can also verify a proof bundle directly:

```bash
minibridge verify --proof docs/proof.json --public-key-file .minibridge-signing.key.pub
```

For unattended startup, use `minibridge serve --config <file>.json` with a bootstrap file that includes `pricing_table`, optional `providers`, and optional `keys`.

To run the host control plane on this machine:

```bash
minibridge host serve --host 0.0.0.0 --port 7070
```

To run a CPU-TEE runner inside Phala or another dstack CVM:

```bash
minibridge serve --host 0.0.0.0 --port 8000 --config configs/minibridge.runner.phala.json --state-file ""
```

The runner config supports `api_key_env` for secrets injected by the TEE runtime. The included Phala template expects `OPENROUTER_API_KEY` in the environment.
If you use the included Phala compose file, the container listens on `8000` but the public URL uses host port `18081`, for example:

```bash
curl https://<app-id>-18081.dstack-pha-prod9.phala.network/health
```

For a containerized demo, run:

```bash
docker compose up --build
```

That starts the host control plane on `http://127.0.0.1:18080` and the web UI on `http://127.0.0.1:5173`.

For a Phala-style runner container, use:

```bash
docker compose -f docker-compose.runner.yml up --build
```

The web UI lives under `web/` and talks only to the HTTP API. It can be run independently of the Python package.

With the included compose stack, the API is published on `http://127.0.0.1:18080` and the UI on `http://127.0.0.1:5173`.

See [docs/provider-registration.md](docs/provider-registration.md) for copy-pastable `POST /register-provider` payloads and a provider-specific call example.
See [docs/usage.md](docs/usage.md) for the end-to-end usage guide, including bundles and verification.
