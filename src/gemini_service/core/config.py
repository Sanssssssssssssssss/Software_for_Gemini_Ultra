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
    cookie_autosync_enabled: bool = False
    cookie_autosync_browser: str = "chrome"
    cookie_autosync_headless: bool = True
    cookie_autosync_timeout_seconds: int = 20
    cookie_autosync_start_url: str = "https://gemini.google.com/app"
    admin_reauth_poll_interval_seconds: float = 2.0
    admin_reauth_timeout_seconds: int = 600
    ui_username: str = "admin"
    ui_password: str = "change-me-ui-password"
    ui_user_username: str = ""
    ui_user_password: str = ""
    ui_session_secret: str = "change-me-session-secret"
    ui_session_cookie: str = "gemini_service_ui"
    ui_spa_enabled: bool = True
    frontend_dist_path: str = "frontend/dist"
    metrics_enabled: bool = True
    asset_storage_backend: str = "local"
    asset_root_path: str = "data/assets"
    asset_max_upload_bytes: int = 40 * 1024 * 1024
    asset_default_ttl_hours: int = 24
    asset_max_files_per_message: int = 5
    asset_cleanup_interval_seconds: int = 300
    asset_orphan_grace_hours: int = 1
    asset_allowed_mime_types: str = (
        "image/png,image/jpeg,image/webp,application/pdf,"
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )

    @property
    def api_token_values(self) -> list[str]:
        return [item.strip() for item in self.api_tokens.split(",") if item.strip()]

    @property
    def asset_allowed_mime_values(self) -> list[str]:
        return [item.strip().lower() for item in self.asset_allowed_mime_types.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
