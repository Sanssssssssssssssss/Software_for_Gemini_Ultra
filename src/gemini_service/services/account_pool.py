from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from gemini_webapi.constants import AccountStatus
from gemini_webapi.exceptions import APIError, AuthError, TemporarilyBlocked, TimeoutError

from ..adapters.base import AccountAdapter, AccountProbeResult
from ..adapters.gemini_web import GeminiWebAccountAdapter
from ..core.config import Settings
from ..core.errors import ServiceError
from ..schemas.accounts import AccountConfig, AccountInventory
from ..schemas.common import AccountSummary


class AccountRuntimeState(str, Enum):
    DISABLED = "disabled"
    INITIALIZING = "initializing"
    READY = "ready"
    COOLDOWN = "cooldown"
    AUTH_FAILED = "auth_failed"
    UNHEALTHY = "unhealthy"


@dataclass(slots=True)
class AccountRuntime:
    config: AccountConfig
    adapter: AccountAdapter
    state: AccountRuntimeState = AccountRuntimeState.INITIALIZING
    active_requests: int = 0
    queue_depth: int = 0
    cooldown_until: datetime | None = None
    last_error: str | None = None
    last_checked_at: datetime | None = None
    account_status: str | None = None
    account_status_code: int | None = None
    status_description: str | None = None
    models: list[str] = field(default_factory=list)
    consecutive_failures: int = 0
    semaphore: asyncio.Semaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.config.enabled:
            self.state = AccountRuntimeState.DISABLED
        self.semaphore = asyncio.Semaphore(self.config.max_concurrency)

    @property
    def available_slots(self) -> int:
        return max(0, self.config.max_concurrency - self.active_requests)

    def can_accept_requests(self) -> bool:
        return self.state == AccountRuntimeState.READY and self.available_slots > 0

    def summary(self) -> AccountSummary:
        return AccountSummary(
            account_id=self.config.account_id,
            state=self.state.value,
            account_status=self.account_status,
            status_description=self.status_description,
            models=self.models,
            active_requests=self.active_requests,
            queue_depth=self.queue_depth,
            configured_max_concurrency=self.config.max_concurrency,
            cooldown_until=self.cooldown_until.isoformat() if self.cooldown_until else None,
            last_error=self.last_error,
        )


class AccountLease:
    def __init__(self, pool: "AccountPool", runtime: AccountRuntime):
        self.pool = pool
        self.runtime = runtime

    async def __aenter__(self) -> AccountRuntime:
        await self.pool._global_semaphore.acquire()
        await self.runtime.semaphore.acquire()
        self.runtime.active_requests += 1
        return self.runtime

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.runtime.active_requests = max(0, self.runtime.active_requests - 1)
        self.runtime.semaphore.release()
        self.pool._global_semaphore.release()


class AccountPool:
    def __init__(
        self,
        settings: Settings,
        adapter_factory: Callable[[AccountConfig], AccountAdapter] | None = None,
    ):
        self.settings = settings
        self.adapter_factory = adapter_factory or GeminiWebAccountAdapter
        self._runtimes: dict[str, AccountRuntime] = {}
        self._refresh_lock = asyncio.Lock()
        self._last_refresh_at: datetime | None = None
        self._global_semaphore = asyncio.Semaphore(settings.global_max_concurrency)

    async def start(self) -> None:
        self._runtimes = self._load_inventory()
        await self.refresh_if_due(force=True)

    async def close(self) -> None:
        await asyncio.gather(
            *(runtime.adapter.close() for runtime in self._runtimes.values()),
            return_exceptions=True,
        )

    @property
    def inventory_count(self) -> int:
        return len(self._runtimes)

    @property
    def ready_account_count(self) -> int:
        return sum(runtime.state == AccountRuntimeState.READY for runtime in self._runtimes.values())

    def has_inventory(self) -> bool:
        return self.inventory_count > 0

    def get_runtime(self, account_id: str) -> AccountRuntime | None:
        return self._runtimes.get(account_id)

    async def refresh_if_due(self, force: bool = False) -> None:
        if not force and self._last_refresh_at is not None:
            age = datetime.now(timezone.utc) - self._last_refresh_at
            if age.total_seconds() < self.settings.account_probe_interval_seconds:
                return

        async with self._refresh_lock:
            if not force and self._last_refresh_at is not None:
                age = datetime.now(timezone.utc) - self._last_refresh_at
                if age.total_seconds() < self.settings.account_probe_interval_seconds:
                    return

            await asyncio.gather(
                *(self._refresh_runtime(runtime) for runtime in self._runtimes.values()),
                return_exceptions=False,
            )
            self._last_refresh_at = datetime.now(timezone.utc)

    async def list_account_summaries(self, force_refresh: bool = False) -> list[AccountSummary]:
        await self.refresh_if_due(force=force_refresh)
        return [runtime.summary() for runtime in self._runtimes.values()]

    async def choose_account(self, preferred_account_id: str | None = None) -> AccountRuntime:
        await self.refresh_if_due()
        candidates = [
            runtime
            for runtime in self._runtimes.values()
            if runtime.can_accept_requests()
        ]

        if preferred_account_id:
            preferred = self._runtimes.get(preferred_account_id)
            if preferred and preferred.can_accept_requests():
                return preferred

        if not candidates:
            raise ServiceError(
                status_code=503,
                code="no_ready_accounts",
                message="No Gemini account is currently ready to accept requests.",
            )

        candidates.sort(
            key=lambda runtime: (
                runtime.active_requests,
                runtime.consecutive_failures,
                runtime.config.account_id,
            )
        )
        return candidates[0]

    async def acquire(self, preferred_account_id: str | None = None) -> AccountLease:
        runtime = await self.choose_account(preferred_account_id=preferred_account_id)
        return AccountLease(self, runtime)

    def _load_inventory(self) -> dict[str, AccountRuntime]:
        path = Path(self.settings.accounts_config_path)
        if not path.exists():
            return {}

        payload = json.loads(path.read_text(encoding="utf-8"))
        inventory = AccountInventory.model_validate(payload)
        runtimes: dict[str, AccountRuntime] = {}
        for account in inventory.accounts:
            runtimes[account.account_id] = AccountRuntime(
                config=account,
                adapter=self.adapter_factory(account),
            )
        return runtimes

    async def _refresh_runtime(self, runtime: AccountRuntime) -> None:
        now = datetime.now(timezone.utc)
        runtime.last_checked_at = now

        if not runtime.config.enabled:
            runtime.state = AccountRuntimeState.DISABLED
            return

        if runtime.cooldown_until and runtime.cooldown_until > now:
            runtime.state = AccountRuntimeState.COOLDOWN
            return

        runtime.state = AccountRuntimeState.INITIALIZING
        try:
            probe = await runtime.adapter.probe()
        except AuthError as exc:
            self._mark_auth_failed(runtime, str(exc))
            return
        except (TemporarilyBlocked, TimeoutError, APIError) as exc:
            self._mark_cooldown(runtime, str(exc))
            return
        except Exception as exc:
            self._mark_unhealthy(runtime, str(exc))
            return

        self._apply_probe(runtime, probe)

    def _apply_probe(self, runtime: AccountRuntime, probe: AccountProbeResult) -> None:
        runtime.account_status = probe.account_status
        runtime.account_status_code = probe.account_status_code
        runtime.status_description = probe.status_description
        runtime.models = probe.models
        runtime.last_error = None
        runtime.cooldown_until = None
        runtime.consecutive_failures = 0

        code = probe.account_status_code
        if code == AccountStatus.AVAILABLE:
            runtime.state = AccountRuntimeState.READY
            return
        if code in {AccountStatus.UNAUTHENTICATED, AccountStatus.TOS_PENDING, AccountStatus.TOS_OUT_OF_DATE}:
            runtime.state = AccountRuntimeState.AUTH_FAILED
            runtime.last_error = probe.status_description
            return
        if code in {AccountStatus.ACCESS_TEMPORARILY_UNAVAILABLE, AccountStatus.ACCOUNT_UNTRUSTED}:
            self._mark_cooldown(runtime, probe.status_description)
            return

        runtime.state = AccountRuntimeState.UNHEALTHY
        runtime.last_error = probe.status_description

    def _mark_auth_failed(self, runtime: AccountRuntime, message: str) -> None:
        runtime.state = AccountRuntimeState.AUTH_FAILED
        runtime.last_error = message
        runtime.consecutive_failures += 1

    def _mark_unhealthy(self, runtime: AccountRuntime, message: str) -> None:
        runtime.state = AccountRuntimeState.UNHEALTHY
        runtime.last_error = message
        runtime.consecutive_failures += 1

    def _mark_cooldown(self, runtime: AccountRuntime, message: str) -> None:
        runtime.state = AccountRuntimeState.COOLDOWN
        runtime.last_error = message
        runtime.consecutive_failures += 1
        runtime.cooldown_until = datetime.now(timezone.utc) + timedelta(
            seconds=runtime.config.cooldown_seconds or self.settings.default_account_cooldown_seconds
        )
