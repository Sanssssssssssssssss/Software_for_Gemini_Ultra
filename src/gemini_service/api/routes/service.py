from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from ...core.errors import ServiceError
from ...schemas.common import (
    AccountsResponse,
    BatchRequest,
    MessageRequest,
    SessionCreateRequest,
    SessionHistoryResponse,
    SessionResponse,
)
from ..dependencies import require_api_token

router = APIRouter()


def _not_implemented(name: str) -> ServiceError:
    return ServiceError(
        status_code=501,
        code="not_implemented",
        message=f"{name} is planned but not implemented in the current phase.",
    )


@router.get("/v1/accounts", response_model=AccountsResponse, tags=["accounts"])
async def list_accounts(_: str | None = Depends(require_api_token)) -> AccountsResponse:
    return AccountsResponse(items=[])


@router.post("/v1/sessions", response_model=SessionResponse, tags=["sessions"])
async def create_session(
    _: SessionCreateRequest,
    __: str | None = Depends(require_api_token),
) -> SessionResponse:
    raise _not_implemented("Session creation")


@router.get("/v1/sessions/{session_id}", response_model=SessionResponse, tags=["sessions"])
async def get_session(
    session_id: str,
    _: str | None = Depends(require_api_token),
) -> SessionResponse:
    raise _not_implemented(f"Session lookup for {session_id}")


@router.get(
    "/v1/sessions/{session_id}/history",
    response_model=SessionHistoryResponse,
    tags=["sessions"],
)
async def get_session_history(
    session_id: str,
    _: str | None = Depends(require_api_token),
) -> SessionHistoryResponse:
    raise _not_implemented(f"Session history for {session_id}")


@router.post("/v1/messages", tags=["messages"])
async def send_message(
    _: MessageRequest,
    __: str | None = Depends(require_api_token),
):
    raise _not_implemented("Non-streaming message send")


@router.post("/v1/messages:stream", tags=["messages"])
async def stream_message(
    _: MessageRequest,
    __: str | None = Depends(require_api_token),
):
    raise _not_implemented("Streaming message send")


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
