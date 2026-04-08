# Operations Runbook

## Normal checks

- `GET /healthz`: process liveness
- `GET /readyz`: readiness based on auth config, account inventory, and ready account count
- `GET /metrics`: request and runtime metrics
- `/admin`: account pool and recent session visibility
- `POST /v1/admin/accounts/{account_id}/actions/{action}`: admin-only runtime actions
- `POST /v1/batches`: persisted background batch execution
- `POST /v1/uploads`: controlled asset ingest for multimodal requests
- `GET /v1/assets/{asset_id}`: asset metadata and ownership checks

## Incident: upload succeeds locally but message send fails

Likely causes:

- provider rejected the file after local upload
- session ownership mismatch
- file count or MIME policy violation

Actions:

1. Inspect application logs for `upload_completed`, `provider_submit_started`, and `provider_submit_failed`.
2. Verify the asset still shows `status=available` through `GET /v1/assets/{asset_id}`.
3. Confirm the same `owner_subject` is sending the message and reading the session.
4. If the upload is no longer needed, allow TTL cleanup or delete the orphaned asset from storage during maintenance.

## Incident: asset download returns `asset_not_available`

Likely causes:

- asset expired by TTL cleanup
- orphan cleanup removed an unbound upload
- operator manually removed the file from storage

Actions:

1. Check `/admin` recent file activity and `/metrics` asset cleanup counters.
2. Confirm whether the asset `expires_at` timestamp has elapsed.
3. Ask the user to upload the file again if the asset was intentionally temporary.
4. If cleanup happened too aggressively, increase `GEMINI_SERVICE_ASSET_TTL_HOURS` or `GEMINI_SERVICE_ASSET_ORPHAN_GRACE_HOURS`.

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

## Incident: startup warns about browser autosync

Likely causes:

- Chrome or Edge is not installed in a standard location on this machine
- the configured browser profile directory does not exist
- the service user cannot read that browser profile

Actions:

1. Run `python scripts/doctor.py --env-file .env` and inspect the `cookie_autosync_*` checks.
2. If the browser is installed in a non-standard path, set `cookie_source_browser_path` for the affected account.
3. If the profile directory is wrong or missing, re-run `python scripts/playwright_bootstrap.py` or update `cookie_source_profile_dir`.
4. Cookie autosync now also refreshes the upstream `gemini_webapi` cookie cache. If the service was already running with stale in-memory cookies, use the admin `refresh` action or restart the service after autosync completes.
5. If you need the service up immediately, start with `python scripts/run_local.py --env-file .env --skip-cookie-sync`.

## Incident: one session reports `session_busy`

Meaning:

- the same session already has an in-flight message send or stream

Actions:

1. Wait for the current turn to finish, or cancel it from the client side.
2. Use another session for parallel work. Different sessions can still route or queue independently.
3. If users frequently hit this, educate them that concurrency is per session and not per workspace.

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

## Incident: asset storage growth is accelerating

Actions:

1. Inspect recent assets in `/admin` and look for repeated large PDFs/PPTX files.
2. Confirm the cleanup worker is running by checking `gemini_service_asset_cleanup_runs_total`.
3. Review `gemini_service_asset_expired_total` and `gemini_service_asset_deleted_total`.
4. Tighten TTLs for temporary assets or move the storage root to larger dedicated disk.

## Incident: database unavailable

Actions:

1. Check PostgreSQL container health.
2. Confirm `GEMINI_SERVICE_DATABASE_URL`.
3. If necessary, restart `postgres` then `app`.
4. Validate session creation and history retrieval afterward.
