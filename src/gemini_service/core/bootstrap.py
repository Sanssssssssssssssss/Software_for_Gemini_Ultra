from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.engine import make_url

from .browser_cookie_sync import normalize_profile_dir, resolve_browser_path, validate_profile_dir
from ..schemas.accounts import load_account_inventory
from ..schemas.common import BootstrapCheck, BootstrapStatusResponse

if TYPE_CHECKING:
    from .config import Settings
    from ..services.account_pool import AccountPool


_PLACEHOLDER_MARKERS = {
    "",
    "change-me",
    "change-me-ui-password",
    "change-me-session-secret",
    "replace-me",
    "replace-me-if-required",
}
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _is_placeholder(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    return (
        normalized in _PLACEHOLDER_MARKERS
        or normalized.startswith("replace-")
        or "change-me" in normalized
    )


def _resolve_repo_path(path_value: str) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = (_REPO_ROOT / path).resolve()
    return path


def _check_directory_ready(path: Path, *, create: bool = True) -> tuple[bool, str]:
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True)
        elif not path.exists():
            return False, "path does not exist"
    except OSError as exc:
        return False, str(exc)

    if not path.exists():
        return False, "path does not exist"
    if not path.is_dir():
        return False, "path is not a directory"
    try:
        probe = path / ".gemini-service-write-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, str(exc)
    return True, "directory is readable and writable"


def _check_file_parent_ready(path: Path) -> tuple[bool, str]:
    return _check_directory_ready(path.parent, create=True)


def evaluate_bootstrap_status(
    settings: "Settings",
    pool: "AccountPool | None" = None,
) -> BootstrapStatusResponse:
    checks: list[BootstrapCheck] = []

    env_path = Path(".env")
    if env_path.exists():
        checks.append(BootstrapCheck(name="env_file", status="pass", detail=f"Environment file detected at {env_path.resolve()}"))
    else:
        checks.append(
            BootstrapCheck(
                name="env_file",
                status="fail",
                detail="No .env file was found.",
                action="Run `python scripts/bootstrap_local.py` to generate local config templates.",
            )
        )

    if not settings.require_auth:
        checks.append(
            BootstrapCheck(
                name="api_auth",
                status="warn",
                detail="API bearer auth is disabled. This is convenient for local validation but not recommended for shared LAN use.",
                action="Set `GEMINI_SERVICE_REQUIRE_AUTH=true` before rollout and configure bearer tokens.",
            )
        )
    elif not settings.api_token_values:
        checks.append(
            BootstrapCheck(
                name="api_auth",
                status="fail",
                detail="API auth is enabled but no bearer tokens are configured.",
                action="Set `GEMINI_SERVICE_API_TOKENS` in .env.",
            )
        )
    elif any(_is_placeholder(token) for token in settings.api_token_values):
        checks.append(
            BootstrapCheck(
                name="api_auth",
                status="fail",
                detail="API bearer auth still uses placeholder tokens.",
                action="Replace `GEMINI_SERVICE_API_TOKENS` with random high-entropy values.",
            )
        )
    else:
        checks.append(
            BootstrapCheck(
                name="api_auth",
                status="pass",
                detail=f"Configured {len(settings.api_token_values)} API bearer token(s).",
            )
        )

    if _is_placeholder(settings.ui_password):
        checks.append(
            BootstrapCheck(
                name="ui_password",
                status="fail",
                detail="The UI password still uses the default placeholder value.",
                action="Set `GEMINI_SERVICE_UI_PASSWORD` to a non-default secret.",
            )
        )
    else:
        checks.append(BootstrapCheck(name="ui_password", status="pass", detail="The UI password is no longer using the default placeholder."))

    if _is_placeholder(settings.ui_session_secret):
        checks.append(
            BootstrapCheck(
                name="ui_session_secret",
                status="fail",
                detail="The UI session signing secret still uses the default placeholder value.",
                action="Set `GEMINI_SERVICE_UI_SESSION_SECRET` to a non-default secret.",
            )
        )
    else:
        checks.append(BootstrapCheck(name="ui_session_secret", status="pass", detail="The UI session signing secret is no longer using the default placeholder."))

    accounts_path = Path(settings.accounts_config_path)
    if not accounts_path.exists():
        checks.append(
            BootstrapCheck(
                name="accounts_file",
                status="fail",
                detail=f"Account inventory file was not found at {accounts_path}.",
                action="Copy `config/accounts.example.json` to that path and fill in real Gemini cookies.",
            )
        )
    else:
        parent_ok, parent_detail = _check_file_parent_ready(accounts_path.resolve())
        checks.append(
            BootstrapCheck(
                name="accounts_path_permissions",
                status="pass" if parent_ok else "fail",
                detail=(
                    f"Account inventory directory is ready: {accounts_path.parent.resolve()}"
                    if parent_ok
                    else f"Account inventory directory is not writable: {parent_detail}"
                ),
                action=None if parent_ok else "Ensure the account inventory directory exists and is writable by the service user.",
            )
        )
        try:
            inventory, inventory_issues, _ = load_account_inventory(accounts_path.resolve(), save_clean=True)
        except Exception as exc:
            checks.append(
                BootstrapCheck(
                    name="accounts_file",
                    status="fail",
                    detail=f"Account inventory file could not be parsed: {exc}",
                    action="Fix the JSON structure so it contains a valid `accounts` array.",
                )
            )
        else:
            if inventory_issues:
                checks.append(
                    BootstrapCheck(
                        name="accounts_inventory_integrity",
                        status="warn",
                        detail="Account inventory contained invalid or duplicate entries that were ignored automatically.",
                        action="Review config/accounts.json and remove blank or duplicate account entries.",
                    )
                )
            if not inventory.accounts:
                checks.append(
                    BootstrapCheck(
                        name="accounts_file",
                        status="fail",
                        detail="The account inventory file exists, but the `accounts` array is empty.",
                        action="Configure at least one Gemini Web account before starting shared use.",
                    )
                )
            else:
                placeholder_accounts = [
                    account.account_id
                    for account in inventory.accounts
                    if _is_placeholder(account.secure_1psid) or _is_placeholder(account.secure_1psidts)
                ]
                if placeholder_accounts:
                    checks.append(
                        BootstrapCheck(
                            name="accounts_credentials",
                            status="fail",
                            detail="The following accounts still use placeholder cookies: " + ", ".join(placeholder_accounts),
                            action="Replace `secure_1psid` and `secure_1psidts` with real values.",
                        )
                    )
                else:
                    checks.append(
                        BootstrapCheck(
                            name="accounts_credentials",
                            status="pass",
                            detail=f"Detected {len(inventory.accounts)} configured account(s) with non-placeholder cookies.",
                        )
                    )

                if settings.cookie_autosync_enabled:
                    autosync_accounts = [
                        account for account in inventory.accounts if account.cookie_source_profile_dir
                    ]
                    if not autosync_accounts:
                        checks.append(
                            BootstrapCheck(
                                name="cookie_autosync",
                                status="warn",
                                detail="Cookie autosync is enabled, but no account has a persistent browser profile configured.",
                                action="Set cookie_source_profile_dir for an account or disable cookie autosync.",
                            )
                        )
                    for account in autosync_accounts:
                        browser = (account.cookie_source_browser or settings.cookie_autosync_browser).lower()
                        browser_path = resolve_browser_path(browser, account.cookie_source_browser_path)
                        if browser_path is None:
                            checks.append(
                                BootstrapCheck(
                                    name=f"cookie_autosync_browser:{account.account_id}",
                                    status="warn",
                                    detail=f"No {browser} executable could be found for autosync on this machine.",
                                    action="Install the browser, set cookie_source_browser_path, or disable autosync for this account.",
                                )
                            )
                            continue
                        profile_dir = normalize_profile_dir(accounts_path.resolve(), account.cookie_source_profile_dir or "")
                        valid_profile, _, profile_action = validate_profile_dir(profile_dir)
                        checks.append(
                            BootstrapCheck(
                                name=f"cookie_autosync_profile:{account.account_id}",
                                status="pass" if valid_profile else "warn",
                                detail=(
                                    f"Persistent browser profile is available for autosync: {profile_dir}"
                                    if valid_profile
                                    else "Configured browser profile directory is missing or unusable for autosync."
                                ),
                                action=None if valid_profile else profile_action,
                            )
                        )

    frontend_dist = _resolve_repo_path(settings.frontend_dist_path)
    if settings.ui_spa_enabled:
        if (frontend_dist / "index.html").exists():
            checks.append(
                BootstrapCheck(
                    name="frontend_dist",
                    status="pass",
                    detail=f"React frontend build detected at {frontend_dist}.",
                )
            )
        else:
            checks.append(
                BootstrapCheck(
                    name="frontend_dist",
                    status="warn",
                    detail=f"React frontend build was not found at {frontend_dist}. Legacy templates will be used instead.",
                    action="Run `cd frontend && npm install && npm run build` before handing the service to users.",
                )
            )

    asset_root = _resolve_repo_path(settings.asset_root_path)
    asset_ok, asset_detail = _check_directory_ready(asset_root, create=True)
    checks.append(
        BootstrapCheck(
            name="asset_root",
            status="pass" if asset_ok else "fail",
            detail=(
                f"Asset storage directory is ready at {asset_root}."
                if asset_ok
                else f"Asset storage directory is not writable: {asset_detail}"
            ),
            action=None if asset_ok else "Ensure GEMINI_SERVICE_ASSET_ROOT_PATH points to a writable directory.",
        )
    )

    if settings.browser_manager_enabled:
        browser_state_path = _resolve_repo_path(settings.browser_state_path)
        browser_profile_root = _resolve_repo_path(settings.browser_profile_root)
        profile_ok, profile_detail = _check_directory_ready(browser_profile_root, create=True)
        checks.append(
            BootstrapCheck(
                name="browser_profile_root",
                status="pass" if profile_ok else "fail",
                detail=(
                    f"Managed browser profile root is ready at {browser_profile_root}."
                    if profile_ok
                    else f"Managed browser profile root is not writable: {profile_detail}"
                ),
                action=None if profile_ok else "Ensure GEMINI_SERVICE_BROWSER_PROFILE_ROOT points to a writable directory.",
            )
        )
        state_ok, state_detail = _check_file_parent_ready(browser_state_path)
        checks.append(
            BootstrapCheck(
                name="browser_state_path",
                status="pass" if state_ok else "fail",
                detail=(
                    f"Managed browser registry path is ready: {browser_state_path}"
                    if state_ok
                    else f"Managed browser registry path is not writable: {state_detail}"
                ),
                action=None if state_ok else "Ensure GEMINI_SERVICE_BROWSER_STATE_PATH is writable by the service user.",
            )
        )

    try:
        database_url = make_url(settings.database_url)
    except Exception as exc:
        checks.append(
            BootstrapCheck(
                name="database_path",
                status="fail",
                detail=f"Database URL could not be parsed: {exc}",
                action="Set GEMINI_SERVICE_DATABASE_URL to a valid SQLAlchemy URL.",
            )
        )
    else:
        if database_url.get_backend_name().startswith("sqlite"):
            sqlite_path = Path(database_url.database or "")
            if not sqlite_path.is_absolute():
                sqlite_path = (_REPO_ROOT / sqlite_path).resolve()
            db_ok, db_detail = _check_file_parent_ready(sqlite_path)
            checks.append(
                BootstrapCheck(
                    name="database_path",
                    status="pass" if db_ok else "fail",
                    detail=(
                        f"SQLite database directory is ready at {sqlite_path.parent}."
                        if db_ok
                        else f"SQLite database directory is not writable: {db_detail}"
                    ),
                    action=None if db_ok else "Ensure the SQLite database directory exists and is writable, or switch to PostgreSQL.",
                )
            )

    if pool is not None:
        ready_accounts = pool.ready_account_count
        total_accounts = pool.inventory_count
        if total_accounts == 0:
            checks.append(
                BootstrapCheck(
                    name="runtime_readiness",
                    status="warn",
                    detail="The service is running but has not loaded any accounts.",
                    action="Populate the account inventory, restart the service, and re-check `/readyz`.",
                )
            )
        elif ready_accounts < settings.min_ready_accounts:
            checks.append(
                BootstrapCheck(
                    name="runtime_readiness",
                    status="warn",
                    detail=(
                        f"Only {ready_accounts}/{total_accounts} account(s) are currently ready. "
                        f"The minimum required count is {settings.min_ready_accounts}."
                    ),
                    action="Check whether cookies expired or wait for cooldown to end before retrying.",
                )
            )
        else:
            checks.append(
                BootstrapCheck(
                    name="runtime_readiness",
                    status="pass",
                    detail=f"{ready_accounts}/{total_accounts} account(s) are currently ready and satisfy startup requirements.",
                )
            )

    next_steps = [check.action for check in checks if check.status == "fail" and check.action]
    if not next_steps and any(check.status == "warn" for check in checks):
        next_steps = [check.action for check in checks if check.status == "warn" and check.action]

    return BootstrapStatusResponse(
        status="ready" if not any(check.status == "fail" for check in checks) else "needs_setup",
        setup_complete=not any(check.status == "fail" for check in checks),
        checks=checks,
        next_steps=next_steps,
        docs={"health": "/healthz", "readiness": "/readyz", "openapi": "/docs", "admin": "/admin"},
    )
