from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from ...core.errors import ServiceError
from ...core.security import AuthContext
from ...schemas.common import (
    AccountsResponse,
    AdminActionResponse,
    AssetResponse,
    BatchRequest,
    BatchResponse,
    MessageRequest,
    MessageResponse,
    SessionCreateRequest,
    SessionHistoryResponse,
    SessionResponse,
    UploadResponse,
)
from ...services.account_pool import AccountPool
from ...services.asset_service import AssetService
from ...services.batch_service import BatchService
from ...services.chat_service import ChatService
from ..dependencies import (
    get_account_pool,
    get_asset_service,
    get_batch_service,
    get_chat_service,
    get_telemetry,
    require_admin_api_token,
    require_api_token,
)
from ...core.telemetry import TelemetryService

router = APIRouter()


def asset_service_response(asset) -> AssetResponse:
    metadata_loader = getattr(asset, "metadata_json", "{}")
    import json

    try:
        metadata = json.loads(metadata_loader or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return AssetResponse(
        asset_id=asset.id,
        owner_subject=asset.owner_subject,
        filename=asset.filename,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        status=asset.status,
        storage_backend=asset.storage_backend,
        provider_ref=asset.provider_ref,
        created_at=asset.created_at.isoformat() if asset.created_at else None,
        expires_at=asset.expires_at.isoformat() if asset.expires_at else None,
        metadata=metadata,
    )


@router.post("/v1/uploads", response_model=UploadResponse, tags=["assets"])
async def create_upload(
    file: UploadFile = File(...),
    temporary: bool = Form(True),
    auth: AuthContext | None = Depends(require_api_token),
    asset_service: AssetService = Depends(get_asset_service),
) -> UploadResponse:
    assert auth is not None
    asset = await asset_service.create_upload(
        auth=auth,
        upload=file,
        temporary=temporary,
    )
    return UploadResponse(asset=asset_service_response(asset))


@router.get("/v1/assets/{asset_id}", response_model=AssetResponse, tags=["assets"])
async def get_asset(
    asset_id: str,
    auth: AuthContext | None = Depends(require_api_token),
    asset_service: AssetService = Depends(get_asset_service),
) -> AssetResponse:
    assert auth is not None
    asset = await asset_service.get_asset_for_read(asset_id=asset_id, auth=auth)
    return asset_service_response(asset)


@router.get("/v1/assets/{asset_id}/content", tags=["assets"])
async def get_asset_content(
    asset_id: str,
    auth: AuthContext | None = Depends(require_api_token),
    asset_service: AssetService = Depends(get_asset_service),
):
    assert auth is not None
    asset = await asset_service.get_asset_for_read(asset_id=asset_id, auth=auth)
    if asset.status != "available":
        raise ServiceError(
            status_code=409,
            code="asset_not_available",
            message="The requested asset is not available for download.",
        )
    path = asset_service.resolve_asset_path(asset)
    return FileResponse(path, media_type=asset.mime_type, filename=asset.filename)


@router.get("/v1/accounts", response_model=AccountsResponse, tags=["accounts"])
async def list_accounts(
    _: AuthContext | None = Depends(require_api_token),
    pool: AccountPool = Depends(get_account_pool),
) -> AccountsResponse:
    return AccountsResponse(items=await pool.list_account_summaries())


@router.post("/v1/sessions", response_model=SessionResponse, tags=["sessions"])
async def create_session(
    request: SessionCreateRequest,
    auth: AuthContext | None = Depends(require_api_token),
    chat_service: ChatService = Depends(get_chat_service),
) -> SessionResponse:
    assert auth is not None
    return await chat_service.create_session(request, auth=auth)


@router.get("/v1/sessions/{session_id}", response_model=SessionResponse, tags=["sessions"])
async def get_session(
    session_id: str,
    auth: AuthContext | None = Depends(require_api_token),
    chat_service: ChatService = Depends(get_chat_service),
) -> SessionResponse:
    assert auth is not None
    return await chat_service.get_session(session_id, auth=auth)


@router.get(
    "/v1/sessions/{session_id}/history",
    response_model=SessionHistoryResponse,
    tags=["sessions"],
)
async def get_session_history(
    session_id: str,
    auth: AuthContext | None = Depends(require_api_token),
    chat_service: ChatService = Depends(get_chat_service),
) -> SessionHistoryResponse:
    assert auth is not None
    return await chat_service.get_history(session_id, auth=auth)


@router.post("/v1/messages", response_model=MessageResponse, tags=["messages"])
async def send_message(
    request: MessageRequest,
    auth: AuthContext | None = Depends(require_api_token),
    chat_service: ChatService = Depends(get_chat_service),
) -> MessageResponse:
    assert auth is not None
    return await chat_service.send_message(request, auth=auth)


@router.post("/v1/messages:stream", tags=["messages"])
async def stream_message(
    request: MessageRequest,
    auth: AuthContext | None = Depends(require_api_token),
    chat_service: ChatService = Depends(get_chat_service),
):
    assert auth is not None
    return StreamingResponse(
        chat_service.stream_message(request, auth=auth),
        media_type="text/event-stream",
    )


@router.post("/v1/batches", response_model=BatchResponse, tags=["batches"])
async def execute_batch(
    request: BatchRequest,
    auth: AuthContext | None = Depends(require_api_token),
    batch_service: BatchService = Depends(get_batch_service),
) -> BatchResponse:
    assert auth is not None
    return await batch_service.create_batch(auth=auth, request=request)


@router.get("/v1/batches/{batch_id}", response_model=BatchResponse, tags=["batches"])
async def get_batch(
    batch_id: str,
    auth: AuthContext | None = Depends(require_api_token),
    batch_service: BatchService = Depends(get_batch_service),
) -> BatchResponse:
    assert auth is not None
    return await batch_service.get_batch(auth=auth, batch_id=batch_id)


@router.post(
    "/v1/admin/accounts/{account_id}/actions/{action}",
    response_model=AdminActionResponse,
    tags=["admin"],
)
async def admin_account_action(
    account_id: str,
    action: str,
    _: AuthContext = Depends(require_admin_api_token),
    pool: AccountPool = Depends(get_account_pool),
    telemetry: TelemetryService = Depends(get_telemetry),
) -> AdminActionResponse:
    if action == "clear-cooldown":
        runtime = await pool.clear_cooldown(account_id)
        detail = "Cooldown cleared."
    elif action == "mark-reauth-required":
        runtime = await pool.mark_reauth_required(account_id, "marked by operator")
        detail = "Account marked as requiring reauthentication."
    elif action == "disable-runtime":
        runtime = await pool.disable_runtime(account_id, "disabled by operator")
        detail = "Runtime disabled."
    elif action == "enable-runtime":
        runtime = await pool.enable_runtime(account_id)
        detail = "Runtime enabled and refreshed."
    elif action == "refresh":
        runtime = await pool.refresh_account(account_id)
        detail = "Account refreshed."
    else:
        telemetry.record_admin_action(action, "unknown")
        raise ServiceError(
            status_code=404,
            code="admin_action_not_found",
            message=f"Unknown account action: {action}",
        )
    telemetry.record_admin_action(action, "success")

    return AdminActionResponse(
        account_id=runtime.config.account_id,
        action=action,
        state=runtime.effective_state.value,
        detail=detail,
    )
