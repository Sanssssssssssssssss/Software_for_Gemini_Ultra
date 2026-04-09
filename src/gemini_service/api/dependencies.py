from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..core.config import Settings, get_settings
from ..core.errors import ServiceError
from ..core.security import AuthContext
from ..core.telemetry import TelemetryService
from ..services.account_pool import AccountPool
from ..services.admin_console_service import AdminConsoleService
from ..services.asset_service import AssetService
from ..services.batch_service import BatchService
from ..services.chat_service import ChatService

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_settings() -> Settings:
    return get_settings()


def get_account_pool(request: Request) -> AccountPool:
    pool = getattr(request.app.state, "account_pool", None)
    if pool is None:
        raise ServiceError(
            status_code=503,
            code="account_pool_unavailable",
            message="Account pool has not been initialized yet.",
        )
    return pool


def get_chat_service(request: Request) -> ChatService:
    service = getattr(request.app.state, "chat_service", None)
    if service is None:
        raise ServiceError(
            status_code=503,
            code="chat_service_unavailable",
            message="Chat service has not been initialized yet.",
        )
    return service


def get_asset_service(request: Request) -> AssetService:
    service = getattr(request.app.state, "asset_service", None)
    if service is None:
        raise ServiceError(
            status_code=503,
            code="asset_service_unavailable",
            message="Asset service has not been initialized yet.",
        )
    return service


def get_admin_console_service(request: Request) -> AdminConsoleService:
    service = getattr(request.app.state, "admin_console_service", None)
    if service is None:
        raise ServiceError(
            status_code=503,
            code="admin_console_service_unavailable",
            message="Admin console service has not been initialized yet.",
        )
    return service


def get_batch_service(request: Request) -> BatchService:
    service = getattr(request.app.state, "batch_service", None)
    if service is None:
        raise ServiceError(
            status_code=503,
            code="batch_service_unavailable",
            message="Batch service has not been initialized yet.",
        )
    return service


def get_telemetry(request: Request) -> TelemetryService:
    telemetry = getattr(request.app.state, "telemetry", None)
    if telemetry is None:
        raise ServiceError(
            status_code=503,
            code="telemetry_unavailable",
            message="Telemetry service has not been initialized yet.",
        )
    return telemetry


def require_ui_user(
    request: Request,
    settings: Settings = Depends(get_current_settings),
) -> AuthContext:
    session = getattr(request, "session", None)
    if not session:
        raise ServiceError(
            status_code=401,
            code="ui_not_authenticated",
            message="UI login is required.",
        )
    username = session.get("ui_user")
    role = session.get("ui_role")
    if not username or role not in {"admin", "user"}:
        raise ServiceError(
            status_code=401,
            code="ui_not_authenticated",
            message="UI login is required.",
        )
    if role == "admin" and username != settings.ui_username:
        raise ServiceError(status_code=401, code="ui_not_authenticated", message="UI login is required.")
    if role == "user" and settings.ui_user_username and username != settings.ui_user_username:
        raise ServiceError(status_code=401, code="ui_not_authenticated", message="UI login is required.")
    return AuthContext(subject=username, role=role, source="ui")


def require_ui_admin(context: AuthContext = Depends(require_ui_user)) -> AuthContext:
    if not context.is_admin:
        raise ServiceError(
            status_code=403,
            code="forbidden",
            message="Administrator access is required for this UI endpoint.",
        )
    return context


def _parse_token_entry(entry: str) -> tuple[str, str, str]:
    parts = [item.strip() for item in entry.split("|")]
    if len(parts) == 3:
        return parts[0] or "api", parts[1], parts[2] or "user"
    if len(parts) == 2:
        return parts[0] or "api", parts[1], "user"
    return "api-admin", entry.strip(), "admin"


def require_api_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_current_settings),
) -> AuthContext | None:
    if not settings.require_auth:
        return AuthContext(subject="anonymous", role="admin", source="api")

    if not settings.api_token_values:
        raise ServiceError(
            status_code=503,
            code="auth_not_configured",
            message="API authentication is enabled but no bearer tokens are configured.",
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ServiceError(
            status_code=401,
            code="unauthorized",
            message="A valid bearer token is required for this endpoint.",
        )

    token = credentials.credentials.strip()
    for entry in settings.api_token_values:
        subject, configured_token, role = _parse_token_entry(entry)
        if token == configured_token:
            return AuthContext(subject=subject, role=role, source="api")

    raise ServiceError(
        status_code=403,
        code="forbidden",
        message="The provided bearer token is not authorized.",
    )


def require_admin_api_token(
    context: AuthContext | None = Depends(require_api_token),
) -> AuthContext:
    if context is None or not context.is_admin:
        raise ServiceError(
            status_code=403,
            code="forbidden",
            message="Administrator access is required for this endpoint.",
        )
    return context
