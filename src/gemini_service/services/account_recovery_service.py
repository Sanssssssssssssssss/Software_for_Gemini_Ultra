from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..core.browser_cookie_sync import (
    BrowserLoginSession,
    CookiePairCandidate,
    CookieBundle,
    get_cookie_cache_path,
    iter_cookie_pair_candidates,
    write_cookie_bundle_cache,
)
from ..core.config import Settings
from ..schemas.accounts import AccountConfig, AccountInventory, AccountInventoryIssue, load_account_inventory, save_account_inventory
from ..schemas.common import AccountSummary
from .account_pool import AccountPool
from .persistent_browser_manager import PersistentBrowserManager


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
    def __init__(
        self,
        *,
        settings: Settings,
        pool: AccountPool,
        browser_manager: PersistentBrowserManager | None = None,
    ) -> None:
        self.settings = settings
        self.pool = pool
        self.browser_manager = browser_manager

    @property
    def accounts_path(self) -> Path:
        return Path(self.settings.accounts_config_path)

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
        if not account.cookie_source_profile_dir and self.browser_manager is None:
            return AccountRecoveryResult(
                account_id=account_id,
                status="failed",
                code="profile_missing",
                detail="This account has no persistent browser profile configured for reauthentication.",
                browser=browser_name,
                action="Set cookie_source_profile_dir for this account in Admin before retrying reauth.",
            )
        profile_dir = self.browser_manager.resolve_profile_dir(account) if self.browser_manager else Path(account.cookie_source_profile_dir or "")

        if browser_session is not None:
            return await self._recover_from_browser_session(
                account=account,
                browser=browser_name,
                profile_dir=profile_dir,
                session=browser_session,
                base_detail=base_detail,
                recovery_source="browser_session",
            )

        if self.browser_manager is None:
            return AccountRecoveryResult(
                account_id=account_id,
                status="failed",
                code="browser_manager_disabled",
                detail="Persistent browser management is disabled for this deployment.",
                browser=browser_name,
                profile_dir=str(profile_dir),
                action="Enable GEMINI_SERVICE_BROWSER_MANAGER_ENABLED or reconfigure cookie sync.",
            )

        live_session = await self.browser_manager.attach_existing_session(account)
        if live_session is not None:
            return await self._recover_from_browser_session(
                account=account,
                browser=browser_name,
                profile_dir=profile_dir,
                session=live_session,
                base_detail=base_detail,
                recovery_source="live_browser_session",
            )

        if not allow_browser_launch:
            return AccountRecoveryResult(
                account_id=account_id,
                status="failed",
                code="browser_session_offline",
                detail=self._join_detail(base_detail, "No live browser session is currently available for this account."),
                browser=browser_name,
                profile_dir=str(profile_dir),
                action="Use Admin reauthentication to launch or focus the managed browser for this account.",
            )

        try:
            session = await self.browser_manager.ensure_session(account)
        except Exception as exc:
            return AccountRecoveryResult(
                account_id=account_id,
                status="failed",
                code="browser_launch_failed",
                detail=self._join_detail(base_detail, "Could not start or attach the managed browser session."),
                browser=browser_name,
                profile_dir=str(profile_dir),
                action=f"Verify the configured browser path and profile permissions, then retry. Details: {exc}",
            )

        return AccountRecoveryResult(
            account_id=account_id,
            status="awaiting_login",
            code="interactive_login_required",
            detail=self._join_detail(base_detail, "Managed browser is ready. Finish Gemini login there and the service will continue automatically."),
            browser=browser_name,
            profile_dir=str(profile_dir),
            recovery_source="browser_launch",
            launched=True,
            action="Open gemini.google.com/app in the managed browser and make sure chat works there.",
            session=session,
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
            if not account.cookie_source_profile_dir and self.browser_manager is None:
                continue
            result = await self.recover_account(
                account.account_id,
                allow_browser_launch=self.settings.browser_manager_enabled,
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
        recovery_source: str,
    ) -> AccountRecoveryResult:
        try:
            if self.browser_manager is not None:
                bundle = await self.browser_manager.collect_cookie_bundle(account)
            else:
                raise RuntimeError("persistent browser manager is unavailable")
        except Exception:
            if self.browser_manager is not None:
                await self.browser_manager.record_error(account.account_id, "gemini_not_logged_in", state="online")
            return AccountRecoveryResult(
                account_id=account.account_id,
                status="awaiting_login",
                code="gemini_not_logged_in",
                detail=self._join_detail(base_detail, "Waiting for a valid Gemini session in the launched browser."),
                browser=browser,
                profile_dir=str(profile_dir),
                recovery_source=recovery_source,
                launched=True,
                action="Open gemini.google.com/app in the launched browser and make sure chat can answer one message.",
                session=session,
            )

        result = await self._validate_and_commit(
            account=account,
            browser=browser,
            profile_dir=profile_dir,
            bundle=bundle,
            recovery_source=recovery_source,
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
        cache_path = get_cookie_cache_path(bundle.secure_1psid)
        previous_cache = cache_path.read_text(encoding="utf-8") if cache_path.exists() else None
        write_cookie_bundle_cache(bundle)
        candidate = None
        probe_summary = None
        pairs = iter_cookie_pair_candidates(bundle)
        if not pairs and bundle.secure_1psid and bundle.secure_1psidts:
            pairs = [
                CookiePairCandidate(
                    secure_1psid=bundle.secure_1psid,
                    secure_1psidts=bundle.secure_1psidts,
                    psid_domain=None,
                    psidts_domain=None,
                )
            ]
        for pair in pairs:
            next_candidate = account.model_copy(
                update={
                    "secure_1psid": pair.secure_1psid,
                    "secure_1psidts": pair.secure_1psidts,
                }
            )
            next_summary = await self.pool.probe_candidate(next_candidate)
            if self._summary_is_recovered(next_summary):
                candidate = next_candidate
                probe_summary = next_summary
                break
            if probe_summary is None:
                probe_summary = next_summary
        if probe_summary is None:
            raise RuntimeError("No candidate probe result was produced during cookie validation.")
        if not self._summary_is_recovered(probe_summary):
            if previous_cache is None:
                cache_path.unlink(missing_ok=True)
            else:
                cache_path.write_text(previous_cache, encoding="utf-8")
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
            secure_1psid=candidate.secure_1psid,
            secure_1psidts=candidate.secure_1psidts,
            recovery_source=recovery_source,
        )
        cookie_count = len(bundle.cookies)
        await self.pool.sync_inventory(force_refresh=True)
        runtime = await self.pool.refresh_account(account.account_id)
        summary = runtime.summary()
        if not self._summary_is_recovered(summary):
            if self.browser_manager is not None and bundle.cookies:
                await self.browser_manager.record_cookie_sync_result(
                    account.account_id,
                    bundle=bundle,
                    provider_status=summary.account_status,
                    runtime_state=summary.state,
                    success=False,
                    error="provider_refresh_failed",
                )
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

        if self.browser_manager is not None:
            await self.browser_manager.record_cookie_sync_result(
                account.account_id,
                bundle=bundle,
                provider_status=summary.account_status,
                runtime_state=summary.state,
                success=True,
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
