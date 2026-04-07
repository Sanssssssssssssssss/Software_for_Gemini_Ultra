from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
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
    BUSY = "busy"
    DEGRADED = "degraded"
    COOLING_DOWN = "cooling_down"
    REAUTH_REQUIRED = "reauth_required"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


@dataclass(slots=True)
class AccountRuntime:
    config: AccountConfig
    adapter: AccountAdapter
    state: AccountRuntimeState = AccountRuntimeState.INITIALIZING
    operator_disabled: bool = False
    active_requests: int = 0
    cooldown_until: datetime | None = None
    last_error: str | None = None
    recent_errors: list[str] = field(default_factory=list)
    last_checked_at: datetime | None = None
    last_transition_at: datetime | None = None
    state_reason: str | None = None
    account_status: str | None = None
    account_status_code: int | None = None
    status_description: str | None = None
    models: list[str] = field(default_factory=list)
    consecutive_failures: int = 0
    waiters: deque[int] = field(default_factory=deque, repr=False)

    @property
    def available_slots(self) -> int:
        return max(0, self.config.max_concurrency - self.active_requests)

    @property
    def queue_depth(self) -> int:
        return len(self.waiters)

    @property
    def effective_state(self) -> AccountRuntimeState:
        if self.operator_disabled:
            return AccountRuntimeState.DISABLED
        if self.state in {AccountRuntimeState.READY, AccountRuntimeState.DEGRADED} and self.available_slots <= 0:
            return AccountRuntimeState.BUSY
        return self.state

    def is_routable(self) -> bool:
        return not self.operator_disabled and self.state in {AccountRuntimeState.READY, AccountRuntimeState.DEGRADED}

    def is_failover_required(self) -> bool:
        return self.state in {
            AccountRuntimeState.COOLING_DOWN,
            AccountRuntimeState.REAUTH_REQUIRED,
            AccountRuntimeState.BLOCKED,
            AccountRuntimeState.UNAVAILABLE,
        }

    def summary(self) -> AccountSummary:
        return AccountSummary(
            account_id=self.config.account_id,
            state=self.effective_state.value,
            account_status=self.account_status,
            status_description=self.status_description,
            models=self.models,
            active_requests=self.active_requests,
            queue_depth=self.queue_depth,
            configured_max_concurrency=self.config.max_concurrency,
            cooldown_until=self.cooldown_until.isoformat() if self.cooldown_until else None,
            last_error=self.last_error,
            recent_errors=list(self.recent_errors),
            failure_count=self.consecutive_failures,
            last_transition_at=self.last_transition_at.isoformat() if self.last_transition_at else None,
            state_reason=self.state_reason,
        )


@dataclass(slots=True)
class AccountSelection:
    runtime: AccountRuntime
    failover_from: str | None = None
    reason: str | None = None


class AccountLease:
    def __init__(self, pool: "AccountPool", selection: AccountSelection):
        self.pool = pool
        self.selection = selection
        self.runtime = selection.runtime

    async def __aenter__(self) -> AccountRuntime:
        await self.pool._acquire_slot(self.runtime)
        return self.runtime

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.pool._release_slot(self.runtime)


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
        self._condition = asyncio.Condition()
        self._last_refresh_at: datetime | None = None
        self._global_active_requests = 0
        self._ticket_counter = 0
        self.logger = logging.getLogger("gemini_service.account_pool")

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

    @property
    def total_queue_depth(self) -> int:
        return sum(runtime.queue_depth for runtime in self._runtimes.values())

    def has_inventory(self) -> bool:
        return self.inventory_count > 0

    def get_runtime(self, account_id: str) -> AccountRuntime | None:
        return self._runtimes.get(account_id)

    async def clear_cooldown(self, account_id: str) -> AccountRuntime:
        runtime = self._require_runtime(account_id)
        runtime.cooldown_until = None
        if runtime.state == AccountRuntimeState.COOLING_DOWN:
            self._set_state(runtime, AccountRuntimeState.DEGRADED, "cooldown cleared by operator")
        return runtime

    async def mark_reauth_required(self, account_id: str, reason: str) -> AccountRuntime:
        runtime = self._require_runtime(account_id)
        self._mark_reauth_required(runtime, reason)
        return runtime

    async def disable_runtime(self, account_id: str, reason: str) -> AccountRuntime:
        runtime = self._require_runtime(account_id)
        runtime.operator_disabled = True
        self._set_state(runtime, AccountRuntimeState.DISABLED, reason)
        return runtime

    async def enable_runtime(self, account_id: str) -> AccountRuntime:
        runtime = self._require_runtime(account_id)
        runtime.operator_disabled = False
        await self._refresh_runtime(runtime)
        return runtime

    async def refresh_account(self, account_id: str) -> AccountRuntime:
        runtime = self._require_runtime(account_id)
        await self._refresh_runtime(runtime)
        return runtime

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

    async def choose_account(
        self,
        preferred_account_id: str | None = None,
        require_preferred: bool = False,
        exclude_account_ids: set[str] | None = None,
    ) -> AccountRuntime:
        selection = await self._choose_new_session_account(
            preferred_account_id=preferred_account_id,
            require_preferred=require_preferred,
            exclude_account_ids=exclude_account_ids or set(),
        )
        return selection.runtime

    async def resolve_session_account(
        self,
        preferred_account_id: str,
        allow_failover: bool,
    ) -> AccountSelection:
        await self.refresh_if_due()
        preferred = self._runtimes.get(preferred_account_id)
        if preferred is None:
            raise ServiceError(
                status_code=503,
                code="preferred_account_unavailable",
                message=f"Preferred account {preferred_account_id} is not available in the inventory.",
            )

        if preferred.is_routable():
            return AccountSelection(runtime=preferred)

        if allow_failover and preferred.is_failover_required():
            fallback = await self._choose_new_session_account(
                preferred_account_id=None,
                require_preferred=False,
                exclude_account_ids={preferred_account_id},
            )
            return AccountSelection(
                runtime=fallback.runtime,
                failover_from=preferred_account_id,
                reason=f"preferred state is {preferred.effective_state.value}",
            )

        raise ServiceError(
            status_code=503,
            code="preferred_account_unavailable",
            message=f"Preferred account {preferred_account_id} is not ready for this session.",
            details={
                "account_id": preferred_account_id,
                "state": preferred.effective_state.value,
                "reason": preferred.state_reason or preferred.last_error or "",
            },
        )

    async def acquire(
        self,
        preferred_account_id: str | None = None,
        require_preferred: bool = False,
        allow_failover: bool = False,
    ) -> AccountLease:
        if preferred_account_id is None:
            selection = await self._choose_new_session_account(
                preferred_account_id=None,
                require_preferred=False,
                exclude_account_ids=set(),
            )
        elif require_preferred:
            selection = await self.resolve_session_account(
                preferred_account_id=preferred_account_id,
                allow_failover=allow_failover,
            )
        else:
            selection = await self._choose_new_session_account(
                preferred_account_id=preferred_account_id,
                require_preferred=False,
                exclude_account_ids=set(),
            )
        return AccountLease(self, selection)

    def record_provider_success(self, runtime: AccountRuntime) -> None:
        if runtime.state in {AccountRuntimeState.DISABLED, AccountRuntimeState.REAUTH_REQUIRED, AccountRuntimeState.BLOCKED}:
            return
        runtime.last_error = None
        runtime.cooldown_until = None
        if runtime.consecutive_failures > 0:
            runtime.consecutive_failures = max(0, runtime.consecutive_failures - 1)
        if runtime.consecutive_failures >= self.settings.degraded_failure_threshold:
            self._set_state(runtime, AccountRuntimeState.DEGRADED, "recovering after recent provider failures")
        else:
            self._set_state(runtime, AccountRuntimeState.READY, "provider call succeeded")

    def record_provider_failure(self, runtime: AccountRuntime, error: ServiceError) -> None:
        message = error.message
        runtime.last_error = message
        runtime.consecutive_failures += 1
        self._append_recent_error(runtime, f"{error.code}: {message}")

        if error.code == "provider_reauth_required":
            self._set_state(runtime, AccountRuntimeState.REAUTH_REQUIRED, message)
            return
        if error.code == "provider_blocked":
            self._set_state(runtime, AccountRuntimeState.BLOCKED, message)
            return
        if error.code == "provider_timeout":
            self._mark_degraded_or_unavailable(runtime, message)
            return
        self._mark_degraded_or_unavailable(runtime, message)

    def _mark_degraded_or_unavailable(self, runtime: AccountRuntime, message: str) -> None:
        if runtime.consecutive_failures >= self.settings.unavailable_failure_threshold:
            self._set_state(runtime, AccountRuntimeState.UNAVAILABLE, message)
            return
        self._set_state(runtime, AccountRuntimeState.DEGRADED, message)

    def _load_inventory(self) -> dict[str, AccountRuntime]:
        path = Path(self.settings.accounts_config_path)
        if not path.exists():
            return {}

        payload = json.loads(path.read_text(encoding="utf-8"))
        inventory = AccountInventory.model_validate(payload)
        runtimes: dict[str, AccountRuntime] = {}
        for account in inventory.accounts:
            runtime = AccountRuntime(
                config=account,
                adapter=self.adapter_factory(account),
            )
            if not account.enabled:
                runtime.state = AccountRuntimeState.DISABLED
                runtime.last_transition_at = datetime.now(timezone.utc)
                runtime.state_reason = "account disabled in inventory"
            runtimes[account.account_id] = runtime
        return runtimes

    async def _refresh_runtime(self, runtime: AccountRuntime) -> None:
        now = datetime.now(timezone.utc)
        runtime.last_checked_at = now

        if not runtime.config.enabled:
            self._set_state(runtime, AccountRuntimeState.DISABLED, "account disabled in inventory")
            return
        if runtime.operator_disabled:
            self._set_state(runtime, AccountRuntimeState.DISABLED, "account disabled by operator")
            return

        if runtime.cooldown_until and runtime.cooldown_until > now:
            self._set_state(
                runtime,
                AccountRuntimeState.COOLING_DOWN,
                f"cooldown until {runtime.cooldown_until.isoformat()}",
            )
            return

        self._set_state(runtime, AccountRuntimeState.INITIALIZING, "running account probe")
        try:
            probe = await runtime.adapter.probe()
        except AuthError as exc:
            self._mark_reauth_required(runtime, str(exc))
            return
        except TemporarilyBlocked as exc:
            self._mark_blocked(runtime, str(exc))
            return
        except (TimeoutError, APIError) as exc:
            self._mark_cooling_down(runtime, str(exc))
            return
        except Exception as exc:
            self._mark_unavailable(runtime, str(exc))
            return

        self._apply_probe(runtime, probe)

    def _apply_probe(self, runtime: AccountRuntime, probe: AccountProbeResult) -> None:
        runtime.account_status = probe.account_status
        runtime.account_status_code = probe.account_status_code
        runtime.status_description = probe.status_description
        runtime.models = probe.models
        runtime.last_error = None
        runtime.cooldown_until = None
        if runtime.consecutive_failures > 0:
            runtime.consecutive_failures = max(0, runtime.consecutive_failures - 1)

        code = probe.account_status_code
        if code == AccountStatus.AVAILABLE:
            if runtime.consecutive_failures >= self.settings.degraded_failure_threshold:
                self._set_state(runtime, AccountRuntimeState.DEGRADED, "probe succeeded after recent failures")
            else:
                self._set_state(runtime, AccountRuntimeState.READY, probe.status_description or "probe succeeded")
            return
        if code in {AccountStatus.UNAUTHENTICATED, AccountStatus.TOS_PENDING, AccountStatus.TOS_OUT_OF_DATE}:
            self._mark_reauth_required(runtime, probe.status_description)
            return
        if code == AccountStatus.ACCESS_TEMPORARILY_UNAVAILABLE:
            self._mark_cooling_down(runtime, probe.status_description)
            return
        if code == AccountStatus.ACCOUNT_UNTRUSTED:
            self._mark_blocked(runtime, probe.status_description)
            return

        self._mark_unavailable(runtime, probe.status_description)

    async def _choose_new_session_account(
        self,
        preferred_account_id: str | None,
        require_preferred: bool,
        exclude_account_ids: set[str],
    ) -> AccountSelection:
        await self.refresh_if_due()

        if preferred_account_id:
            preferred = self._runtimes.get(preferred_account_id)
            if preferred and preferred.is_routable() and preferred.available_slots > 0:
                return AccountSelection(runtime=preferred)
            if require_preferred:
                raise ServiceError(
                    status_code=503,
                    code="preferred_account_unavailable",
                    message=f"Preferred account {preferred_account_id} is not ready to accept new sessions.",
                    details={
                        "account_id": preferred_account_id,
                        "state": preferred.effective_state.value if preferred else "missing",
                    },
                )

        candidates = [
            runtime
            for runtime in self._runtimes.values()
            if runtime.config.account_id not in exclude_account_ids
            and runtime.is_routable()
            and runtime.available_slots > 0
        ]
        if not candidates:
            raise ServiceError(
                status_code=503,
                code="no_ready_accounts",
                message="No Gemini account is currently ready to accept new sessions.",
            )

        candidates.sort(
            key=lambda runtime: (
                0 if runtime.state == AccountRuntimeState.READY else 1,
                runtime.active_requests,
                runtime.queue_depth,
                runtime.consecutive_failures,
                runtime.config.account_id,
            )
        )
        return AccountSelection(runtime=candidates[0])

    async def _acquire_slot(self, runtime: AccountRuntime) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.queue_wait_timeout_seconds
        ticket_id: int | None = None

        async with self._condition:
            while True:
                if self._can_start_request(runtime) and (ticket_id is None or runtime.waiters[0] == ticket_id):
                    if ticket_id is not None and runtime.waiters and runtime.waiters[0] == ticket_id:
                        runtime.waiters.popleft()
                    self._global_active_requests += 1
                    runtime.active_requests += 1
                    self._condition.notify_all()
                    return

                if ticket_id is None:
                    if self._is_queue_full(runtime):
                        raise ServiceError(
                            status_code=429,
                            code="queue_full",
                            message=f"Queue is full for account {runtime.config.account_id}.",
                            details={
                                "account_id": runtime.config.account_id,
                                "queue_depth": runtime.queue_depth,
                                "global_queue_depth": self.total_queue_depth,
                            },
                        )
                    ticket_id = self._ticket_counter
                    self._ticket_counter += 1
                    runtime.waiters.append(ticket_id)

                remaining = deadline - loop.time()
                if remaining <= 0:
                    self._remove_waiter(runtime, ticket_id)
                    raise ServiceError(
                        status_code=504,
                        code="queue_timeout",
                        message=f"Timed out waiting for account {runtime.config.account_id} to accept the request.",
                        details={
                            "account_id": runtime.config.account_id,
                            "queue_depth": runtime.queue_depth,
                        },
                    )
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    self._remove_waiter(runtime, ticket_id)
                    raise ServiceError(
                        status_code=504,
                        code="queue_timeout",
                        message=f"Timed out waiting for account {runtime.config.account_id} to accept the request.",
                        details={
                            "account_id": runtime.config.account_id,
                            "queue_depth": runtime.queue_depth,
                        },
                    ) from None

    async def _release_slot(self, runtime: AccountRuntime) -> None:
        async with self._condition:
            runtime.active_requests = max(0, runtime.active_requests - 1)
            self._global_active_requests = max(0, self._global_active_requests - 1)
            self._condition.notify_all()

    def _can_start_request(self, runtime: AccountRuntime) -> bool:
        return (
            runtime.is_routable()
            and self._global_active_requests < self.settings.global_max_concurrency
            and runtime.active_requests < runtime.config.max_concurrency
        )

    def _is_queue_full(self, runtime: AccountRuntime) -> bool:
        return (
            runtime.queue_depth >= self.settings.per_account_max_queue_depth
            or self.total_queue_depth >= self.settings.global_max_queue_depth
        )

    def _remove_waiter(self, runtime: AccountRuntime, ticket_id: int | None) -> None:
        if ticket_id is None:
            return
        try:
            runtime.waiters.remove(ticket_id)
        except ValueError:
            return

    def _mark_reauth_required(self, runtime: AccountRuntime, message: str) -> None:
        runtime.last_error = message
        runtime.consecutive_failures += 1
        self._append_recent_error(runtime, f"provider_reauth_required: {message}")
        self._set_state(runtime, AccountRuntimeState.REAUTH_REQUIRED, message)

    def _mark_blocked(self, runtime: AccountRuntime, message: str) -> None:
        runtime.last_error = message
        runtime.consecutive_failures += 1
        self._append_recent_error(runtime, f"provider_blocked: {message}")
        self._set_state(runtime, AccountRuntimeState.BLOCKED, message)

    def _mark_unavailable(self, runtime: AccountRuntime, message: str) -> None:
        runtime.last_error = message
        runtime.consecutive_failures += 1
        self._append_recent_error(runtime, f"provider_unavailable: {message}")
        self._set_state(runtime, AccountRuntimeState.UNAVAILABLE, message)

    def _mark_cooling_down(self, runtime: AccountRuntime, message: str) -> None:
        runtime.last_error = message
        runtime.consecutive_failures += 1
        runtime.cooldown_until = datetime.now(timezone.utc) + timedelta(
            seconds=runtime.config.cooldown_seconds or self.settings.default_account_cooldown_seconds
        )
        self._append_recent_error(runtime, f"cooling_down: {message}")
        self._set_state(runtime, AccountRuntimeState.COOLING_DOWN, message)

    def _append_recent_error(self, runtime: AccountRuntime, message: str) -> None:
        runtime.recent_errors.append(message)
        if len(runtime.recent_errors) > self.settings.recent_error_limit:
            runtime.recent_errors = runtime.recent_errors[-self.settings.recent_error_limit :]

    def _require_runtime(self, account_id: str) -> AccountRuntime:
        runtime = self._runtimes.get(account_id)
        if runtime is None:
            raise ServiceError(
                status_code=404,
                code="account_not_found",
                message=f"Account {account_id} does not exist.",
            )
        return runtime

    def _set_state(self, runtime: AccountRuntime, state: AccountRuntimeState, reason: str | None) -> None:
        previous = runtime.state
        runtime.state = state
        runtime.state_reason = reason
        now = datetime.now(timezone.utc)
        if previous != state:
            runtime.last_transition_at = now
            self.logger.info(
                "account_state_transition",
                extra={
                    "event": "account_state_transition",
                    "account_id": runtime.config.account_id,
                    "from_state": previous.value,
                    "to_state": state.value,
                    "reason": reason or "",
                },
            )
        elif runtime.last_transition_at is None:
            runtime.last_transition_at = now
