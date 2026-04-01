from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ApiError


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str


class ReadinessCheck(BaseModel):
    name: str
    status: Literal["pass", "fail"]
    detail: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: list[ReadinessCheck]


class AccountSummary(BaseModel):
    account_id: str
    state: str
    account_status: str | None = None
    status_description: str | None = None
    models: list[str] = Field(default_factory=list)
    active_requests: int = 0
    queue_depth: int = 0
    configured_max_concurrency: int = 1
    cooldown_until: str | None = None
    last_error: str | None = None


class AccountsResponse(BaseModel):
    items: list[AccountSummary]


class SessionCreateRequest(BaseModel):
    account_id: str | None = None
    routing_policy: str = "sticky"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    session_id: str
    account_id: str
    routing_policy: str
    status: str


class MessageRequest(BaseModel):
    session_id: str
    message: str
    stream: bool = False
    idempotency_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BatchItem(BaseModel):
    external_id: str
    prompt: str
    session_id: str | None = None
    account_id: str | None = None


class BatchRequest(BaseModel):
    items: list[BatchItem]


class SessionHistoryItem(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: str | None = None


class SessionHistoryResponse(BaseModel):
    session_id: str
    items: list[SessionHistoryItem]
