from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import AccountSummary, AssetResponse, SessionHistoryResponse, SessionResponse


class AdminManagedAccount(BaseModel):
    account_id: str
    enabled: bool = True
    provider_backend: str = "gemini_web"
    cookie_source_browser: str | None = None
    cookie_source_browser_path: str | None = None
    cookie_source_profile_dir: str | None = None
    proxy: str | None = None
    max_concurrency: int = 1
    cooldown_seconds: int = 60
    request_timeout_seconds: int = 450
    verify_ssl: bool = True
    tags: list[str] = Field(default_factory=list)
    has_cookie_bundle: bool = False
    runtime: AccountSummary | None = None


class AdminAccountUpsertRequest(BaseModel):
    account_id: str
    enabled: bool = True
    provider_backend: str = "gemini_web"
    cookie_source_browser: str | None = None
    cookie_source_browser_path: str | None = None
    cookie_source_profile_dir: str | None = None
    proxy: str | None = None
    max_concurrency: int = Field(default=1, ge=1)
    cooldown_seconds: int = Field(default=60, ge=5)
    request_timeout_seconds: int = Field(default=450, ge=10)
    verify_ssl: bool = True
    tags: list[str] = Field(default_factory=list)
    secure_1psid: str | None = None
    secure_1psidts: str | None = None


class AdminReauthJobResponse(BaseModel):
    job_id: str
    account_id: str
    status: Literal["awaiting_login", "syncing", "completed", "failed", "cancelled"]
    detail: str
    browser: str | None = None
    profile_dir: str | None = None
    launched: bool = False
    created_at: str
    updated_at: str
    action_required: str | None = None
    launch_url: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)


class AdminSessionExportEnvelope(BaseModel):
    session: SessionResponse
    history: SessionHistoryResponse


class AdminDashboardResponse(BaseModel):
    accounts: list[AccountSummary] = Field(default_factory=list)
    inventory_accounts: list[AdminManagedAccount] = Field(default_factory=list)
    sessions: list[SessionResponse] = Field(default_factory=list)
    assets: list[AssetResponse] = Field(default_factory=list)
    reauth_jobs: list[AdminReauthJobResponse] = Field(default_factory=list)
    telemetry: dict[str, Any] = Field(default_factory=dict)
    health: dict[str, Any] = Field(default_factory=dict)
