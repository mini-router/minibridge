# Minibridge web UI

This directory is a separate frontend app boundary.

It talks only to the Minibridge HTTP API.

For the operator and proof workflow, see [../docs/usage.md](../docs/usage.md).

## Run locally

```bash
cd web
npm install
npm run dev
```

Set the host API base URL with:

```bash
VITE_MINIBRIDGE_HOST_URL=http://127.0.0.1:7070 npm run dev
```

The app defaults to `/api`, which works when it is served behind the included nginx proxy in Docker.

For the containerized frontend:

```bash
docker compose up --build
```

## What it covers

- host health
- runner enrollment and listing
- job submission
- bundle manifest inspection
- bundle verification
