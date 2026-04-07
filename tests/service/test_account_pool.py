from __future__ import annotations

import asyncio
import json

import pytest
from gemini_webapi.exceptions import TemporarilyBlocked

from gemini_service.adapters.base import AccountProbeResult
from gemini_service.core.config import Settings
from gemini_service.core.errors import ServiceError
from gemini_service.services.account_pool import AccountPool, AccountRuntimeState


class FakeAdapter:
    def __init__(self, probe_result=None, error: Exception | None = None):
        self.probe_result = probe_result
        self.error = error
        self.closed = False

    async def probe(self):
        if self.error is not None:
            raise self.error
        return self.probe_result

    async def close(self):
        self.closed = True


def _build_settings(tmp_path, accounts, **overrides):
    config_path = tmp_path / "accounts.json"
    config_path.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    overrides.setdefault("account_probe_interval_seconds", 3600)
    overrides.setdefault("queue_wait_timeout_seconds", 1)
    return Settings(
        require_auth=False,
        accounts_config_path=str(config_path),
        **overrides,
    )


def _ready_probe() -> AccountProbeResult:
    return AccountProbeResult(
        account_status="AVAILABLE",
        account_status_code=1000,
        status_description="ok",
        models=["gemini-3-pro"],
    )


async def _hold_lease(lease, entered: asyncio.Event, release: asyncio.Event) -> None:
    async with lease:
        entered.set()
        await release.wait()


async def _claim_lease(lease, order: list[str], label: str) -> None:
    async with lease:
        order.append(label)
        await asyncio.sleep(0.01)


def test_account_pool_marks_ready_and_disabled_accounts(tmp_path):
    settings = _build_settings(
        tmp_path,
        [
            {"account_id": "ready-1", "secure_1psid": "a"},
            {"account_id": "disabled-1", "secure_1psid": "b", "enabled": False},
        ],
    )

    def adapter_factory(config):
        return FakeAdapter(probe_result=_ready_probe())

    pool = AccountPool(settings, adapter_factory=adapter_factory)
    asyncio.run(pool.start())

    ready_runtime = pool.get_runtime("ready-1")
    disabled_runtime = pool.get_runtime("disabled-1")

    assert ready_runtime is not None
    assert ready_runtime.state == AccountRuntimeState.READY
    assert disabled_runtime is not None
    assert disabled_runtime.state == AccountRuntimeState.DISABLED


def test_account_pool_enters_blocked_state_on_temporary_failure(tmp_path):
    settings = _build_settings(
        tmp_path,
        [{"account_id": "blocked-1", "secure_1psid": "a", "cooldown_seconds": 30}],
    )

    pool = AccountPool(
        settings,
        adapter_factory=lambda config: FakeAdapter(error=TemporarilyBlocked("blocked")),
    )
    asyncio.run(pool.start())

    runtime = pool.get_runtime("blocked-1")
    assert runtime is not None
    assert runtime.state == AccountRuntimeState.BLOCKED


def test_account_pool_transitions_between_degraded_and_unavailable(tmp_path):
    settings = _build_settings(
        tmp_path,
        [{"account_id": "acc-1", "secure_1psid": "a"}],
        unavailable_failure_threshold=2,
    )
    pool = AccountPool(settings, adapter_factory=lambda config: FakeAdapter(probe_result=_ready_probe()))
    asyncio.run(pool.start())

    runtime = pool.get_runtime("acc-1")
    assert runtime is not None

    pool.record_provider_failure(
        runtime,
        ServiceError(status_code=504, code="provider_timeout", message="first timeout"),
    )
    assert runtime.state == AccountRuntimeState.DEGRADED

    pool.record_provider_failure(
        runtime,
        ServiceError(status_code=503, code="provider_unavailable", message="second failure"),
    )
    assert runtime.state == AccountRuntimeState.UNAVAILABLE

    pool.record_provider_success(runtime)
    assert runtime.state == AccountRuntimeState.DEGRADED

    pool.record_provider_success(runtime)
    assert runtime.state == AccountRuntimeState.READY


def test_account_pool_rejects_when_queue_is_full(tmp_path):
    settings = _build_settings(
        tmp_path,
        [{"account_id": "acc-1", "secure_1psid": "a", "max_concurrency": 1}],
        global_max_concurrency=1,
        global_max_queue_depth=1,
        per_account_max_queue_depth=1,
    )
    pool = AccountPool(settings, adapter_factory=lambda config: FakeAdapter(probe_result=_ready_probe()))

    async def scenario():
        await pool.start()
        entered = asyncio.Event()
        release = asyncio.Event()

        holder = asyncio.create_task(
            _hold_lease(await pool.acquire("acc-1", require_preferred=True), entered, release)
        )
        await entered.wait()

        queued = asyncio.create_task(_claim_lease(await pool.acquire("acc-1", require_preferred=True), [], "queued"))
        await asyncio.sleep(0.05)
        runtime = pool.get_runtime("acc-1")
        assert runtime is not None
        assert runtime.queue_depth == 1

        with pytest.raises(ServiceError) as exc:
            await _claim_lease(await pool.acquire("acc-1", require_preferred=True), [], "overflow")
        assert exc.value.code == "queue_full"

        release.set()
        await holder
        await queued
        await pool.close()

    asyncio.run(scenario())


def test_account_pool_times_out_when_waiting_in_queue(tmp_path):
    settings = _build_settings(
        tmp_path,
        [{"account_id": "acc-1", "secure_1psid": "a", "max_concurrency": 1}],
        global_max_concurrency=1,
        global_max_queue_depth=2,
        per_account_max_queue_depth=2,
        queue_wait_timeout_seconds=0.1,
    )
    pool = AccountPool(settings, adapter_factory=lambda config: FakeAdapter(probe_result=_ready_probe()))

    async def scenario():
        await pool.start()
        entered = asyncio.Event()
        release = asyncio.Event()

        holder = asyncio.create_task(
            _hold_lease(await pool.acquire("acc-1", require_preferred=True), entered, release)
        )
        await entered.wait()

        with pytest.raises(ServiceError) as exc:
            await _claim_lease(await pool.acquire("acc-1", require_preferred=True), [], "timeout")
        assert exc.value.code == "queue_timeout"

        release.set()
        await holder
        await pool.close()

    asyncio.run(scenario())


def test_account_pool_queues_in_fifo_order(tmp_path):
    settings = _build_settings(
        tmp_path,
        [{"account_id": "acc-1", "secure_1psid": "a", "max_concurrency": 1}],
        global_max_concurrency=1,
        global_max_queue_depth=2,
        per_account_max_queue_depth=2,
    )
    pool = AccountPool(settings, adapter_factory=lambda config: FakeAdapter(probe_result=_ready_probe()))

    async def scenario():
        await pool.start()
        entered = asyncio.Event()
        release = asyncio.Event()
        order: list[str] = []

        holder = asyncio.create_task(
            _hold_lease(await pool.acquire("acc-1", require_preferred=True), entered, release)
        )
        await entered.wait()

        second = asyncio.create_task(_claim_lease(await pool.acquire("acc-1", require_preferred=True), order, "second"))
        await asyncio.sleep(0.02)
        third = asyncio.create_task(_claim_lease(await pool.acquire("acc-1", require_preferred=True), order, "third"))

        await asyncio.sleep(0.05)
        release.set()
        await holder
        await second
        await third
        await pool.close()
        return order

    order = asyncio.run(scenario())
    assert order == ["second", "third"]
