from __future__ import annotations

import asyncio
from pathlib import Path

from gemini_service.core.config import Settings
from gemini_service.core.security import AuthContext
from gemini_service.schemas.accounts import AccountConfig, AccountInventory, save_account_inventory
from gemini_service.services.admin_console_service import AdminConsoleService


class _FakeRepository:
    async def list_recent_assets(self, limit=20):
        return []

    async def count_messages(self):
        return 0

    async def count_batches(self):
        return 0

    async def count_assets(self):
        return 0

    async def count_assets_by_status(self):
        return {}


class _FakeChatService:
    repository = _FakeRepository()

    async def list_sessions(self, auth, limit=50):
        return []


class _FakePool:
    ready_account_count = 0
    inventory_count = 1
    total_queue_depth = 0

    def __init__(self) -> None:
        self.runtime = None

    async def list_account_summaries(self, force_refresh=True):
        return []

    def get_runtime(self, account_id):
        return self.runtime

    async def sync_inventory(self, force_refresh=True):
        return None


class _FakeRecovery:
    async def recover_account(self, account_id, *, allow_browser_launch, browser_session=None, browser=None):
        return type(
            "Result",
            (),
            {
                "account_id": account_id,
                "status": "completed",
                "code": "recovery_completed",
                "detail": "Recovered.",
                "browser": "chrome",
                "profile_dir": "data/browser-profiles/acc-1",
                "recovery_source": "live_browser_session",
                "cookie_count": 12,
                "provider_status": "AVAILABLE",
                "runtime_state": "ready",
                "account_models": ["gemini-3-pro"],
                "updated": True,
                "launched": False,
                "action": None,
                "session": None,
            },
        )()


class _FakeBrowserManager:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []

    async def health(self, account):
        return {
            "browser_online": True,
            "profile_dir": "data/browser-profiles/acc-1",
            "debug_port": 9440,
            "state": "online",
            "auto_refresh_enabled": True,
            "last_sync_at": None,
            "last_validated_at": None,
            "last_good_cookie_at": None,
            "last_error": None,
        }

    async def focus_session(self, account):
        self.actions.append((account.account_id, "focus"))
        return type("Entry", (), {"state": "online", "debug_port": 9440, "auto_refresh_enabled": True})()

    async def stop_session(self, account_id):
        self.actions.append((account_id, "stop"))
        return type("Entry", (), {"state": "offline", "debug_port": 9440, "auto_refresh_enabled": True})()

    async def set_auto_refresh(self, account_id, enabled):
        self.actions.append((account_id, "resume" if enabled else "pause"))
        return type("Entry", (), {"state": "online", "debug_port": 9440, "auto_refresh_enabled": enabled})()

    def get_entry(self, account_id):
        return type("Entry", (), {"state": "online", "debug_port": 9440, "auto_refresh_enabled": True})()


class _FakeDaemon:
    def __init__(self) -> None:
        self.forced: list[str] = []

    def force_sync_now(self, account_id: str) -> None:
        self.forced.append(account_id)


def test_admin_console_browser_actions_and_reauth_job(tmp_path: Path):
    accounts_path = tmp_path / "accounts.json"
    save_account_inventory(
        accounts_path,
        AccountInventory(
            accounts=[
                AccountConfig(
                    account_id="acc-1",
                    secure_1psid="cookie",
                    secure_1psidts="sidts-cookie",
                    cookie_source_browser="chrome",
                    cookie_source_profile_dir="data/browser-profiles/acc-1",
                )
            ]
        ).model_dump(mode="json"),
    )
    service = AdminConsoleService(
        settings=Settings(accounts_config_path=str(accounts_path)),
        repository=_FakeRepository(),
        pool=_FakePool(),
        chat_service=_FakeChatService(),
        asset_service=None,  # type: ignore[arg-type]
        recovery_service=_FakeRecovery(),
        browser_manager=_FakeBrowserManager(),
        refresh_daemon=_FakeDaemon(),
        telemetry=None,
    )

    response = asyncio.run(service.browser_action("acc-1", "focus"))
    assert response["browser_state"] == "online"

    job = asyncio.run(service.start_reauth_job("acc-1"))
    assert job.account_id == "acc-1"
    assert job.status in {"queued", "completed", "validating_provider"}

    dashboard = asyncio.run(service.build_dashboard(AuthContext(subject="admin", role="admin", source="ui")))
    assert dashboard.inventory_accounts[0].browser_online is True
