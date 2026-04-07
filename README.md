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

Recommended first-run flow:

```sh
py scripts/bootstrap_local.py
py -m pip install -e .[dev]
py scripts/doctor.py
py -m uvicorn gemini_service.main:app --host 0.0.0.0 --port 8000
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
- `GET /v1/sessions/{id}/history`
- `GET /ui/login`
- `GET /ui/chat`
- `GET /admin`

UI authentication uses `.env` values from `GEMINI_SERVICE_UI_USERNAME` and `GEMINI_SERVICE_UI_PASSWORD`. API authentication uses `GEMINI_SERVICE_API_TOKENS`.
The current chat UI still exposes manual account selection for debugging, while automatic routing remains the preferred default behavior.
`/v1/batches` remains intentionally unimplemented in the current phase and still returns `501`.

## Documentation

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
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
