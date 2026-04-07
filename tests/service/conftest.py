from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gemini_service.app import create_app
from gemini_service.core.config import get_settings


_SERVICE_ENV_KEYS = (
    "GEMINI_SERVICE_REQUIRE_AUTH",
    "GEMINI_SERVICE_API_TOKENS",
    "GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH",
    "GEMINI_SERVICE_DATABASE_URL",
    "GEMINI_SERVICE_UI_USERNAME",
    "GEMINI_SERVICE_UI_PASSWORD",
    "GEMINI_SERVICE_UI_SESSION_SECRET",
    "GEMINI_SERVICE_UI_SPA_ENABLED",
    "GEMINI_SERVICE_FRONTEND_DIST_PATH",
)


@pytest.fixture
def isolated_service_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.chdir(tmp_path)
    for key in _SERVICE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text('{"accounts":[]}', encoding="utf-8")
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'service.db').as_posix()}"

    monkeypatch.setenv("GEMINI_SERVICE_ACCOUNTS_CONFIG_PATH", str(accounts_path))
    monkeypatch.setenv("GEMINI_SERVICE_DATABASE_URL", database_url)
    monkeypatch.setenv("GEMINI_SERVICE_UI_SPA_ENABLED", "false")

    get_settings.cache_clear()
    return {
        "tmp_path": str(tmp_path),
        "accounts_path": str(accounts_path),
        "database_url": database_url,
    }


@pytest.fixture
def client_factory(
    monkeypatch: pytest.MonkeyPatch,
    isolated_service_env: dict[str, str],
):
    @contextmanager
    def _factory(**env: str):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            yield client
        get_settings.cache_clear()

    return _factory
