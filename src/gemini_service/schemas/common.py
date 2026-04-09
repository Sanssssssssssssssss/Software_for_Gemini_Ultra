from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator


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


class BootstrapCheck(BaseModel):
    name: str
    status: Literal["pass", "warn", "fail"]
    detail: str
    action: str | None = None


class BootstrapStatusResponse(BaseModel):
    status: Literal["ready", "needs_setup"]
    setup_complete: bool
    checks: list[BootstrapCheck]
    next_steps: list[str] = Field(default_factory=list)
    docs: dict[str, str] = Field(default_factory=dict)


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
    recent_errors: list[str] = Field(default_factory=list)
    failure_count: int = 0
    last_transition_at: str | None = None
    state_reason: str | None = None


class AccountsResponse(BaseModel):
    items: list[AccountSummary]


class SessionCreateRequest(BaseModel):
    account_id: str | None = None
    routing_policy: str = "sticky"
    model: str | None = None
    gem: str | None = None
    allow_failover: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionUpdateRequest(BaseModel):
    title: str | None = None


class SessionResponse(BaseModel):
    session_id: str
    account_id: str
    routing_policy: str
    status: str
    model: str | None = None
    gem: str | None = None
    allow_failover: bool = False
    title: str | None = None
    gemini_metadata: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


class MessageTextPart(BaseModel):
    type: Literal["text"]
    text: str


class MessageAssetPart(BaseModel):
    type: Literal["asset"]
    asset_id: str


MessagePart = Annotated[MessageTextPart | MessageAssetPart, Field(discriminator="type")]


class AssetResponse(BaseModel):
    asset_id: str
    owner_subject: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    status: str
    storage_backend: str
    provider_ref: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UploadResponse(BaseModel):
    asset: AssetResponse


class MessageRequest(BaseModel):
    session_id: str
    message: str | None = None
    parts: list[MessagePart] = Field(default_factory=list)
    stream: bool = False
    temporary: bool | None = None
    idempotency_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_payload(self) -> "MessageRequest":
        if self.message and self.parts:
            raise ValueError("Provide either 'message' or 'parts', not both.")
        if not self.message and not self.parts:
            raise ValueError("A message requires either 'message' or 'parts'.")
        return self


class MessageMedia(BaseModel):
    asset_id: str
    mime_type: str
    filename: str
    media_type: Literal["image"]
    content_url: str


class MessageResponsePart(BaseModel):
    type: Literal["text", "asset"]
    text: str | None = None
    asset: AssetResponse | None = None


class MessageResponse(BaseModel):
    session_id: str
    account_id: str
    content: str
    parts: list[MessageResponsePart] = Field(default_factory=list)
    media: list[MessageMedia] = Field(default_factory=list)
    cached: bool = False
    message_id: str
    user_message_id: str | None = None
    gemini_metadata: list[str] = Field(default_factory=list)
    created_at: str | None = None


class BatchItem(BaseModel):
    external_id: str
    prompt: str
    session_id: str | None = None
    account_id: str | None = None


class BatchRequest(BaseModel):
    items: list[BatchItem]


class BatchItemResponse(BaseModel):
    external_id: str
    status: str
    session_id: str | None = None
    requested_account_id: str | None = None
    account_id: str | None = None
    response_text: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class BatchResponse(BaseModel):
    batch_id: str
    status: str
    total_items: int
    completed_items: int
    failed_items: int
    requested_account_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    items: list[BatchItemResponse] = Field(default_factory=list)


class AdminActionResponse(BaseModel):
    account_id: str
    action: str
    state: str
    detail: str


class SessionHistoryItem(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    parts: list[MessageResponsePart] = Field(default_factory=list)
    media: list[MessageMedia] = Field(default_factory=list)
    created_at: str | None = None
    idempotency_key: str | None = None


class SessionHistoryResponse(BaseModel):
    session_id: str
    items: list[SessionHistoryItem]
