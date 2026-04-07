# Testing and Load Validation

## Automated tests

Run the service test suite locally:

```sh
py -m pytest tests/service -q
```

Build the frontend before browser-level validation:

```sh
cd frontend
npm install
npm run build
```

Current coverage includes:

- health and readiness endpoints
- API bearer auth
- UI login and protected pages
- setup diagnostics and bootstrap redirects
- account pool selection and cooldown transitions
- queue backpressure, queue timeout, and FIFO admission
- durable chat session persistence
- idempotent message replay behavior
- sticky session behavior and opt-in failover
- batch execution and batch ownership enforcement
- admin/user UI permission boundaries
- provider timeout and error mapping
- metrics endpoint exposure
- isolated test execution independent of local `.env` or `config/accounts.json`
- frontend UI session-cookie behavior in local mock mode
- UI admin overview and async admin JSON actions

## Playwright E2E

The modern frontend ships with Playwright E2E coverage for the mock deployment path.

```sh
cd frontend
npm run test:e2e
```

These tests boot the backend with [`config/e2e.mock.env`](../config/e2e.mock.env) and verify:

- admin login into the React chat workspace
- standard-user redirect away from `/admin`
- streamed chat response in the new composer flow
- async admin action flow without leaving the admin page

## Smoke test

Use the smoke script against a running service with valid account cookies:

```sh
py scripts/smoke_test.py --base-url http://127.0.0.1:8000 --token change-me
```

The smoke script verifies:

- `/healthz`
- `/readyz`
- `/v1/accounts`
- session creation
- non-streaming message send
- session history retrieval

## Closed-loop validation

Use the validation orchestrator to boot the service against mock provider accounts, then run:

- health and readiness checks
- user/admin permission checks
- message send and batch execution
- admin runtime actions
- load matrix validation at multiple concurrency levels

```sh
py scripts/validate_service.py
```

The validator uses [config/accounts.mock.json](../config/accounts.mock.json) by default and does not require real Gemini cookies.

## Load test

Use the async load harness to exercise concurrent sessions and a mix of
streaming and non-streaming workloads:

```sh
py scripts/load_test.py --base-url http://127.0.0.1:8000 --token change-me --workers 8 --duration-seconds 60 --stream-ratio 0.4
```

Key knobs:

- `--workers`: concurrent virtual users
- `--duration-seconds`: total test duration
- `--stream-ratio`: share of requests that use `/v1/messages:stream`
- `--burst-size`: requests each worker sends before a short pause
- `--account-id`: pin all new sessions to a specific account
- `--session-mode`: `reuse`, `new`, or `mixed`
- `--new-session-ratio`: when using `mixed`, how often a worker opens a fresh session

Recommended validation flow:

1. Run unit/integration tests.
2. Build the frontend and run Playwright E2E against the mock environment.
3. Start the service with a small real account inventory.
4. Run `smoke_test.py`.
5. Run `load_test.py` with conservative settings.
6. Inspect `/metrics`, `/admin`, and provider-call logs during the run.
