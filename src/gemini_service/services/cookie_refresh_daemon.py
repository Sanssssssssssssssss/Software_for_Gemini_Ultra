from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ..core.config import Settings
from ..schemas.accounts import load_account_inventory
from .account_pool import AccountPool
from .account_recovery_service import AccountRecoveryService
from .persistent_browser_manager import PersistentBrowserManager


class CookieRefreshDaemon:
    def __init__(
        self,
        *,
        settings: Settings,
        pool: AccountPool,
        recovery_service: AccountRecoveryService,
        browser_manager: PersistentBrowserManager,
    ) -> None:
        self.settings = settings
        self.pool = pool
        self.recovery_service = recovery_service
        self.browser_manager = browser_manager
        self.logger = logging.getLogger("gemini_service.cookie_refresh_daemon")
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._force_sync: set[str] = set()

    async def start(self) -> None:
        if not self.settings.browser_manager_enabled:
            return
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._closing = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def force_sync_now(self, account_id: str) -> None:
        self._force_sync.add(account_id)

    async def _run(self) -> None:
        while not self._closing:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.warning("cookie_refresh_tick_failed", extra={"event": "cookie_refresh_tick_failed", "error": str(exc)})
            await asyncio.sleep(1)

    async def _tick(self) -> None:
        inventory, _, _ = load_account_inventory(self.recovery_service.accounts_path, save_clean=True)
        now = datetime.now(timezone.utc)
        for account in inventory.accounts:
            entry = self.browser_manager.get_entry(account.account_id)
            if entry is not None and not entry.auto_refresh_enabled:
                continue
            if not account.cookie_source_profile_dir and not self.settings.browser_manager_enabled:
                continue

            if account.account_id in self._force_sync:
                self._force_sync.discard(account.account_id)
                await self._sync_account(account.account_id)
                continue

            interval = self.settings.browser_sync_interval_seconds
            runtime = self.pool.get_runtime(account.account_id)
            if runtime is not None and runtime.state.value in {"reauth_required", "blocked", "unavailable", "cooling_down"}:
                interval = self.settings.browser_recovery_interval_seconds
            if entry is not None and entry.state in {"awaiting_login", "error", "stale"}:
                interval = self.settings.browser_recovery_interval_seconds

            if entry is not None and entry.last_validated_at:
                try:
                    last = datetime.fromisoformat(entry.last_validated_at)
                    if (now - last).total_seconds() < interval:
                        continue
                except ValueError:
                    pass

            if entry is None:
                continue
            if entry.state not in {"online", "focused", "collecting_cookies", "error", "stale"}:
                continue

            await self._sync_account(account.account_id)

    async def _sync_account(self, account_id: str) -> None:
        result = await self.recovery_service.recover_account(
            account_id,
            allow_browser_launch=False,
        )
        self.logger.info(
            "cookie_refresh_account",
            extra={
                "event": "cookie_refresh_account",
                "account_id": account_id,
                "status": result.status,
                "code": result.code,
                "provider_status": result.provider_status,
                "runtime_state": result.runtime_state,
            },
        )
