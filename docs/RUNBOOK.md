# Operations Runbook

## Normal checks

- `GET /healthz`: process liveness
- `GET /readyz`: readiness based on auth config, account inventory, and ready account count
- `GET /metrics`: request and runtime metrics
- `/admin`: account pool and recent session visibility
- `POST /v1/admin/accounts/{account_id}/actions/{action}`: admin-only runtime actions
- `POST /v1/batches`: persisted background batch execution

## Incident: account shows `reauth_required`

Likely causes:

- cookie expired
- Google invalidated the session
- account requires ToS acceptance or re-login

Actions:

1. Re-authenticate the affected Google account in a browser.
2. Update `config/accounts.json` with fresh `__Secure-1PSID` and `__Secure-1PSIDTS`.
3. Use the admin page or admin API action to refresh the runtime after the new cookies are in place.
4. Confirm `/v1/accounts` and `/admin` show the account as `ready` or `degraded`, then run a smoke test.

## Incident: account stuck in `cooling_down`

Likely causes:

- temporary throttling
- network timeout
- intermittent upstream failure

Actions:

1. Check `/metrics` for elevated request counts and errors.
2. Reduce offered load with the load harness disabled or lower concurrency.
3. Wait for `cooldown_until` to elapse, or use the admin action `clear-cooldown` when you have confirmed the provider recovered.
4. If repeated, reduce that account's `max_concurrency`.

## Incident: `no_ready_accounts`

Likely causes:

- every account is cooling down, unhealthy, or auth failed
- inventory file missing or empty

Actions:

1. Verify `config/accounts.json` is mounted in the right path.
2. Inspect `/admin` and `/v1/accounts`.
3. Refresh cookies or temporarily disable bad accounts.
4. Re-run `scripts/smoke_test.py` after remediation.

## Incident: batch stuck in `running`

Actions:

1. Query `GET /v1/batches/{id}` and inspect which item is still `pending` or `running`.
2. Check logs for `batch_item_failed`, `provider_call`, and `session_failover`.
3. If the service restarted mid-run, the in-process worker should resume pending/running batches automatically on startup.
4. If the same item keeps failing, inspect the target session ownership, account health, and recent provider errors.

## Incident: rising 5xx errors

Actions:

1. Inspect application logs with request IDs.
2. Check `/metrics` for total errors and request latency growth.
3. Verify database reachability.
4. Run a reduced-scope smoke test before restoring load.

## Incident: database unavailable

Actions:

1. Check PostgreSQL container health.
2. Confirm `GEMINI_SERVICE_DATABASE_URL`.
3. If necessary, restart `postgres` then `app`.
4. Validate session creation and history retrieval afterward.
