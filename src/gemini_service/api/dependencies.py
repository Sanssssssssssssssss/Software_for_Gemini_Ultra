from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..core.config import Settings, get_settings
from ..core.errors import ServiceError

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_settings() -> Settings:
    return get_settings()


def require_api_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_current_settings),
) -> str | None:
    if not settings.require_auth:
        return None

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
    if token not in settings.api_token_values:
        raise ServiceError(
            status_code=403,
            code="forbidden",
            message="The provided bearer token is not authorized.",
        )

    return token
