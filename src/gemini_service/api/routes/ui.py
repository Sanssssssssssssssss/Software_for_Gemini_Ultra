from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from ...core.config import get_settings
from ...core.errors import ServiceError
from ...core.telemetry import TelemetryService
from ...schemas.common import MessageRequest, SessionCreateRequest
from ...services.account_pool import AccountPool
from ...services.chat_service import ChatService
from ..dependencies import get_account_pool, get_chat_service, get_telemetry, require_ui_user

router = APIRouter(tags=["ui"])


def _templates(request: Request):
    return request.app.state.templates


@router.get("/ui/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    if request.session.get("ui_user"):
        return RedirectResponse(url="/ui/chat", status_code=303)
    return _templates(request).TemplateResponse(
        request,
        "login.html",
        {"request": request, "error": None},
    )


@router.post("/ui/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
) -> HTMLResponse:
    body = (await request.body()).decode("utf-8")
    form = parse_qs(body, keep_blank_values=True)
    username = (form.get("username") or [""])[0]
    password = (form.get("password") or [""])[0]
    settings = get_settings()
    if username == settings.ui_username and password == settings.ui_password:
        request.session["ui_user"] = username
        return RedirectResponse(url="/ui/chat", status_code=303)
    return _templates(request).TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "error": "用户名或密码错误。",
        },
        status_code=401,
    )


@router.post("/ui/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/ui/login", status_code=303)


@router.get("/ui/chat", response_class=HTMLResponse)
async def chat_page(
    request: Request,
    _: str = Depends(require_ui_user),
    pool: AccountPool = Depends(get_account_pool),
    chat_service: ChatService = Depends(get_chat_service),
) -> HTMLResponse:
    return _templates(request).TemplateResponse(
        request,
        "chat.html",
        {
            "request": request,
            "accounts": await pool.list_account_summaries(),
            "sessions": await chat_service.list_sessions(limit=30),
            "ui_user": request.session.get("ui_user"),
        },
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    _: str = Depends(require_ui_user),
    pool: AccountPool = Depends(get_account_pool),
    chat_service: ChatService = Depends(get_chat_service),
    telemetry: TelemetryService = Depends(get_telemetry),
) -> HTMLResponse:
    sessions = await chat_service.list_sessions(limit=50)
    telemetry.update_runtime(
        ready_accounts=pool.ready_account_count,
        total_accounts=pool.inventory_count,
        sessions=len(sessions),
        messages=await chat_service.repository.count_messages(),
    )
    return _templates(request).TemplateResponse(
        request,
        "admin.html",
        {
            "request": request,
            "accounts": await pool.list_account_summaries(force_refresh=True),
            "sessions": sessions,
            "telemetry": telemetry.snapshot(),
            "ui_user": request.session.get("ui_user"),
        },
    )


@router.get("/ui/api/bootstrap")
async def ui_bootstrap(
    _: str = Depends(require_ui_user),
    pool: AccountPool = Depends(get_account_pool),
    chat_service: ChatService = Depends(get_chat_service),
):
    return {
        "accounts": (await pool.list_account_summaries()),
        "sessions": (await chat_service.list_sessions(limit=30)),
    }


@router.post("/ui/api/sessions")
async def ui_create_session(
    request: Request,
    _: str = Depends(require_ui_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    payload = await request.json()
    return await chat_service.create_session(SessionCreateRequest.model_validate(payload))


@router.get("/ui/api/sessions/{session_id}/history")
async def ui_get_history(
    session_id: str,
    _: str = Depends(require_ui_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    return await chat_service.get_history(session_id)


@router.post("/ui/api/messages")
async def ui_send_message(
    request: Request,
    _: str = Depends(require_ui_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    payload = await request.json()
    return await chat_service.send_message(MessageRequest.model_validate(payload))


@router.post("/ui/api/messages:stream")
async def ui_stream_message(
    request: Request,
    _: str = Depends(require_ui_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    payload = await request.json()
    return StreamingResponse(
        chat_service.stream_message(MessageRequest.model_validate(payload)),
        media_type="text/event-stream",
    )
