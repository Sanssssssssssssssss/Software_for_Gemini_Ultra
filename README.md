<p align="center">
    <img src="https://raw.githubusercontent.com/HanaokaYuzu/Gemini-API/master/assets/banner.png" width="55%" alt="Gemini Banner" align="center">
</p>

# Gemini Internal Service

This repository is a production-oriented fork of [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API), focused on turning the upstream reverse-engineered Python SDK into an internal multi-account Gemini service for LAN deployment.

> [!WARNING]
> This project is built on a non-official web wrapper around the Google Gemini web application. It is inherently more fragile than an official API integration. Expect breakage when Google changes the web app, cookies, RPC payloads, headers, or abuse controls.

## Project Status

- Current focus: production hardening and service layering on top of the upstream SDK.
- Target shape: authenticated internal API + web chat UI + multi-account pool + session persistence + observability + Docker deployment.
- License: AGPL-3.0. This fork remains AGPL-compatible because it is a derivative work of the upstream repository.

## Fork Notice

- Upstream project: [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API)
- Upstream license: AGPL-3.0
- This repository keeps the upstream codebase and incrementally adds service, operations, deployment, and reliability layers.
- See [NOTICE](NOTICE) for attribution details and [DISCLAIMER.md](DISCLAIMER.md) for deployment and operational risk statements.

## Production Roadmap

- Phase 0: repository bootstrap, legal notices, deployment intent, and documentation baseline
- Phase 1: FastAPI service skeleton, configuration, health endpoints, auth, and OpenAPI
- Phase 2: multi-account pool, scheduler, concurrency controls, queueing, retries, and circuit breaking
- Phase 3: durable sessions, chat API, streaming API, and session history
- Phase 4: internal web UI with chat, account selection, history, and admin visibility
- Phase 5: structured logging, metrics, traces, and admin operations views
- Phase 6: unit tests, integration tests, smoke tests, and load harness
- Phase 7: Docker packaging, deployment docs, runbooks, and hardening checklist

## Quickstart

The internal service lives in `src/gemini_service`. The current implementation already includes:

- health and readiness endpoints
- bearer token authentication
- multi-account inventory probing
- durable sessions and history
- internal LAN chat UI and admin page
- metrics, smoke scripts, and load test harness
- provider timeout handling and structured service errors
- isolated service tests that do not depend on local `.env` or `config/accounts.json`
- explicit account runtime states, bounded queueing, and backpressure
- sticky sessions with opt-in failover and persisted failover events
- persisted batch execution with background recovery on restart
- user-owned sessions and batches with admin/user permission boundaries
- mock provider validation mode for offline end-to-end verification
- React + TypeScript + Vite frontend for Login, Setup, Chat, and Admin, served by FastAPI after build
- Playwright E2E coverage for login, chat streaming, admin actions, and user/admin access boundaries

Recommended first-run flow:

```sh
python scripts/bootstrap_local.py
python -m pip install -e .[dev]
cd frontend && npm install && npm run build && cd ..
python scripts/doctor.py --env-file .env
python scripts/validate_service.py
python scripts/run_local.py --env-file .env
```

Offline local startup without real Gemini cookies:

```sh
python scripts/bootstrap_local.py --profile mock
python -m pip install -e .[dev]
cd frontend && npm install && npm run build && cd ..
python scripts/run_local.py --mock
```

Then:

1. Edit `config/accounts.json`
2. Replace placeholder Gemini cookies with real values
3. Open `http://127.0.0.1:8000/setup`
4. Fix any failed setup checks
5. Log in at `http://127.0.0.1:8000/ui/login`

Useful endpoints:

- `GET /healthz`
- `GET /readyz`
- `GET /docs`
- `GET /metrics`
- `GET /setup`
- `GET /setup/status`
- `GET /v1/accounts`
- `POST /v1/sessions`
- `POST /v1/messages`
- `POST /v1/messages:stream`
- `POST /v1/batches`
- `GET /v1/batches/{id}`
- `GET /v1/sessions/{id}/history`
- `GET /ui/login`
- `GET /ui/chat`
- `GET /admin`

UI authentication uses admin credentials from `GEMINI_SERVICE_UI_USERNAME` / `GEMINI_SERVICE_UI_PASSWORD` and can optionally enable a standard user login with `GEMINI_SERVICE_UI_USER_USERNAME` / `GEMINI_SERVICE_UI_USER_PASSWORD`.
API authentication uses `GEMINI_SERVICE_API_TOKENS`. Plain tokens remain backward compatible and are treated as admin tokens. Structured tokens use `subject|token|role`, for example `alice|token-1|user,bob|token-2|admin`.
The current chat UI defaults standard users to automatic routing. Manual account pinning is only exposed to administrators for debugging and recovery work.
For offline validation without real Gemini cookies, use [config/accounts.mock.json](config/accounts.mock.json) together with `python scripts/validate_service.py`.
For interactive cookie bootstrap without closing your main browser session, use `python scripts/playwright_bootstrap.py` to open a dedicated persistent browser profile and export fresh Gemini cookies into `config/accounts.json`.
If you bind an account to a persistent browser profile with `cookie_source_browser` and `cookie_source_profile_dir`, local startup can now auto-refresh Gemini cookies before the service boots. Enable it with `GEMINI_SERVICE_COOKIE_AUTOSYNC_ENABLED=true`, then keep using `python scripts/run_local.py --env-file .env`.
The React frontend is built from [`frontend/`](frontend/) and now owns Login, Setup, Chat, and Admin. Build it with `cd frontend && npm install && npm run build` before launching the FastAPI app so `/ui/login`, `/setup`, `/ui/chat`, and `/admin` all resolve to the new SPA.
For local HTTP startup, non-production environments such as `development`, `local`, `local-mock`, and `test` intentionally issue a non-`Secure` UI session cookie so browser logins work without HTTPS termination.
Run `cd frontend && npm run test:e2e` to execute the Playwright browser suite against the mock environment defined in [`config/e2e.mock.env`](config/e2e.mock.env).
The service now has a multimodal backend contract foundation: uploads land in controlled storage through `POST /v1/uploads`, messages can send either legacy `{message: "..."}` payloads or structured `parts`, and uploaded assets can be inspected through `GET /v1/assets/{asset_id}` and `GET /v1/assets/{asset_id}/content`. Text-only clients remain backward compatible.
Multimodal V1 currently supports `png`, `jpg`, `jpeg`, `webp`, `pdf`, and `pptx` inputs, plus text and generated-image outputs. Large binary assets are stored in controlled filesystem storage instead of the SQL database, and attachment-bearing turns default to `temporary=true` unless the caller explicitly opts out.

## Startup notes

- New session creation is now decoupled from immediate execution capacity. A healthy but busy account can still be assigned to a new sticky session; actual execution waits in the configured queue/backpressure path.
- Different sessions may send concurrently, queue, or route independently. The same session is intentionally single-flight: a second send while one request is still in progress now returns `session_busy`.
- `python scripts/doctor.py --env-file .env` and `/setup/status` now check more than secrets and cookies. They also report missing frontend builds, unwritable asset/database paths, and cookie autosync browser/profile issues.
- `python scripts/run_local.py --skip-cookie-sync` is the safe escape hatch when cookie autosync is unavailable on a given machine.

## Documentation

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- [docs/FRONTEND.md](docs/FRONTEND.md)
- [docs/FRONTEND_PERFORMANCE.md](docs/FRONTEND_PERFORMANCE.md)
- [docs/RUNBOOK.md](docs/RUNBOOK.md)
- [docs/HARDENING_CHECKLIST.md](docs/HARDENING_CHECKLIST.md)
- [docs/FAILURE_MODES.md](docs/FAILURE_MODES.md)
- [docs/TESTING.md](docs/TESTING.md)

## Operational Notes

- Use [config/accounts.example.json](config/accounts.example.json) as the inventory template.
- Default local persistence is SQLite; production deployment targets PostgreSQL.
- Docker and Compose assets are included, but container runtime verification still depends on the target environment.
- Because the underlying integration is unofficial, cookie rotation, account cooldown, and partial account failure should be treated as normal operating conditions.

## Upstream SDK

The upstream repository still provides the reverse-engineered Gemini Web SDK this service builds on. For lower-level SDK usage patterns, examples, and supported Gemini-web features, refer to:

- [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API)

## License

This fork remains AGPL-compatible due to its derivative relationship with the upstream project.
