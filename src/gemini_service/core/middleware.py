from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

_request_id_var: ContextVar[str | None] = ContextVar("gemini_service_request_id", default=None)


def get_request_id() -> str | None:
    return _request_id_var.get()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        reset_token = _request_id_var.set(request_id)

        telemetry = request.app.state.telemetry
        telemetry.request_started()
        started = time.perf_counter()
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            duration = time.perf_counter() - started
            telemetry.request_finished(
                method=request.method,
                path=path,
                status=500,
                duration=duration,
            )
            logging.getLogger("gemini_service.request").exception(
                "request_failed",
                extra={
                    "event": "request_failed",
                    "request_id": request_id,
                    "method": request.method,
                    "path": path,
                    "status_code": 500,
                    "duration_ms": round(duration * 1000, 2),
                },
            )
            _request_id_var.reset(reset_token)
            raise

        duration = time.perf_counter() - started
        telemetry.request_finished(
            method=request.method,
            path=path,
            status=status_code,
            duration=duration,
        )
        response.headers["X-Request-ID"] = request_id
        logging.getLogger("gemini_service.request").info(
            "request_complete",
            extra={
                "event": "request_complete",
                "request_id": request_id,
                "method": request.method,
                "path": path,
                "status_code": status_code,
                "duration_ms": round(duration * 1000, 2),
            },
        )
        _request_id_var.reset(reset_token)
        return response
