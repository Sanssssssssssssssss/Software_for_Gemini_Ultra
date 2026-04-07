from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from gemini_service.app import create_app
from gemini_service.core.bootstrap import evaluate_bootstrap_status
from gemini_service.core.config import Settings, get_settings


@contextmanager
def _client(monkeypatch, tmp_path, **env):
    monkeypatch.chdir(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        yield client


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


def test_root_redirects_to_setup_when_bootstrap_incomplete(monkeypatch, tmp_path):
    accounts = tmp_path / "accounts.json"
    accounts.write_text('{"accounts":[]}', encoding="utf-8")

    with _client(
        monkeypatch,
        tmp_path,
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="change-me",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=str(accounts),
        GEMINI_SERVICE_UI_PASSWORD="change-me-ui-password",
        GEMINI_SERVICE_UI_SESSION_SECRET="change-me-session-secret",
    ) as client:
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/setup"


def test_setup_status_endpoint_returns_bootstrap_report(monkeypatch, tmp_path):
    accounts = tmp_path / "accounts.json"
    accounts.write_text('{"accounts":[]}', encoding="utf-8")

    with _client(
        monkeypatch,
        tmp_path,
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=str(accounts),
    ) as client:
        response = client.get("/setup/status")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "needs_setup"
        assert any(check["name"] == "accounts_file" for check in payload["checks"])
