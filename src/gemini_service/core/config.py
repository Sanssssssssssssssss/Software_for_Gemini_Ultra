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
    accounts_config_path: str = "config/accounts.json"
    min_ready_accounts: int = 1
    global_max_concurrency: int = 32
    account_probe_interval_seconds: int = 30
    default_account_cooldown_seconds: int = 60

    @property
    def api_token_values(self) -> list[str]:
        return [item.strip() for item in self.api_tokens.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
