from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, StreamingResponse

from ...core.errors import ServiceError
from ...schemas.common import (
    AccountsResponse,
    BatchRequest,
    MessageRequest,
    MessageResponse,
    SessionCreateRequest,
    SessionHistoryResponse,
    SessionResponse,
)
from ...services.account_pool import AccountPool
from ...services.chat_service import ChatService
from ..dependencies import get_account_pool, get_chat_service, require_api_token

router = APIRouter()


def _not_implemented(name: str) -> ServiceError:
    return ServiceError(
        status_code=501,
        code="not_implemented",
        message=f"{name} is planned but not implemented in the current phase.",
    )


@router.get("/v1/accounts", response_model=AccountsResponse, tags=["accounts"])
async def list_accounts(
    _: str | None = Depends(require_api_token),
    pool: AccountPool = Depends(get_account_pool),
) -> AccountsResponse:
    return AccountsResponse(items=await pool.list_account_summaries())


@router.post("/v1/sessions", response_model=SessionResponse, tags=["sessions"])
async def create_session(
    request: SessionCreateRequest,
    _: str | None = Depends(require_api_token),
    chat_service: ChatService = Depends(get_chat_service),
) -> SessionResponse:
    return await chat_service.create_session(request)


@router.get("/v1/sessions/{session_id}", response_model=SessionResponse, tags=["sessions"])
async def get_session(
    session_id: str,
    _: str | None = Depends(require_api_token),
    chat_service: ChatService = Depends(get_chat_service),
) -> SessionResponse:
    return await chat_service.get_session(session_id)


@router.get(
    "/v1/sessions/{session_id}/history",
    response_model=SessionHistoryResponse,
    tags=["sessions"],
)
async def get_session_history(
    session_id: str,
    _: str | None = Depends(require_api_token),
    chat_service: ChatService = Depends(get_chat_service),
) -> SessionHistoryResponse:
    return await chat_service.get_history(session_id)


@router.post("/v1/messages", response_model=MessageResponse, tags=["messages"])
async def send_message(
    request: MessageRequest,
    _: str | None = Depends(require_api_token),
    chat_service: ChatService = Depends(get_chat_service),
) -> MessageResponse:
    return await chat_service.send_message(request)


@router.post("/v1/messages:stream", tags=["messages"])
async def stream_message(
    request: MessageRequest,
    _: str | None = Depends(require_api_token),
    chat_service: ChatService = Depends(get_chat_service),
):
    return StreamingResponse(
        chat_service.stream_message(request),
        media_type="text/event-stream",
    )


@router.post("/v1/batches", tags=["batches"])
async def execute_batch(
    _: BatchRequest,
    __: str | None = Depends(require_api_token),
):
    raise _not_implemented("Batch execution")


@router.get("/admin", response_class=HTMLResponse, tags=["admin"])
async def admin_page(_: str | None = Depends(require_api_token)) -> HTMLResponse:
    html = """
    <html>
      <head><title>Gemini Internal Service Admin</title></head>
      <body>
        <h1>Gemini Internal Service</h1>
        <p>Admin UI is not implemented yet.</p>
        <p>Current phase provides service bootstrap, auth, health checks, and API contract placeholders.</p>
      </body>
    </html>
    """
    return HTMLResponse(content=html)
