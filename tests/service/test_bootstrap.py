from __future__ import annotations

import json
from pathlib import Path

from gemini_service.core.bootstrap import evaluate_bootstrap_status
from gemini_service.core.config import Settings


def test_bootstrap_status_detects_placeholder_configuration(tmp_path, monkeypatch):
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        '{"accounts":[{"account_id":"acc-1","secure_1psid":"replace-me","secure_1psidts":"replace-me-if-required"}]}',
        encoding="utf-8",
    )
    settings = Settings(
        require_auth=True,
        api_tokens="change-me",
        ui_password="change-me-ui-password",
        ui_session_secret="change-me-session-secret",
        accounts_config_path=str(accounts),
    )

    monkeypatch.chdir(tmp_path)
    status = evaluate_bootstrap_status(settings)

    assert status.setup_complete is False
    assert status.status == "needs_setup"
    assert any(check.name == "accounts_credentials" and check.status == "fail" for check in status.checks)


def test_root_redirects_to_setup_when_bootstrap_incomplete(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="change-me",
        GEMINI_SERVICE_UI_PASSWORD="change-me-ui-password",
        GEMINI_SERVICE_UI_SESSION_SECRET="change-me-session-secret",
    ) as client:
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/setup"


def test_setup_status_endpoint_returns_bootstrap_report(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
    ) as client:
        response = client.get("/setup/status")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "needs_setup"
        assert any(check["name"] == "accounts_file" for check in payload["checks"])


def test_bootstrap_reports_missing_frontend_dist_and_unusable_cookie_profile(tmp_path, monkeypatch):
    profile_dir = tmp_path / "missing-profile"
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "account_id": "acc-1",
                        "secure_1psid": "real-cookie",
                        "secure_1psidts": "sidts-real-cookie",
                        "cookie_source_browser": "chrome",
                        "cookie_source_profile_dir": profile_dir.as_posix(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        require_auth=False,
        ui_spa_enabled=True,
        frontend_dist_path="frontend/missing-dist",
        cookie_autosync_enabled=True,
        accounts_config_path=str(accounts),
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'service.db').as_posix()}",
        asset_root_path=str(tmp_path / "assets"),
    )

    monkeypatch.setattr(
        "gemini_service.core.bootstrap.resolve_browser_path",
        lambda browser, explicit_path=None: Path("/fake/browser"),
    )

    monkeypatch.chdir(tmp_path)
    status = evaluate_bootstrap_status(settings)
    checks = {check.name: check for check in status.checks}

    assert checks["frontend_dist"].status == "warn"
    assert checks["asset_root"].status == "pass"
    assert checks["database_path"].status == "pass"
    assert checks["cookie_autosync_profile:acc-1"].status == "warn"
