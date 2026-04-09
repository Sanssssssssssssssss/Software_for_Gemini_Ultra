from __future__ import annotations

import asyncio
import json
from pathlib import Path

from gemini_service.core.browser_cookie_sync import BrowserLoginSession
from gemini_service.core.config import Settings
from gemini_service.schemas.accounts import AccountConfig
from gemini_service.services.persistent_browser_manager import PersistentBrowserManager


def test_browser_manager_persists_registry_and_attaches_existing_session(tmp_path, monkeypatch):
    state_path = tmp_path / "browser-sessions.json"
    profile_root = tmp_path / "profiles"
    settings = Settings(
        browser_state_path=str(state_path),
        browser_profile_root=str(profile_root),
    )
    manager = PersistentBrowserManager(settings)
    asyncio.run(manager.start())

    async def _fake_update() -> None:
        await manager._update_entry(  # noqa: SLF001
            "acc-1",
            profile_dir=str((profile_root / "acc-1").resolve()),
            browser="chrome",
            browser_path=str(Path("/fake/browser")),
            debug_port=9440,
            pid=1010,
            state="online",
            launched_by_service=True,
        )

    asyncio.run(_fake_update())
    assert state_path.exists()
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["sessions"][0]["account_id"] == "acc-1"

    monkeypatch.setattr(
        "gemini_service.services.persistent_browser_manager.browser_debug_endpoint_available",
        lambda port, timeout_seconds=2: True,
    )

    account = AccountConfig(account_id="acc-1", secure_1psid="cookie", secure_1psidts="sidts-cookie")
    attached = asyncio.run(manager.attach_existing_session(account))
    assert attached is not None
    assert attached.port == 9440
    assert attached.process is None


def test_browser_manager_respects_explicit_profile_dir(tmp_path):
    settings = Settings(
        browser_state_path=str(tmp_path / "browser-sessions.json"),
        browser_profile_root=str(tmp_path / "profiles"),
    )
    manager = PersistentBrowserManager(settings)
    account = AccountConfig(
        account_id="acc-2",
        secure_1psid="cookie",
        secure_1psidts="sidts-cookie",
        cookie_source_profile_dir="data/my-profile",
    )
    resolved = manager.resolve_profile_dir(account)
    assert resolved.name == "my-profile"
