from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .api.routes.health import router as health_router
from .api.routes.service import router as service_router
from .core.config import get_settings
from .core.errors import ServiceError
from .core.logging import configure_logging
from .db.repository import ChatRepository
from .schemas.common import ApiError, ErrorResponse
from .services.account_pool import AccountPool
from .services.chat_service import ChatService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)
    pool = AccountPool(settings)
    repository = ChatRepository(settings.database_url)
    await repository.start()
    await pool.start()
    app.state.account_pool = pool
    app.state.chat_service = ChatService(pool=pool, repository=repository)
    logging.getLogger("gemini_service").info(
        "service_startup",
        extra={
            "event": "service_startup",
            "env": settings.env,
            "accounts_config_path": settings.accounts_config_path,
            "database_url": settings.database_url,
            "require_auth": settings.require_auth,
            "inventory_count": pool.inventory_count,
        },
    )
    try:
        yield
    finally:
        await repository.close()
        await pool.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Gemini Internal Service",
        version="0.1.0",
        summary="Internal multi-account Gemini service built on a non-official web wrapper.",
        description=(
            "Production-oriented service shell around the upstream Gemini web wrapper. "
            "Endpoints that are not implemented yet return explicit 501 responses."
        ),
        docs_url="/docs" if settings.openapi_enabled else None,
        redoc_url="/redoc" if settings.openapi_enabled else None,
        openapi_url="/openapi.json" if settings.openapi_enabled else None,
        lifespan=lifespan,
    )

    @app.exception_handler(ServiceError)
    async def handle_service_error(_, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error=ApiError(
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                )
            ).model_dump(mode="json"),
        )

    app.include_router(health_router)
    app.include_router(service_router)

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        return {
            "service": settings.service_name,
            "status": "bootstrapping",
            "docs": "/docs" if settings.openapi_enabled else "disabled",
        }

    return app
