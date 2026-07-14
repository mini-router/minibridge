# Minibridge web UI

This directory is a separate frontend app boundary.

It talks only to the Minibridge HTTP API.

## Run locally

```bash
cd web
npm install
npm run dev
```

Set the API base URL with:

```bash
VITE_MINIBRIDGE_API_URL=http://127.0.0.1:8080 npm run dev
```

The app defaults to `/api`, which works when it is served behind the included nginx proxy in Docker.

For the containerized frontend:

```bash
docker compose up --build
```

## What it covers

- service health
- provider enrollment and listing
- key enrollment
- caller request submission
- receipt verification
- receipt browsing
