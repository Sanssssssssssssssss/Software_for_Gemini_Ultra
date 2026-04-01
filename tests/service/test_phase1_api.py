from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from gemini_service.app import create_app
from gemini_service.core.config import get_settings


@contextmanager
def _client(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        yield client


def test_healthz_returns_ok(monkeypatch):
    with _client(
        monkeypatch,
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="phase1-token",
    ) as client:
        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert "X-Request-ID" in response.headers


def test_readyz_reports_missing_accounts_config(monkeypatch):
    with _client(
        monkeypatch,
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="phase1-token",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH="config/does-not-exist.json",
    ) as client:
        response = client.get("/readyz")

        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"


def test_accounts_requires_bearer_token(monkeypatch):
    with _client(
        monkeypatch,
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="phase1-token",
    ) as client:
        response = client.get("/v1/accounts")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


def test_accounts_returns_empty_list_with_valid_token(monkeypatch):
    with _client(
        monkeypatch,
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="phase1-token",
    ) as client:
        response = client.get(
            "/v1/accounts",
            headers={"Authorization": "Bearer phase1-token"},
        )

        assert response.status_code == 200
        assert response.json() == {"items": []}


def test_metrics_endpoint_exposes_prometheus_text(monkeypatch, tmp_path):
    accounts = tmp_path / "accounts.json"
    accounts.write_text('{"accounts":[]}', encoding="utf-8")

    with _client(
        monkeypatch,
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=str(accounts),
    ) as client:
        client.get("/healthz")
        response = client.get("/metrics")

        assert response.status_code == 200
        assert "gemini_service_http_requests_total" in response.text
