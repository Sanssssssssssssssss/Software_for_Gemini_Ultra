from __future__ import annotations


def test_healthz_returns_ok(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="phase1-token",
    ) as client:
        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert "X-Request-ID" in response.headers


def test_readyz_reports_missing_accounts_config(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="phase1-token",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH="config/does-not-exist.json",
    ) as client:
        response = client.get("/readyz")

        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"


def test_accounts_requires_bearer_token(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="phase1-token",
    ) as client:
        response = client.get("/v1/accounts")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


def test_accounts_returns_empty_list_with_valid_token(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="phase1-token",
    ) as client:
        response = client.get(
            "/v1/accounts",
            headers={"Authorization": "Bearer phase1-token"},
        )

        assert response.status_code == 200
        assert response.json() == {"items": []}


def test_metrics_endpoint_exposes_prometheus_text(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="false",
    ) as client:
        client.get("/healthz")
        response = client.get("/metrics")

        assert response.status_code == 200
        assert "gemini_service_http_requests_total" in response.text
        assert "gemini_service_account_states" in response.text
        assert "gemini_service_provider_calls_total" in response.text
