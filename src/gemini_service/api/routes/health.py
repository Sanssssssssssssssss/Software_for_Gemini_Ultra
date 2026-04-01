from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from ...core.config import get_settings
from ...schemas.common import HealthResponse, ReadinessCheck, ReadinessResponse

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
async def readyz() -> JSONResponse:
    settings = get_settings()
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
            status="pass"
            if Path(settings.accounts_config_path).exists()
            else "fail",
            detail=f"loaded from {settings.accounts_config_path}"
            if Path(settings.accounts_config_path).exists()
            else "account inventory file not found yet",
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
