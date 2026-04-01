# Deployment Guide

## Recommended baseline

The recommended first production baseline is Docker Compose with:

- one `app` container running FastAPI/Uvicorn
- one `postgres` container for durable session storage
- bind-mounted `config/accounts.json` for account inventory
- bind-mounted `data/` for runtime cookie artifacts

## Pre-deployment checklist

1. Copy `.env.example` to `.env`.
2. Copy `config/accounts.example.json` to `config/accounts.json`.
3. Replace all placeholder values:
   - `GEMINI_SERVICE_API_TOKENS`
   - `GEMINI_SERVICE_UI_PASSWORD`
   - `GEMINI_SERVICE_UI_SESSION_SECRET`
   - all Gemini cookies in `config/accounts.json`
4. Confirm the host can reach `gemini.google.com`.

## Local process launch

```sh
py -m pip install -e .
py -m uvicorn gemini_service.main:app --host 0.0.0.0 --port 8000
```

## Docker Compose launch

```sh
docker compose build
docker compose up -d
docker compose ps
```

## Post-deploy checks

```sh
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
curl http://127.0.0.1:8000/metrics
```

Then verify:

- `/ui/login` renders
- `/admin` shows account pool state after login
- `scripts/smoke_test.py` passes against the deployed service

## Notes

- SQLite remains supported for local development, but PostgreSQL is the intended deployment database.
- This repository's Docker artifacts were authored for production deployment, but they were not runtime-verified in the current environment because Docker was not installed on the build host.
