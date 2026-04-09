from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..core.browser_cookie_sync import (
    BrowserLoginSession,
    CookieBundle,
    collect_cookies_from_browser_session,
    extract_cookie_bundle_from_browser_session,
    extract_cookie_bundle_from_profile,
    launch_browser_login_session,
    normalize_profile_dir,
    resolve_browser_path,
    validate_profile_dir,
    write_cookie_bundle_cache,
)
from ..core.config import Settings
from ..schemas.accounts import AccountConfig, AccountInventory, AccountInventoryIssue, load_account_inventory, save_account_inventory
from ..schemas.common import AccountSummary
from .account_pool import AccountPool


@dataclass(slots=True)
class AccountRecoveryResult:
    account_id: str
    status: str
    code: str
    detail: str
    browser: str | None = None
    profile_dir: str | None = None
    recovery_source: str | None = None
    cookie_count: int | None = None
    provider_status: str | None = None
    runtime_state: str | None = None
    account_models: list[str] = field(default_factory=list)
    updated: bool = False
    launched: bool = False
    action: str | None = None
    session: BrowserLoginSession | None = None


class AccountRecoveryService:
    def __init__(self, *, settings: Settings, pool: AccountPool) -> None:
        self.settings = settings
        self.pool = pool

    async def recover_account(
        self,
        account_id: str,
        *,
        allow_browser_launch: bool,
        browser_session: BrowserLoginSession | None = None,
        browser: str | None = None,
    ) -> AccountRecoveryResult:
        inventory, issues = self.load_inventory()
        account = next((item for item in inventory.accounts if item.account_id == account_id), None)
        if account is None:
            return AccountRecoveryResult(
                account_id=account_id,
                status="failed",
                code="account_not_found",
                detail=f"Account {account_id} does not exist in the current inventory.",
                action="Check the account ID or recreate the account entry in Admin.",
            )

        if issues:
            # Inventory was cleaned, so call attention to remaining hygiene issues without blocking recovery.
            inventory_issue_codes = ", ".join(sorted({issue.code for issue in issues}))
            base_detail = f"Inventory hygiene issues were detected and sanitized automatically ({inventory_issue_codes})."
        else:
            base_detail = ""

        browser_name = (browser or account.cookie_source_browser or self.settings.cookie_autosync_browser).lower()
        browser_path = resolve_browser_path(browser_name, account.cookie_source_browser_path)
        if browser_path is None:
            return AccountRecoveryResult(
                account_id=account_id,
                status="failed",
                code="browser_not_found",
                detail="Could not find the configured browser executable on this machine.",
                browser=browser_name,
                action="Install the browser or set cookie_source_browser_path for this account.",
            )

        if not account.cookie_source_profile_dir:
            return AccountRecoveryResult(
                account_id=account_id,
                status="failed",
                code="profile_missing",
                detail="This account has no persistent browser profile configured for reauthentication.",
                browser=browser_name,
                action="Set cookie_source_profile_dir for this account in Admin before retrying reauth.",
            )

        profile_dir = normalize_profile_dir(Path(self.settings.accounts_config_path), account.cookie_source_profile_dir)
        valid_profile, profile_code, profile_action = validate_profile_dir(profile_dir)
        if not valid_profile and browser_session is None and not (
            allow_browser_launch and profile_code == "cookie_profile_missing"
        ):
            return AccountRecoveryResult(
                account_id=account_id,
                status="failed",
                code=profile_code,
                detail="The configured browser profile directory is missing or not accessible.",
                browser=browser_name,
                profile_dir=str(profile_dir),
                action=profile_action,
            )

        if browser_session is not None:
            return await self._recover_from_browser_session(
                account=account,
                browser=browser_name,
                profile_dir=profile_dir,
                session=browser_session,
                base_detail=base_detail,
            )

        try:
            bundle = extract_cookie_bundle_from_profile(
                browser=browser_name,
                profile_dir=profile_dir,
                browser_path=browser_path,
                start_url=self.settings.cookie_autosync_start_url,
                timeout_seconds=self.settings.cookie_autosync_timeout_seconds,
                headless=self.settings.cookie_autosync_headless,
            )
        except Exception as exc:
            if not allow_browser_launch:
                return AccountRecoveryResult(
                    account_id=account_id,
                    status="failed",
                    code="cookie_sync_failed",
                    detail=self._join_detail(base_detail, "Could not collect valid Gemini cookies from the configured browser profile."),
                    browser=browser_name,
                    profile_dir=str(profile_dir),
                    action=f"Log into Gemini in that browser profile and retry. Details: {exc}",
                )

            session = launch_browser_login_session(
                browser=browser_name,
                profile_dir=profile_dir,
                browser_path=browser_path,
                start_url=self.settings.cookie_autosync_start_url,
            )
            return AccountRecoveryResult(
                account_id=account_id,
                status="awaiting_login",
                code="interactive_login_required",
                detail=self._join_detail(base_detail, "Browser opened. Finish Gemini login in that browser and the service will continue automatically."),
                browser=browser_name,
                profile_dir=str(profile_dir),
                recovery_source="browser_launch",
                launched=True,
                action="Open gemini.google.com/app in the launched browser and make sure chat works there.",
                session=session,
            )

        return await self._validate_and_commit(
            account=account,
            browser=browser_name,
            profile_dir=profile_dir,
            bundle=bundle,
            recovery_source="profile_sync",
            base_detail=base_detail,
        )

    async def recover_accounts_for_startup(self) -> list[AccountRecoveryResult]:
        inventory, issues = self.load_inventory()
        results: list[AccountRecoveryResult] = []
        if issues:
            for issue in issues:
                results.append(
                    AccountRecoveryResult(
                        account_id=issue.account_id or "<inventory>",
                        status="failed",
                        code=issue.code,
                        detail=issue.detail,
                        action="Fix the accounts inventory so each enabled account has a unique non-empty account_id.",
                    )
                )

        for account in inventory.accounts:
            if not account.cookie_source_profile_dir:
                continue
            result = await self.recover_account(
                account.account_id,
                allow_browser_launch=False,
            )
            results.append(result)
        return results

    def load_inventory(self) -> tuple[AccountInventory, list[AccountInventoryIssue]]:
        inventory, issues, _ = load_account_inventory(Path(self.settings.accounts_config_path), save_clean=True)
        return inventory, issues

    async def _recover_from_browser_session(
        self,
        *,
        account: AccountConfig,
        browser: str,
        profile_dir: Path,
        session: BrowserLoginSession,
        base_detail: str,
    ) -> AccountRecoveryResult:
        try:
            bundle = extract_cookie_bundle_from_browser_session(
                session,
                timeout_seconds=self.settings.cookie_autosync_timeout_seconds,
            )
        except Exception:
            return AccountRecoveryResult(
                account_id=account.account_id,
                status="awaiting_login",
                code="gemini_not_logged_in",
                detail=self._join_detail(base_detail, "Waiting for a valid Gemini session in the launched browser."),
                browser=browser,
                profile_dir=str(profile_dir),
                recovery_source="browser_launch",
                launched=True,
                action="Open gemini.google.com/app in the launched browser and make sure chat can answer one message.",
                session=session,
            )

        result = await self._validate_and_commit(
            account=account,
            browser=browser,
            profile_dir=profile_dir,
            bundle=bundle,
            recovery_source="browser_session",
            base_detail=base_detail,
        )
        if result.status == "failed":
            result.status = "awaiting_login"
            result.action = "Keep the browser on gemini.google.com/app and confirm Gemini can answer one message."
        result.launched = True
        result.session = session
        return result

    async def _validate_and_commit(
        self,
        *,
        account: AccountConfig,
        browser: str,
        profile_dir: Path,
        bundle: CookieBundle,
        recovery_source: str,
        base_detail: str,
    ) -> AccountRecoveryResult:
        candidate = account.model_copy(
            update={
                "secure_1psid": bundle.secure_1psid,
                "secure_1psidts": bundle.secure_1psidts,
            }
        )
        probe_summary = await self.pool.probe_candidate(candidate)
        if not self._summary_is_recovered(probe_summary):
            return AccountRecoveryResult(
                account_id=account.account_id,
                status="failed",
                code="cookie_collected_but_provider_unauthenticated",
                detail=self._join_detail(base_detail, "Collected browser cookies, but Gemini still reports the account as unauthenticated."),
                browser=browser,
                profile_dir=str(profile_dir),
                recovery_source=recovery_source,
                provider_status=probe_summary.account_status,
                runtime_state=probe_summary.state,
                account_models=probe_summary.models,
                action="Open gemini.google.com/app in the configured browser profile, confirm chat works there, then retry reauth.",
            )

        updated = self._write_recovered_account(
            account_id=account.account_id,
            secure_1psid=bundle.secure_1psid,
            secure_1psidts=bundle.secure_1psidts,
            recovery_source=recovery_source,
        )
        cookie_count = write_cookie_bundle_cache(bundle)
        await self.pool.sync_inventory(force_refresh=True)
        runtime = await self.pool.refresh_account(account.account_id)
        summary = runtime.summary()
        if not self._summary_is_recovered(summary):
            return AccountRecoveryResult(
                account_id=account.account_id,
                status="failed",
                code="provider_refresh_failed",
                detail=self._join_detail(base_detail, "Cookies validated in isolation, but the live runtime did not recover after reload."),
                browser=browser,
                profile_dir=str(profile_dir),
                recovery_source=recovery_source,
                cookie_count=cookie_count,
                provider_status=summary.account_status,
                runtime_state=summary.state,
                account_models=summary.models,
                action="Retry reauthentication or refresh the account runtime from Admin.",
            )

        return AccountRecoveryResult(
            account_id=account.account_id,
            status="completed",
            code="recovery_completed",
            detail=self._join_detail(base_detail, "Cookies synced and Gemini is routable again."),
            browser=browser,
            profile_dir=str(profile_dir),
            recovery_source=recovery_source,
            cookie_count=cookie_count,
            provider_status=summary.account_status,
            runtime_state=summary.state,
            account_models=summary.models,
            updated=updated,
        )

    def _write_recovered_account(
        self,
        *,
        account_id: str,
        secure_1psid: str,
        secure_1psidts: str,
        recovery_source: str,
    ) -> bool:
        inventory, _, _ = load_account_inventory(Path(self.settings.accounts_config_path), save_clean=True)
        updated = False
        recovery_at = datetime.now(timezone.utc).isoformat()
        next_accounts = []
        for account in inventory.accounts:
            if account.account_id != account_id:
                next_accounts.append(account)
                continue
            updated = (
                account.secure_1psid != secure_1psid
                or account.secure_1psidts != secure_1psidts
                or account.last_recovery_source != recovery_source
            )
            next_accounts.append(
                account.model_copy(
                    update={
                        "secure_1psid": secure_1psid,
                        "secure_1psidts": secure_1psidts,
                        "last_recovery_at": recovery_at,
                        "last_recovery_source": recovery_source,
                    }
                )
            )
        save_account_inventory(
            Path(self.settings.accounts_config_path),
            AccountInventory(accounts=next_accounts).model_dump(mode="json"),
        )
        return updated

    def _summary_is_recovered(self, summary: AccountSummary) -> bool:
        return summary.state in {"ready", "degraded"} and summary.account_status == "AVAILABLE"

    def _join_detail(self, prefix: str, detail: str) -> str:
        if not prefix:
            return detail
        return f"{prefix} {detail}"
