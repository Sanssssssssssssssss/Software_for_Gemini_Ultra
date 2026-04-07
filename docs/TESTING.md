# Testing and Load Validation

## Automated tests

Run the service test suite locally:

```sh
py -m pytest tests/service -q
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
- provider timeout and error mapping
- metrics endpoint exposure
- isolated test execution independent of local `.env` or `config/accounts.json`

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

Recommended validation flow:

1. Run unit/integration tests.
2. Start the service with a small real account inventory.
3. Run `smoke_test.py`.
4. Run `load_test.py` with conservative settings.
5. Inspect `/metrics`, `/admin`, and provider-call logs during the run.
