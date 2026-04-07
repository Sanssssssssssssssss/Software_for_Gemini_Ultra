from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .api.routes.health import router as health_router
from .api.routes.service import router as service_router
from .api.routes.ui import router as ui_router
from .core.bootstrap import evaluate_bootstrap_status
from .core.config import get_settings
from .core.errors import ServiceError
from .core.logging import configure_logging
from .core.middleware import RequestContextMiddleware
from .core.telemetry import TelemetryService
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
    app.state.chat_service = ChatService(
        pool=pool,
        repository=repository,
        telemetry=app.state.telemetry,
    )
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
    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
    telemetry = TelemetryService()
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
    app.state.templates = templates
    app.state.telemetry = telemetry
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.ui_session_secret,
        session_cookie=settings.ui_session_cookie,
        same_site="lax",
        https_only=settings.env != "development",
        max_age=60 * 60 * 8,
    )
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(ServiceError)
    async def handle_service_error(request: Request, exc: ServiceError):
        if (
            exc.code == "ui_not_authenticated"
            and request.url.path.startswith(("/ui", "/admin"))
            and request.url.path != "/ui/login"
        ):
            return RedirectResponse(url="/ui/login", status_code=303)
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
    app.include_router(ui_router)

    @app.get("/", tags=["meta"])
    async def root(request: Request) -> RedirectResponse:
        bootstrap = evaluate_bootstrap_status(
            settings,
            pool=getattr(app.state, "account_pool", None),
        )
        if not bootstrap.setup_complete:
            return RedirectResponse(url="/setup", status_code=307)
        if request.session.get("ui_user") == settings.ui_username:
            return RedirectResponse(url="/ui/chat", status_code=307)
        return RedirectResponse(url="/ui/login", status_code=307)

    return app
