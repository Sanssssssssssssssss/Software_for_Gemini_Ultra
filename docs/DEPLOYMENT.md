# Deployment Guide

## Recommended baseline

The recommended first production baseline is Docker Compose with:

- one `app` container running FastAPI/Uvicorn
- one `postgres` container for durable session storage
- bind-mounted `config/accounts.json` for account inventory
- bind-mounted `data/` for runtime cookie artifacts
- bind-mounted asset storage directory for uploaded and generated media

## Pre-deployment checklist

1. Copy `.env.example` to `.env`.
2. Copy `config/accounts.example.json` to `config/accounts.json`.
3. Replace all placeholder values:
   - `GEMINI_SERVICE_API_TOKENS`
   - `GEMINI_SERVICE_UI_PASSWORD`
   - `GEMINI_SERVICE_UI_SESSION_SECRET`
   - all Gemini cookies in `config/accounts.json`
4. Confirm filesystem storage settings for multimodal assets:
   - `GEMINI_SERVICE_ASSET_STORAGE_BACKEND`
   - `GEMINI_SERVICE_ASSET_STORAGE_ROOT`
   - `GEMINI_SERVICE_ASSET_CLEANUP_INTERVAL_SECONDS`
   - `GEMINI_SERVICE_ASSET_ORPHAN_GRACE_HOURS`
   - `GEMINI_SERVICE_ASSET_TTL_HOURS`
5. Confirm the host can reach `gemini.google.com`.

## Local process launch

```sh
python -m pip install -e .
python -m uvicorn gemini_service.main:app --host 0.0.0.0 --port 8000
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
- `POST /v1/uploads` accepts a small image and `GET /v1/assets/{asset_id}` returns metadata
- uploaded files land under the configured controlled asset directory, not inside the database
- `/setup/status` reports passing or explainable warnings for `frontend_dist`, `asset_root`, `database_path`, and cookie autosync checks

## Pre-production offline verification

Before using a real Gemini account inventory, validate the service layer itself with mock accounts:

```sh
python scripts/validate_service.py
```

This exercises the real FastAPI app, scheduler, batch worker, auth boundaries, and metrics without requiring live Gemini cookies. Do not use mock accounts in production.

## Notes

- SQLite remains supported for local development, but PostgreSQL is the intended deployment database.
- Multimodal V1 supports image, PDF, and PPTX inputs; do not advertise video/audio support yet.
- Uploaded files and generated images are persisted in the configured storage backend and cleaned by a background TTL/orphan reaper.
- This repository's Docker artifacts were authored for production deployment, but they were not runtime-verified in the current environment because Docker was not installed on the build host.
- Prefer `python ...` commands in operator docs; do not assume a Windows-only `py` launcher exists.
- Cookie autosync now probes common Chrome and Edge locations on Windows, macOS, and Linux. If autosync is unavailable on a host, use `python scripts/run_local.py --skip-cookie-sync`.
