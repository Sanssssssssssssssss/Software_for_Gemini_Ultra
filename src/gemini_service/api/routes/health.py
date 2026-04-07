from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, Response

from ...api.dependencies import get_account_pool, get_batch_service, get_chat_service, get_telemetry
from ...core.config import get_settings
from ...core.telemetry import TelemetryService
from ...schemas.common import HealthResponse, ReadinessCheck, ReadinessResponse
from ...services.batch_service import BatchService
from ...services.chat_service import ChatService

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
    chat_service = get_chat_service(request)
    batch_service = get_batch_service(request)
    await pool.refresh_if_due()
    inventory_present = pool.has_inventory()
    ready_accounts = pool.ready_account_count
    database_ok = await chat_service.repository.ping()
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
            name="database",
            status="pass" if database_ok else "fail",
            detail="database connection is available" if database_ok else "database connection failed",
        ),
        ReadinessCheck(
            name="batch_worker",
            status="pass" if batch_service.is_available else "fail",
            detail="batch worker initialized" if batch_service.is_available else "batch worker unavailable",
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
    batch_service: BatchService = get_batch_service(request)
    accounts = await pool.list_account_summaries()
    sessions = await chat_service.repository.count_sessions()
    messages = await chat_service.repository.count_messages()
    batches = await chat_service.repository.count_batches()
    telemetry.update_runtime(
        ready_accounts=pool.ready_account_count,
        total_accounts=pool.inventory_count,
        sessions=sessions,
        messages=messages,
        batches=batches,
    )
    telemetry.update_account_pool(accounts)
    telemetry.update_batch_runtime(
        batch_status_counts=await chat_service.repository.count_batches_by_status(),
        active_workers=batch_service.active_task_count,
    )
    return Response(
        content=telemetry.render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
