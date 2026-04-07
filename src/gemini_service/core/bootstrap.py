from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ..schemas.accounts import AccountInventory
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


def _is_placeholder(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    return (
        normalized in _PLACEHOLDER_MARKERS
        or normalized.startswith("replace-")
        or "change-me" in normalized
    )


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
                action="Run `py scripts/bootstrap_local.py` to generate local config templates.",
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
        try:
            inventory = AccountInventory.model_validate(json.loads(accounts_path.read_text(encoding="utf-8")))
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
