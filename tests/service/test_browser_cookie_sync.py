from __future__ import annotations

import json
from pathlib import Path

from gemini_service.core.browser_cookie_sync import normalize_profile_dir, sync_inventory_from_browser_profiles


def test_sync_inventory_updates_configured_account(tmp_path: Path, monkeypatch):
    profile_dir = tmp_path / "profiles" / "acc-1"
    profile_dir.mkdir(parents=True)
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "account_id": "acc-1",
                        "secure_1psid": "old",
                        "secure_1psidts": "old-ts",
                        "cookie_source_browser": "chrome",
                        "cookie_source_profile_dir": str(profile_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "gemini_service.core.browser_cookie_sync.sync_cookies_from_profile",
        lambda **kwargs: ("new-cookie", "new-cookie-ts"),
    )

    results = sync_inventory_from_browser_profiles(
        accounts_path=accounts_path,
        timeout_seconds=5,
        start_url="https://gemini.google.com/app",
    )

    payload = json.loads(accounts_path.read_text(encoding="utf-8"))
    account = payload["accounts"][0]
    assert account["secure_1psid"] == "new-cookie"
    assert account["secure_1psidts"] == "new-cookie-ts"
    assert results[0].status == "ok"
    assert results[0].updated is True


def test_sync_inventory_skips_accounts_without_profile(tmp_path: Path):
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "account_id": "acc-1",
                        "secure_1psid": "keep",
                        "secure_1psidts": "keep-ts",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    results = sync_inventory_from_browser_profiles(
        accounts_path=accounts_path,
        timeout_seconds=5,
        start_url="https://gemini.google.com/app",
    )

    payload = json.loads(accounts_path.read_text(encoding="utf-8"))
    account = payload["accounts"][0]
    assert account["secure_1psid"] == "keep"
    assert account["secure_1psidts"] == "keep-ts"
    assert results[0].status == "skipped"


def test_sync_inventory_reports_missing_browser_profile(tmp_path: Path, monkeypatch):
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "account_id": "acc-1",
                        "secure_1psid": "keep",
                        "secure_1psidts": "keep-ts",
                        "cookie_source_browser": "chrome",
                        "cookie_source_profile_dir": "profiles/acc-1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "gemini_service.core.browser_cookie_sync.resolve_browser_path",
        lambda browser, explicit_path=None: Path("/fake/browser"),
    )

    results = sync_inventory_from_browser_profiles(
        accounts_path=accounts_path,
        timeout_seconds=5,
        start_url="https://gemini.google.com/app",
    )

    assert results[0].status == "error"
    assert results[0].code == "cookie_profile_missing"


def test_normalize_profile_dir_uses_repo_relative_resolution(tmp_path: Path):
    accounts_path = tmp_path / "config" / "accounts.json"
    accounts_path.parent.mkdir(parents=True)
    expected = (tmp_path / "data" / "profile-a").resolve()

    normalized = normalize_profile_dir(accounts_path, "data/profile-a")

    assert normalized == expected
