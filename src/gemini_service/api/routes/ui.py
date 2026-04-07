from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from ...core.bootstrap import evaluate_bootstrap_status
from ...core.config import get_settings
from ...core.errors import ServiceError
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


def _frontend_index(request: Request) -> Path | None:
    index_path = getattr(request.app.state, "frontend_index", None)
    return index_path if index_path and Path(index_path).exists() else None


def _serve_frontend_page(request: Request) -> FileResponse | None:
    index_path = _frontend_index(request)
    if index_path is None or not get_settings().ui_spa_enabled:
        return None
    return FileResponse(index_path)


class UiLoginPayload(BaseModel):
    username: str
    password: str


def _resolve_ui_role(username: str, password: str) -> str | None:
    settings = get_settings()
    if username == settings.ui_username and password == settings.ui_password:
        return "admin"
    if (
        settings.ui_user_username
        and settings.ui_user_password
        and username == settings.ui_user_username
        and password == settings.ui_user_password
    ):
        return "user"
    return None


async def _apply_admin_account_action(
    *,
    account_id: str,
    action: str,
    pool: AccountPool,
    telemetry: TelemetryService,
) -> dict[str, str]:
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
    return {
        "account_id": runtime.config.account_id,
        "action": action,
        "state": runtime.effective_state.value,
        "detail": detail,
    }


@router.get("/ui/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    if request.session.get("ui_user"):
        return RedirectResponse(url="/ui/chat", status_code=303)
    frontend_page = _serve_frontend_page(request)
    if frontend_page is not None:
        return frontend_page
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
    frontend_page = _serve_frontend_page(request)
    if frontend_page is not None:
        return frontend_page
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
    role = _resolve_ui_role(username, password)

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


@router.get("/ui/api/me")
async def ui_me(request: Request):
    user = request.session.get("ui_user")
    role = request.session.get("ui_role")
    return {
        "authenticated": bool(user and role),
        "subject": user,
        "role": role,
        "is_admin": role == "admin",
    }


@router.post("/ui/api/login")
async def ui_login_api(
    payload: UiLoginPayload,
    request: Request,
):
    role = _resolve_ui_role(payload.username, payload.password)
    if role is None:
        raise ServiceError(
            status_code=401,
            code="ui_login_failed",
            message="Invalid username or password.",
        )

    request.session["ui_user"] = payload.username
    request.session["ui_role"] = role
    return {
        "authenticated": True,
        "subject": payload.username,
        "role": role,
        "is_admin": role == "admin",
        "redirect_to": "/ui/chat",
    }


@router.post("/ui/api/logout")
async def ui_logout_api(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/ui/api/setup/status", response_model=BootstrapStatusResponse)
async def ui_setup_status(
    pool: AccountPool = Depends(get_account_pool),
) -> BootstrapStatusResponse:
    return evaluate_bootstrap_status(get_settings(), pool=pool)


@router.get("/ui/chat", response_class=HTMLResponse)
async def chat_page(
    request: Request,
    auth: AuthContext = Depends(require_ui_user),
    pool: AccountPool = Depends(get_account_pool),
    chat_service: ChatService = Depends(get_chat_service),
) -> HTMLResponse:
    frontend_page = _serve_frontend_page(request)
    if frontend_page is not None:
        return frontend_page
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
    frontend_page = _serve_frontend_page(request)
    if frontend_page is not None:
        return frontend_page
    sessions = await chat_service.list_sessions(auth=auth, limit=50)
    telemetry.update_runtime(
        ready_accounts=pool.ready_account_count,
        total_accounts=pool.inventory_count,
        sessions=len(sessions),
        messages=await chat_service.repository.count_messages(),
        batches=await chat_service.repository.count_batches(),
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
    telemetry: TelemetryService = Depends(get_telemetry),
) -> RedirectResponse:
    await _apply_admin_account_action(
        account_id=account_id,
        action=action,
        pool=pool,
        telemetry=telemetry,
    )
    return RedirectResponse(url="/admin", status_code=303)


@router.get("/ui/api/sessions/{session_id}")
async def ui_get_session(
    session_id: str,
    auth: AuthContext = Depends(require_ui_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    return await chat_service.get_session(session_id, auth=auth)


@router.get("/ui/api/admin/overview")
async def ui_admin_overview(
    auth: AuthContext = Depends(require_ui_admin),
    pool: AccountPool = Depends(get_account_pool),
    chat_service: ChatService = Depends(get_chat_service),
    telemetry: TelemetryService = Depends(get_telemetry),
):
    sessions = await chat_service.list_sessions(auth=auth, limit=50)
    accounts = await pool.list_account_summaries(force_refresh=True)
    telemetry.update_runtime(
        ready_accounts=pool.ready_account_count,
        total_accounts=pool.inventory_count,
        sessions=len(sessions),
        messages=await chat_service.repository.count_messages(),
        batches=await chat_service.repository.count_batches(),
    )
    telemetry.update_account_pool(accounts)
    return {
        "accounts": accounts,
        "sessions": sessions,
        "telemetry": {
            "total_requests": telemetry.total_requests,
            "total_errors": telemetry.total_errors,
            "active_requests": telemetry.active_requests,
            "account_ready": telemetry.account_ready,
            "account_total": telemetry.account_total,
            "chat_sessions": telemetry.chat_sessions,
            "chat_messages": telemetry.chat_messages,
            "chat_batches": telemetry.chat_batches,
            "batch_workers_active": telemetry.batch_workers_active,
            "session_failovers_total": telemetry.session_failovers_total,
            "account_state_counts": dict(telemetry.account_state_counts),
            "account_queue_depth": dict(telemetry.account_queue_depth),
            "account_in_flight": dict(telemetry.account_in_flight),
        },
    }


@router.post("/ui/api/admin/accounts/{account_id}/actions/{action}")
async def ui_admin_account_action(
    account_id: str,
    action: str,
    _: AuthContext = Depends(require_ui_admin),
    pool: AccountPool = Depends(get_account_pool),
    telemetry: TelemetryService = Depends(get_telemetry),
):
    return await _apply_admin_account_action(
        account_id=account_id,
        action=action,
        pool=pool,
        telemetry=telemetry,
    )


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
