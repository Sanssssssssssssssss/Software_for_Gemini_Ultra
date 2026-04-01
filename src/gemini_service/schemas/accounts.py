from __future__ import annotations

from pydantic import BaseModel, Field


class AccountConfig(BaseModel):
    account_id: str
    enabled: bool = True
    secure_1psid: str
    secure_1psidts: str | None = None
    proxy: str | None = None
    max_concurrency: int = Field(default=1, ge=1)
    cooldown_seconds: int = Field(default=60, ge=5)
    request_timeout_seconds: int = Field(default=450, ge=10)
    verify_ssl: bool = True
    tags: list[str] = Field(default_factory=list)


class AccountInventory(BaseModel):
    accounts: list[AccountConfig] = Field(default_factory=list)
