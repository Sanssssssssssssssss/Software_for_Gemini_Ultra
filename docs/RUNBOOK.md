# Operations Runbook

## Normal checks

- `GET /healthz`: process liveness
- `GET /readyz`: readiness based on auth config, account inventory, and ready account count
- `GET /metrics`: request and runtime metrics
- `/admin`: account pool and recent session visibility
- `/ui/api/admin/dashboard`: admin control console data for account inventory, browser sessions, reauth jobs, sessions, and assets
- `/ui/api/admin/accounts`: add or update managed account inventory entries
- `/ui/api/admin/accounts/{account_id}/reauth`: launch or reuse a persistent browser reauthentication job
- `/ui/api/admin/reauth-jobs/{job_id}/complete`: force an immediate provider-validated sync attempt for a running browser job
- `/ui/api/admin/accounts/{account_id}/browser/{action}`: start, focus, sync, stop, pause, or resume a managed browser session
- `/ui/api/admin/sessions/{session_id}/export`: export a single session as JSON or Markdown
- `POST /v1/admin/accounts/{account_id}/actions/{action}`: admin-only runtime actions
- `POST /v1/batches`: persisted background batch execution
- `POST /v1/uploads`: controlled asset ingest for multimodal requests
- `GET /v1/assets/{asset_id}`: asset metadata and ownership checks

## Incident: account shows `reauth_required`

Likely causes:

- cookie expired
- Google invalidated the session
- account requires ToS acceptance or re-login

Actions:

1. Open `/admin` and start reauth for the affected account.
2. The service first tries to reuse that account's already-running managed browser session.
3. If none is available, it launches the same dedicated account profile and keeps that browser open.
4. Finish the Google / Gemini login in that browser and confirm `gemini.google.com/app` can answer one message.
5. Watch the reauth job move through `awaiting_login -> collecting_cookies -> validating_provider -> completed`.
6. Confirm `/v1/accounts` and `/admin` show the account as `ready` or `degraded`.

Important notes:

- Cancelling a reauth job only stops polling; it does not close the managed browser window.
- Use the explicit browser `stop` action only when you truly want to terminate that account's browser session.
- Inventory writes now happen only after provider validation succeeds. A bad cookie capture must not overwrite the last known good inventory state.

## Incident: startup warns about browser autosync

Likely causes:

- Chrome or Edge is not installed in a standard location on this machine
- the configured browser profile directory does not exist
- the service user cannot read that browser profile
- the managed browser registry path is not writable

Actions:

1. Run `python scripts/doctor.py --env-file .env`.
2. Inspect these checks:
   - `cookie_autosync_browser:*`
   - `cookie_autosync_profile:*`
   - `browser_profile_root`
   - `browser_state_path`
3. If the browser is installed in a non-standard path, set `cookie_source_browser_path` for the affected account.
4. If the profile directory is wrong or missing, use the Admin console to save the correct dedicated profile, or run `python scripts/playwright_bootstrap.py --account-id <account_id>`.
5. If you need the service up immediately, start with `python scripts/run_local.py --env-file .env --skip-cookie-sync`.

## Incident: upload succeeds locally but message send fails

Likely causes:

- provider rejected the file after local upload
- session ownership mismatch
- file count or MIME policy violation

Actions:

1. Inspect application logs for `upload_completed`, `provider_submit_started`, and `provider_submit_failed`.
2. Verify the asset still shows `status=available` through `GET /v1/assets/{asset_id}`.
3. Confirm the same `owner_subject` is sending the message and reading the session.
4. If the upload is no longer needed, allow TTL cleanup or delete the orphaned asset during maintenance.

## Incident: asset download returns `asset_not_available`

Likely causes:

- asset expired by TTL cleanup
- orphan cleanup removed an unbound upload
- operator manually removed the file from storage

Actions:

1. Check `/admin` recent file activity and `/metrics` asset cleanup counters.
2. Confirm whether the asset `expires_at` timestamp has elapsed.
3. Ask the user to upload the file again if the asset was intentionally temporary.
4. If cleanup happened too aggressively, increase `GEMINI_SERVICE_ASSET_DEFAULT_TTL_HOURS` or `GEMINI_SERVICE_ASSET_ORPHAN_GRACE_HOURS`.

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
