from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GEMINI_SERVICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "gemini-internal-service"
    env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    log_json: bool = False
    require_auth: bool = True
    api_tokens: str = ""
    openapi_enabled: bool = True
    database_url: str = "sqlite+aiosqlite:///./data/gemini_service.db"
    accounts_config_path: str = "config/accounts.json"
    min_ready_accounts: int = 1
    global_max_concurrency: int = 32
    global_max_queue_depth: int = 128
    per_account_max_queue_depth: int = 16
    queue_wait_timeout_seconds: float = 30.0
    account_probe_interval_seconds: int = 30
    default_account_cooldown_seconds: int = 60
    degraded_failure_threshold: int = 1
    unavailable_failure_threshold: int = 3
    recent_error_limit: int = 5
    ui_username: str = "admin"
    ui_password: str = "change-me-ui-password"
    ui_session_secret: str = "change-me-session-secret"
    ui_session_cookie: str = "gemini_service_ui"
    metrics_enabled: bool = True

    @property
    def api_token_values(self) -> list[str]:
        return [item.strip() for item in self.api_tokens.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
