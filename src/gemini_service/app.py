from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
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
from .services.account_recovery_service import AccountRecoveryService
from .services.asset_cleanup_service import AssetCleanupService
from .services.asset_service import AssetService
from .services.admin_console_service import AdminConsoleService
from .services.batch_service import BatchService
from .services.chat_service import ChatService
from .storage.local import LocalAssetStorage


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)
    pool = AccountPool(settings)
    repository = ChatRepository(settings.database_url)
    asset_storage = LocalAssetStorage(settings.asset_root_path)
    await repository.start()
    await pool.start()
    app.state.account_pool = pool
    asset_service = AssetService(
        settings=settings,
        repository=repository,
        storage=asset_storage,
    )
    app.state.asset_service = asset_service
    asset_cleanup_service = AssetCleanupService(
        settings=settings,
        repository=repository,
        storage=asset_storage,
        telemetry=app.state.telemetry,
    )
    await asset_cleanup_service.start()
    app.state.asset_cleanup_service = asset_cleanup_service
    chat_service = ChatService(
        pool=pool,
        repository=repository,
        asset_service=asset_service,
        telemetry=app.state.telemetry,
    )
    batch_service = BatchService(repository=repository, chat_service=chat_service)
    await batch_service.start()
    app.state.chat_service = chat_service
    app.state.batch_service = batch_service
    account_recovery_service = AccountRecoveryService(
        settings=settings,
        pool=pool,
    )
    app.state.account_recovery_service = account_recovery_service
    admin_console_service = AdminConsoleService(
        settings=settings,
        repository=repository,
        pool=pool,
        chat_service=chat_service,
        asset_service=asset_service,
        recovery_service=account_recovery_service,
        telemetry=app.state.telemetry,
    )
    app.state.admin_console_service = admin_console_service
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
        await admin_console_service.close()
        await asset_cleanup_service.close()
        await batch_service.close()
        await repository.close()
        await pool.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app_root = Path(__file__).resolve().parent
    repo_root = app_root.parent.parent
    templates = Jinja2Templates(directory=str(app_root / "templates"))
    frontend_dist = repo_root / settings.frontend_dist_path
    frontend_index = frontend_dist / "index.html"
    frontend_assets = frontend_dist / "assets"
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
    app.state.frontend_dist = frontend_dist
    app.state.frontend_index = frontend_index if frontend_index.exists() else None
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.ui_session_secret,
        session_cookie=settings.ui_session_cookie,
        same_site="lax",
        https_only=settings.env not in {"development", "local", "local-mock", "test"},
        max_age=60 * 60 * 8,
    )
    app.add_middleware(RequestContextMiddleware)
    if settings.ui_spa_enabled and frontend_assets.exists():
        app.mount("/ui/assets", StaticFiles(directory=str(frontend_assets)), name="ui-assets")

    @app.exception_handler(ServiceError)
    async def handle_service_error(request: Request, exc: ServiceError):
        app.state.telemetry.record_service_error(exc.code)
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
        if request.session.get("ui_user"):
            return RedirectResponse(url="/ui/chat", status_code=307)
        return RedirectResponse(url="/ui/login", status_code=307)

    return app
