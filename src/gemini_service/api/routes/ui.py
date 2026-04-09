from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel

from ...core.bootstrap import evaluate_bootstrap_status
from ...core.config import get_settings
from ...core.errors import ServiceError
from ...core.security import AuthContext
from ...core.telemetry import TelemetryService
from ...schemas.admin import AdminAccountUpsertRequest
from ...schemas.common import BootstrapStatusResponse, MessageRequest, SessionCreateRequest, SessionUpdateRequest
from ...services.account_pool import AccountPool
from ...services.admin_console_service import AdminConsoleService
from ...services.asset_service import AssetService
from ...services.batch_service import BatchService
from ...services.chat_service import ChatService
from ..dependencies import (
    get_account_pool,
    get_admin_console_service,
    get_asset_service,
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
        assets=await chat_service.repository.count_assets(),
    )
    telemetry.update_account_pool(await pool.list_account_summaries(force_refresh=True))
    telemetry.update_asset_runtime(await chat_service.repository.count_assets_by_status())
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


@router.patch("/ui/api/sessions/{session_id}")
async def ui_update_session(
    session_id: str,
    payload: SessionUpdateRequest,
    auth: AuthContext = Depends(require_ui_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    return await chat_service.update_session(session_id, payload, auth=auth)


@router.delete("/ui/api/sessions/{session_id}")
async def ui_delete_session(
    session_id: str,
    auth: AuthContext = Depends(require_ui_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    await chat_service.delete_session(session_id, auth=auth)
    return {"ok": True, "session_id": session_id}


@router.get("/ui/api/admin/overview")
async def ui_admin_overview(
    auth: AuthContext = Depends(require_ui_admin),
    admin_console: AdminConsoleService = Depends(get_admin_console_service),
):
    dashboard = await admin_console.build_dashboard(auth)
    return dashboard.model_dump(mode="json")


@router.get("/ui/api/admin/dashboard")
async def ui_admin_dashboard(
    auth: AuthContext = Depends(require_ui_admin),
    admin_console: AdminConsoleService = Depends(get_admin_console_service),
):
    dashboard = await admin_console.build_dashboard(auth)
    return dashboard.model_dump(mode="json")


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


@router.post("/ui/api/admin/accounts")
async def ui_admin_upsert_account(
    payload: AdminAccountUpsertRequest,
    _: AuthContext = Depends(require_ui_admin),
    admin_console: AdminConsoleService = Depends(get_admin_console_service),
):
    account = await admin_console.upsert_account(payload)
    return account.model_dump(mode="json")


@router.patch("/ui/api/admin/accounts/{account_id}")
async def ui_admin_patch_account(
    account_id: str,
    payload: AdminAccountUpsertRequest,
    _: AuthContext = Depends(require_ui_admin),
    admin_console: AdminConsoleService = Depends(get_admin_console_service),
):
    patched = payload.model_copy(update={"account_id": account_id})
    account = await admin_console.upsert_account(patched)
    return account.model_dump(mode="json")


@router.delete("/ui/api/admin/accounts/{account_id}")
async def ui_admin_delete_account(
    account_id: str,
    _: AuthContext = Depends(require_ui_admin),
    admin_console: AdminConsoleService = Depends(get_admin_console_service),
):
    return await admin_console.delete_account(account_id)


@router.get("/ui/api/admin/reauth-jobs")
async def ui_admin_list_reauth_jobs(
    _: AuthContext = Depends(require_ui_admin),
    admin_console: AdminConsoleService = Depends(get_admin_console_service),
):
    return {"items": [item.model_dump(mode="json") for item in admin_console.list_reauth_jobs()]}


@router.post("/ui/api/admin/accounts/{account_id}/reauth")
async def ui_admin_start_reauth(
    account_id: str,
    _: AuthContext = Depends(require_ui_admin),
    admin_console: AdminConsoleService = Depends(get_admin_console_service),
):
    job = await admin_console.start_reauth_job(account_id)
    return job.model_dump(mode="json")


@router.post("/ui/api/admin/reauth-jobs/{job_id}/complete")
async def ui_admin_complete_reauth(
    job_id: str,
    _: AuthContext = Depends(require_ui_admin),
    admin_console: AdminConsoleService = Depends(get_admin_console_service),
):
    job = await admin_console.complete_reauth_job(job_id)
    return job.model_dump(mode="json")


@router.post("/ui/api/admin/reauth-jobs/{job_id}/cancel")
async def ui_admin_cancel_reauth(
    job_id: str,
    _: AuthContext = Depends(require_ui_admin),
    admin_console: AdminConsoleService = Depends(get_admin_console_service),
):
    job = await admin_console.cancel_reauth_job(job_id)
    return job.model_dump(mode="json")


@router.get("/ui/api/admin/sessions/{session_id}/export")
async def ui_admin_export_session(
    session_id: str,
    format: str = Query("json"),
    auth: AuthContext = Depends(require_ui_admin),
    admin_console: AdminConsoleService = Depends(get_admin_console_service),
):
    filename, content, media_type = await admin_console.export_session_bytes(
        session_id=session_id,
        auth=auth,
        export_format=format,
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/ui/api/admin/sessions/export")
async def ui_admin_export_sessions(
    format: str = Query("json"),
    owner_subject: str | None = Query(None),
    account_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    auth: AuthContext = Depends(require_ui_admin),
    admin_console: AdminConsoleService = Depends(get_admin_console_service),
):
    filename, content, media_type = await admin_console.export_sessions_bytes(
        auth=auth,
        export_format=format,
        owner_subject=owner_subject,
        account_id=account_id,
        limit=limit,
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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


@router.post("/ui/api/uploads")
async def ui_create_upload(
    file: UploadFile = File(...),
    temporary: bool = Form(True),
    auth: AuthContext = Depends(require_ui_user),
    asset_service: AssetService = Depends(get_asset_service),
):
    asset = await asset_service.create_upload(
        auth=auth,
        upload=file,
        temporary=temporary,
    )
    return {
        "asset": {
            "asset_id": asset.id,
            "owner_subject": asset.owner_subject,
            "filename": asset.filename,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
            "sha256": asset.sha256,
            "status": asset.status,
            "storage_backend": asset.storage_backend,
            "provider_ref": asset.provider_ref,
            "created_at": asset.created_at.isoformat() if asset.created_at else None,
            "expires_at": asset.expires_at.isoformat() if asset.expires_at else None,
        }
    }


@router.get("/ui/api/assets/{asset_id}")
async def ui_get_asset(
    asset_id: str,
    auth: AuthContext = Depends(require_ui_user),
    asset_service: AssetService = Depends(get_asset_service),
):
    asset = await asset_service.get_asset_for_read(asset_id=asset_id, auth=auth)
    return {
        "asset_id": asset.id,
        "owner_subject": asset.owner_subject,
        "filename": asset.filename,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "sha256": asset.sha256,
        "status": asset.status,
        "storage_backend": asset.storage_backend,
        "provider_ref": asset.provider_ref,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "expires_at": asset.expires_at.isoformat() if asset.expires_at else None,
    }


@router.get("/ui/api/assets/{asset_id}/content")
async def ui_get_asset_content(
    asset_id: str,
    auth: AuthContext = Depends(require_ui_user),
    asset_service: AssetService = Depends(get_asset_service),
):
    asset = await asset_service.get_asset_for_read(asset_id=asset_id, auth=auth)
    if asset.status != "available":
        raise ServiceError(
            status_code=409,
            code="asset_not_available",
            message="The requested asset is not available for download.",
        )
    path = asset_service.resolve_asset_path(asset)
    return FileResponse(path, media_type=asset.mime_type, filename=asset.filename)


@router.get("/ui/api/batches/{batch_id}")
async def ui_get_batch(
    batch_id: str,
    auth: AuthContext = Depends(require_ui_user),
    batch_service: BatchService = Depends(get_batch_service),
):
    return await batch_service.get_batch(auth=auth, batch_id=batch_id)
