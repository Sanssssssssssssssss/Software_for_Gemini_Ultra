from __future__ import annotations

from fastapi.testclient import TestClient

from gemini_service.app import create_app
from gemini_service.core.config import get_settings


def _client(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    return TestClient(create_app())


def test_healthz_returns_ok(monkeypatch):
    client = _client(
        monkeypatch,
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="phase1-token",
    )

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readyz_reports_missing_accounts_config(monkeypatch):
    client = _client(
        monkeypatch,
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="phase1-token",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH="config/does-not-exist.json",
    )

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_accounts_requires_bearer_token(monkeypatch):
    client = _client(
        monkeypatch,
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="phase1-token",
    )

    response = client.get("/v1/accounts")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_accounts_returns_empty_list_with_valid_token(monkeypatch):
    client = _client(
        monkeypatch,
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="phase1-token",
    )

    response = client.get(
        "/v1/accounts",
        headers={"Authorization": "Bearer phase1-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}
