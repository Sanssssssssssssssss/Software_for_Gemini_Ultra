from __future__ import annotations

from pydantic import BaseModel, Field


class AccountConfig(BaseModel):
    account_id: str
    enabled: bool = True
    provider_backend: str = "gemini_web"
    secure_1psid: str
    secure_1psidts: str | None = None
    cookie_source_browser: str | None = None
    cookie_source_browser_path: str | None = None
    cookie_source_profile_dir: str | None = None
    proxy: str | None = None
    max_concurrency: int = Field(default=1, ge=1)
    cooldown_seconds: int = Field(default=60, ge=5)
    request_timeout_seconds: int = Field(default=450, ge=10)
    verify_ssl: bool = True
    mock_behavior: str = "healthy"
    mock_delay_ms: int = Field(default=0, ge=0)
    mock_models: list[str] = Field(default_factory=lambda: ["gemini-3-pro"])
    tags: list[str] = Field(default_factory=list)


class AccountInventory(BaseModel):
    accounts: list[AccountConfig] = Field(default_factory=list)
