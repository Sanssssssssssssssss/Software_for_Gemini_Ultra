from __future__ import annotations

import asyncio
import json
from pathlib import Path

from gemini_service.core.browser_cookie_sync import BrowserLoginSession, CookieBundle
from gemini_service.core.config import Settings
from gemini_service.schemas.accounts import AccountConfig
from gemini_service.schemas.common import AccountSummary
from gemini_service.services.account_recovery_service import AccountRecoveryService


class _FakePool:
    def __init__(self) -> None:
        self.sync_calls = 0
        self.refresh_calls = 0
        self.candidate_summary = AccountSummary(
            account_id="acc-1",
            state="reauth_required",
            account_status="UNAUTHENTICATED",
            status_description="not authenticated",
            models=[],
            active_requests=0,
            queue_depth=0,
            configured_max_concurrency=1,
            cooldown_until=None,
            last_error=None,
            recent_errors=[],
            failure_count=0,
            last_transition_at=None,
            state_reason="not authenticated",
        )

    async def probe_candidate(self, config: AccountConfig) -> AccountSummary:
        return self.candidate_summary

    async def sync_inventory(self, force_refresh: bool = True) -> None:
        self.sync_calls += 1

    async def refresh_account(self, account_id: str):
        self.refresh_calls += 1
        raise AssertionError("refresh_account should not be called when candidate validation fails")


class _FakeBrowserManager:
    def __init__(self, bundle: CookieBundle | None = None) -> None:
        self.bundle = bundle
        self.recorded_errors: list[str] = []
        self.recorded_results: list[tuple[str, bool]] = []
        self.session = BrowserLoginSession(
            browser="chrome",
            browser_path=Path("/fake/browser"),
            profile_dir=Path("/fake/profile"),
            port=9222,
            process=None,
            start_url="https://gemini.google.com/app",
            pid=1234,
            launched_by_service=True,
        )

    def resolve_profile_dir(self, account: AccountConfig) -> Path:
        return Path(account.cookie_source_profile_dir or "/fake/profile")

    async def attach_existing_session(self, account: AccountConfig):
        return self.session

    async def ensure_session(self, account: AccountConfig):
        return self.session

    async def collect_cookie_bundle(self, account: AccountConfig) -> CookieBundle:
        if self.bundle is None:
            raise RuntimeError("no cookies yet")
        return self.bundle

    async def record_error(self, account_id: str, error: str, *, state: str = "error") -> None:
        self.recorded_errors.append(error)

    async def record_cookie_sync_result(
        self,
        account_id: str,
        *,
        bundle: CookieBundle,
        provider_status: str | None,
        runtime_state: str | None,
        success: bool,
        error: str | None = None,
    ) -> None:
        self.recorded_results.append((account_id, success))


def test_recovery_does_not_overwrite_inventory_when_provider_is_still_unauthenticated(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "account_id": "acc-1",
                        "secure_1psid": "old-cookie",
                        "secure_1psidts": "old-sidts",
                        "cookie_source_browser": "chrome",
                        "cookie_source_profile_dir": str(tmp_path / "profiles" / "acc-1"),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    profile_dir = tmp_path / "profiles" / "acc-1"
    profile_dir.mkdir(parents=True)

    settings = Settings(
        accounts_config_path=str(accounts_path),
        cookie_autosync_enabled=True,
    )
    pool = _FakePool()
    manager = _FakeBrowserManager(
        CookieBundle(
            secure_1psid="new-cookie",
            secure_1psidts="sidts-new-cookie",
            cookies=[
                {
                    "name": "__Secure-1PSID",
                    "value": "new-cookie",
                    "domain": ".google.com",
                    "path": "/",
                },
                {
                    "name": "__Secure-1PSIDTS",
                    "value": "sidts-new-cookie",
                    "domain": ".google.com",
                    "path": "/",
                },
            ],
        )
    )
    service = AccountRecoveryService(settings=settings, pool=pool, browser_manager=manager)

    result = asyncio.run(service.recover_account("acc-1", allow_browser_launch=False))

    payload = json.loads(accounts_path.read_text(encoding="utf-8"))
    assert result.status == "awaiting_login"
    assert result.code == "cookie_collected_but_provider_unauthenticated"
    assert payload["accounts"][0]["secure_1psid"] == "old-cookie"
    assert payload["accounts"][0]["secure_1psidts"] == "old-sidts"
    assert pool.sync_calls == 0
    assert pool.refresh_calls == 0
    assert manager.recorded_results == []
