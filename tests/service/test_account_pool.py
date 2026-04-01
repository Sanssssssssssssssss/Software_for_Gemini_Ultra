from __future__ import annotations

import asyncio
import json

from gemini_webapi.exceptions import TemporarilyBlocked

from gemini_service.adapters.base import AccountProbeResult
from gemini_service.core.config import Settings
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


def _build_settings(tmp_path, accounts):
    config_path = tmp_path / "accounts.json"
    config_path.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    return Settings(
        require_auth=False,
        accounts_config_path=str(config_path),
        account_probe_interval_seconds=0,
    )


def test_account_pool_marks_ready_and_disabled_accounts(tmp_path):
    settings = _build_settings(
        tmp_path,
        [
            {"account_id": "ready-1", "secure_1psid": "a"},
            {"account_id": "disabled-1", "secure_1psid": "b", "enabled": False},
        ],
    )

    def adapter_factory(config):
        return FakeAdapter(
            probe_result=AccountProbeResult(
                account_status="AVAILABLE",
                account_status_code=1000,
                status_description="ok",
                models=["gemini-3-pro"],
            )
        )

    pool = AccountPool(settings, adapter_factory=adapter_factory)
    asyncio.run(pool.start())

    ready_runtime = pool.get_runtime("ready-1")
    disabled_runtime = pool.get_runtime("disabled-1")

    assert ready_runtime is not None
    assert ready_runtime.state == AccountRuntimeState.READY
    assert disabled_runtime is not None
    assert disabled_runtime.state == AccountRuntimeState.DISABLED


def test_account_pool_enters_cooldown_on_temporary_failure(tmp_path):
    settings = _build_settings(
        tmp_path,
        [{"account_id": "cooldown-1", "secure_1psid": "a", "cooldown_seconds": 30}],
    )

    pool = AccountPool(
        settings,
        adapter_factory=lambda config: FakeAdapter(error=TemporarilyBlocked("blocked")),
    )
    asyncio.run(pool.start())

    runtime = pool.get_runtime("cooldown-1")
    assert runtime is not None
    assert runtime.state == AccountRuntimeState.COOLDOWN
    assert runtime.cooldown_until is not None


def test_account_pool_prefers_least_busy_ready_account(tmp_path):
    settings = _build_settings(
        tmp_path,
        [
            {"account_id": "busy", "secure_1psid": "a"},
            {"account_id": "idle", "secure_1psid": "b"},
        ],
    )

    def adapter_factory(config):
        return FakeAdapter(
            probe_result=AccountProbeResult(
                account_status="AVAILABLE",
                account_status_code=1000,
                status_description="ok",
                models=["gemini-3-flash"],
            )
        )

    pool = AccountPool(settings, adapter_factory=adapter_factory)
    asyncio.run(pool.start())

    busy_runtime = pool.get_runtime("busy")
    assert busy_runtime is not None
    busy_runtime.active_requests = 1

    chosen = asyncio.run(pool.choose_account())
    assert chosen.config.account_id == "idle"
