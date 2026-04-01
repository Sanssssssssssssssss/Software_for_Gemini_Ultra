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


def test_ui_protected_routes_redirect_to_login(monkeypatch, tmp_path):
    accounts = tmp_path / "accounts.json"
    accounts.write_text('{"accounts":[]}', encoding="utf-8")

    with _client(
        monkeypatch,
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=str(accounts),
    ) as client:
        response = client.get("/ui/chat", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/ui/login"


def test_ui_login_and_admin_page(monkeypatch, tmp_path):
    accounts = tmp_path / "accounts.json"
    accounts.write_text('{"accounts":[]}', encoding="utf-8")

    with _client(
        monkeypatch,
        GEMINI_SERVICE_REQUIRE_AUTH="false",
        GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH=str(accounts),
        GEMINI_SERVICE_UI_USERNAME="admin",
        GEMINI_SERVICE_UI_PASSWORD="secret-pass",
    ) as client:
        login = client.post(
            "/ui/login",
            data={"username": "admin", "password": "secret-pass"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/ui/chat"

        admin = client.get("/admin")
        assert admin.status_code == 200
        assert "账号池与会话总览" in admin.text
