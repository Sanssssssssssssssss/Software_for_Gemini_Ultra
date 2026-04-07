from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from ...core.bootstrap import evaluate_bootstrap_status
from ...core.config import get_settings
from ...core.security import AuthContext
from ...core.telemetry import TelemetryService
from ...schemas.common import BootstrapStatusResponse, MessageRequest, SessionCreateRequest
from ...services.account_pool import AccountPool
from ...services.batch_service import BatchService
from ...services.chat_service import ChatService
from ..dependencies import (
    get_account_pool,
    get_batch_service,
    get_chat_service,
    get_telemetry,
    require_ui_admin,
    require_ui_user,
)

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


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(
    request: Request,
    pool: AccountPool = Depends(get_account_pool),
) -> HTMLResponse:
    status = evaluate_bootstrap_status(get_settings(), pool=pool)
    return _templates(request).TemplateResponse(
        request,
        "setup.html",
        {
            "request": request,
            "status": status,
        },
    )


@router.get("/setup/status", response_model=BootstrapStatusResponse)
async def setup_status(
    pool: AccountPool = Depends(get_account_pool),
) -> BootstrapStatusResponse:
    return evaluate_bootstrap_status(get_settings(), pool=pool)


@router.post("/ui/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
) -> HTMLResponse:
    body = (await request.body()).decode("utf-8")
    form = parse_qs(body, keep_blank_values=True)
    username = (form.get("username") or [""])[0]
    password = (form.get("password") or [""])[0]
    settings = get_settings()
    role: str | None = None
    if username == settings.ui_username and password == settings.ui_password:
        role = "admin"
    elif (
        settings.ui_user_username
        and settings.ui_user_password
        and username == settings.ui_user_username
        and password == settings.ui_user_password
    ):
        role = "user"

    if role is not None:
        request.session["ui_user"] = username
        request.session["ui_role"] = role
        return RedirectResponse(url="/ui/chat", status_code=303)

    return _templates(request).TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "error": "Invalid username or password.",
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
    auth: AuthContext = Depends(require_ui_user),
    pool: AccountPool = Depends(get_account_pool),
    chat_service: ChatService = Depends(get_chat_service),
) -> HTMLResponse:
    accounts = await pool.list_account_summaries() if auth.is_admin else []
    return _templates(request).TemplateResponse(
        request,
        "chat.html",
        {
            "request": request,
            "accounts": accounts,
            "sessions": await chat_service.list_sessions(auth=auth, limit=30),
            "ui_user": auth.subject,
            "ui_role": auth.role,
            "ui_is_admin": auth.is_admin,
        },
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    auth: AuthContext = Depends(require_ui_admin),
    pool: AccountPool = Depends(get_account_pool),
    chat_service: ChatService = Depends(get_chat_service),
    telemetry: TelemetryService = Depends(get_telemetry),
) -> HTMLResponse:
    sessions = await chat_service.list_sessions(auth=auth, limit=50)
    telemetry.update_runtime(
        ready_accounts=pool.ready_account_count,
        total_accounts=pool.inventory_count,
        sessions=len(sessions),
        messages=await chat_service.repository.count_messages(),
    )
    telemetry.update_account_pool(await pool.list_account_summaries(force_refresh=True))
    return _templates(request).TemplateResponse(
        request,
        "admin.html",
        {
            "request": request,
            "accounts": await pool.list_account_summaries(force_refresh=True),
            "sessions": sessions,
            "telemetry": telemetry.snapshot(),
            "ui_user": auth.subject,
            "ui_role": auth.role,
        },
    )


@router.post("/ui/admin/accounts/{account_id}/actions/{action}")
async def admin_account_action(
    account_id: str,
    action: str,
    _: AuthContext = Depends(require_ui_admin),
    pool: AccountPool = Depends(get_account_pool),
) -> RedirectResponse:
    if action == "clear-cooldown":
        await pool.clear_cooldown(account_id)
    elif action == "mark-reauth-required":
        await pool.mark_reauth_required(account_id, "marked by operator")
    elif action == "disable-runtime":
        await pool.disable_runtime(account_id, "disabled by operator")
    elif action == "enable-runtime":
        await pool.enable_runtime(account_id)
    elif action == "refresh":
        await pool.refresh_account(account_id)
    return RedirectResponse(url="/admin", status_code=303)


@router.get("/ui/api/bootstrap")
async def ui_bootstrap(
    auth: AuthContext = Depends(require_ui_user),
    pool: AccountPool = Depends(get_account_pool),
    chat_service: ChatService = Depends(get_chat_service),
):
    return {
        "accounts": (await pool.list_account_summaries()) if auth.is_admin else [],
        "sessions": (await chat_service.list_sessions(auth=auth, limit=30)),
        "is_admin": auth.is_admin,
    }


@router.post("/ui/api/sessions")
async def ui_create_session(
    request: Request,
    auth: AuthContext = Depends(require_ui_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    payload = await request.json()
    if not auth.is_admin:
        payload["account_id"] = None
        payload["allow_failover"] = False
    return await chat_service.create_session(SessionCreateRequest.model_validate(payload), auth=auth)


@router.get("/ui/api/sessions/{session_id}/history")
async def ui_get_history(
    session_id: str,
    auth: AuthContext = Depends(require_ui_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    return await chat_service.get_history(session_id, auth=auth)


@router.post("/ui/api/messages")
async def ui_send_message(
    request: Request,
    auth: AuthContext = Depends(require_ui_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    payload = await request.json()
    return await chat_service.send_message(MessageRequest.model_validate(payload), auth=auth)


@router.post("/ui/api/messages:stream")
async def ui_stream_message(
    request: Request,
    auth: AuthContext = Depends(require_ui_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    payload = await request.json()
    return StreamingResponse(
        chat_service.stream_message(MessageRequest.model_validate(payload), auth=auth),
        media_type="text/event-stream",
    )


@router.get("/ui/api/batches/{batch_id}")
async def ui_get_batch(
    batch_id: str,
    auth: AuthContext = Depends(require_ui_user),
    batch_service: BatchService = Depends(get_batch_service),
):
    return await batch_service.get_batch(auth=auth, batch_id=batch_id)
