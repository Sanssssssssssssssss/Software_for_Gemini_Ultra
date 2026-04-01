from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, Response

from ...api.dependencies import get_account_pool, get_chat_service, get_telemetry
from ...core.config import get_settings
from ...schemas.common import HealthResponse, ReadinessCheck, ReadinessResponse
from ...services.chat_service import ChatService
from ...core.telemetry import TelemetryService

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        version="0.1.0",
    )


@router.get("/readyz", response_model=ReadinessResponse)
async def readyz(request: Request) -> JSONResponse:
    settings = get_settings()
    pool = get_account_pool(request)
    await pool.refresh_if_due()
    inventory_present = pool.has_inventory()
    ready_accounts = pool.ready_account_count
    checks = [
        ReadinessCheck(name="config_loaded", status="pass", detail="settings available"),
        ReadinessCheck(
            name="auth_tokens",
            status="pass"
            if (not settings.require_auth or bool(settings.api_token_values))
            else "fail",
            detail="bearer auth configured"
            if (not settings.require_auth or bool(settings.api_token_values))
            else "authentication is enabled but no tokens are configured",
        ),
        ReadinessCheck(
            name="accounts_config",
            status="pass" if inventory_present else "fail",
            detail=f"loaded from {settings.accounts_config_path}"
            if inventory_present
            else (
                f"account inventory file not found at {settings.accounts_config_path}"
                if not Path(settings.accounts_config_path).exists()
                else "account inventory is empty"
            ),
        ),
        ReadinessCheck(
            name="ready_accounts",
            status="pass" if ready_accounts >= settings.min_ready_accounts else "fail",
            detail=f"{ready_accounts}/{pool.inventory_count} accounts ready; minimum required is {settings.min_ready_accounts}",
        ),
    ]

    is_ready = all(check.status == "pass" for check in checks)
    payload = ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        checks=checks,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload.model_dump(mode="json"),
    )


@router.get("/metrics", include_in_schema=False)
async def metrics(
    request: Request,
) -> Response:
    telemetry: TelemetryService = get_telemetry(request)
    pool = get_account_pool(request)
    chat_service: ChatService = get_chat_service(request)
    sessions = await chat_service.repository.count_sessions()
    messages = await chat_service.repository.count_messages()
    telemetry.update_runtime(
        ready_accounts=pool.ready_account_count,
        total_accounts=pool.inventory_count,
        sessions=sessions,
        messages=messages,
    )
    return Response(
        content=telemetry.render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
