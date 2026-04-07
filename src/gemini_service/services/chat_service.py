from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator

from ..core.errors import ServiceError
from ..core.middleware import get_request_id
from ..core.telemetry import TelemetryService
from ..db.models import MessageRecord, SessionRecord
from ..db.repository import ChatRepository
from ..schemas.common import (
    MessageRequest,
    MessageResponse,
    SessionCreateRequest,
    SessionHistoryItem,
    SessionHistoryResponse,
    SessionResponse,
)
from .account_pool import AccountPool, AccountSelection


class ChatService:
    def __init__(self, pool: AccountPool, repository: ChatRepository, telemetry: TelemetryService | None = None):
        self.pool = pool
        self.repository = repository
        self.telemetry = telemetry
        self.logger = logging.getLogger("gemini_service.provider")

    async def create_session(self, request: SessionCreateRequest) -> SessionResponse:
        runtime = await self.pool.choose_account(
            preferred_account_id=request.account_id,
            require_preferred=bool(request.account_id),
        )
        record = await self.repository.create_session(
            account_id=runtime.config.account_id,
            routing_policy=request.routing_policy,
            model_name=request.model,
            gem_id=request.gem,
            allow_failover=request.allow_failover,
            metadata=request.metadata,
        )
        return self._session_response(record)

    async def get_session(self, session_id: str) -> SessionResponse:
        record = await self.repository.get_session(session_id)
        if record is None:
            raise ServiceError(
                status_code=404,
                code="session_not_found",
                message=f"Session {session_id} does not exist.",
            )
        return self._session_response(record)

    async def get_history(self, session_id: str) -> SessionHistoryResponse:
        record = await self.repository.get_session(session_id)
        if record is None:
            raise ServiceError(
                status_code=404,
                code="session_not_found",
                message=f"Session {session_id} does not exist.",
            )
        messages = await self.repository.list_messages(session_id)
        return SessionHistoryResponse(
            session_id=session_id,
            items=[
                SessionHistoryItem(
                    role=message.role,
                    content=message.content,
                    created_at=message.created_at.isoformat() if message.created_at else None,
                    idempotency_key=message.idempotency_key,
                )
                for message in messages
            ],
        )

    async def list_sessions(self, limit: int = 50) -> list[SessionResponse]:
        records = await self.repository.list_sessions(limit=limit)
        return [self._session_response(record) for record in records]

    async def send_message(self, request: MessageRequest) -> MessageResponse:
        record = await self._require_session(request.session_id)
        cached = await self._maybe_get_cached_response(record, request.idempotency_key)
        if cached is not None:
            return cached

        selection, record = await self._prepare_session_runtime(record)
        lease = await self.pool.acquire(
            preferred_account_id=selection.runtime.config.account_id,
            require_preferred=True,
            allow_failover=False,
        )
        started = time.perf_counter()
        async with lease as runtime:
            try:
                result = await runtime.adapter.send_message(
                    prompt=request.message,
                    chat_metadata=self._metadata_from_record(record),
                    model=record.model_name,
                    gem=record.gem_id,
                    temporary=request.temporary,
                )
            except ServiceError as exc:
                self.pool.record_provider_failure(runtime, exc)
                self._log_provider_event(
                    operation="send_message",
                    session_id=record.id,
                    account_id=runtime.config.account_id,
                    result="error",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error=exc,
                )
                raise
            else:
                self.pool.record_provider_success(runtime)

        self._log_provider_event(
            operation="send_message",
            session_id=record.id,
            account_id=record.account_id,
            result="success",
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        updated = await self.repository.update_session_metadata(record.id, result.metadata)
        user_message = await self.repository.add_message(
            session_id=record.id,
            role="user",
            content=request.message,
            idempotency_key=request.idempotency_key,
        )
        assistant_message = await self.repository.add_message(
            session_id=record.id,
            role="assistant",
            content=result.text,
            idempotency_key=request.idempotency_key,
        )
        return self._message_response(
            record=updated or record,
            assistant_message=assistant_message,
            cached=False,
            user_message_id=user_message.id,
        )

    async def stream_message(self, request: MessageRequest) -> AsyncIterator[str]:
        record = await self._require_session(request.session_id)
        cached = await self._maybe_get_cached_response(record, request.idempotency_key)
        if cached is not None:
            yield self._sse("chunk", {"text_delta": cached.content, "cached": True})
            yield self._sse("done", cached.model_dump(mode="json"))
            return

        selection, record = await self._prepare_session_runtime(record)
        lease = await self.pool.acquire(
            preferred_account_id=selection.runtime.config.account_id,
            require_preferred=True,
            allow_failover=False,
        )
        accumulated_text = ""
        final_metadata = self._metadata_from_record(record)
        started = time.perf_counter()
        async with lease as runtime:
            try:
                async for chunk in runtime.adapter.stream_message(
                    prompt=request.message,
                    chat_metadata=final_metadata,
                    model=record.model_name,
                    gem=record.gem_id,
                    temporary=request.temporary,
                ):
                    accumulated_text = chunk.text or (accumulated_text + chunk.text_delta)
                    if chunk.metadata:
                        final_metadata = chunk.metadata
                    yield self._sse(
                        "chunk",
                        {
                            "session_id": record.id,
                            "account_id": runtime.config.account_id,
                            "text_delta": chunk.text_delta,
                        },
                    )
            except ServiceError as exc:
                self.pool.record_provider_failure(runtime, exc)
                self._log_provider_event(
                    operation="stream_message",
                    session_id=record.id,
                    account_id=runtime.config.account_id,
                    result="error",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error=exc,
                )
                raise
            else:
                self.pool.record_provider_success(runtime)

        self._log_provider_event(
            operation="stream_message",
            session_id=record.id,
            account_id=record.account_id,
            result="success",
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        updated = await self.repository.update_session_metadata(record.id, final_metadata)
        user_message = await self.repository.add_message(
            session_id=record.id,
            role="user",
            content=request.message,
            idempotency_key=request.idempotency_key,
        )
        assistant_message = await self.repository.add_message(
            session_id=record.id,
            role="assistant",
            content=accumulated_text,
            idempotency_key=request.idempotency_key,
        )
        response = self._message_response(
            record=updated or record,
            assistant_message=assistant_message,
            cached=False,
            user_message_id=user_message.id,
        )
        yield self._sse("done", response.model_dump(mode="json"))

    async def _require_session(self, session_id: str) -> SessionRecord:
        record = await self.repository.get_session(session_id)
        if record is None:
            raise ServiceError(
                status_code=404,
                code="session_not_found",
                message=f"Session {session_id} does not exist.",
            )
        return record

    async def _prepare_session_runtime(self, record: SessionRecord) -> tuple[AccountSelection, SessionRecord]:
        selection = await self.pool.resolve_session_account(
            preferred_account_id=record.account_id,
            allow_failover=record.allow_failover,
        )
        if selection.failover_from is None:
            return selection, record

        updated = await self.repository.record_failover(
            session_id=record.id,
            from_account_id=selection.failover_from,
            to_account_id=selection.runtime.config.account_id,
            reason=selection.reason or "automatic failover",
        )
        self.logger.warning(
            "session_failover",
            extra={
                "event": "session_failover",
                "request_id": get_request_id(),
                "session_id": record.id,
                "from_account_id": selection.failover_from,
                "to_account_id": selection.runtime.config.account_id,
                "reason": selection.reason or "",
            },
        )
        return selection, updated or record

    async def _maybe_get_cached_response(
        self,
        record: SessionRecord,
        idempotency_key: str | None,
    ) -> MessageResponse | None:
        if not idempotency_key:
            return None
        cached = await self.repository.get_cached_assistant_message(record.id, idempotency_key)
        if cached is None:
            return None
        return self._message_response(
            record=record,
            assistant_message=cached,
            cached=True,
            user_message_id=None,
        )

    def _session_response(self, record: SessionRecord) -> SessionResponse:
        return SessionResponse(
            session_id=record.id,
            account_id=record.account_id,
            routing_policy=record.routing_policy,
            status=record.status,
            model=record.model_name,
            gem=record.gem_id,
            allow_failover=record.allow_failover,
            gemini_metadata=self._metadata_from_record(record),
            created_at=record.created_at.isoformat() if record.created_at else None,
            updated_at=record.updated_at.isoformat() if record.updated_at else None,
        )

    def _message_response(
        self,
        record: SessionRecord,
        assistant_message: MessageRecord,
        cached: bool,
        user_message_id: str | None,
    ) -> MessageResponse:
        return MessageResponse(
            session_id=record.id,
            account_id=record.account_id,
            content=assistant_message.content,
            cached=cached,
            message_id=assistant_message.id,
            user_message_id=user_message_id,
            gemini_metadata=self._metadata_from_record(record),
            created_at=assistant_message.created_at.isoformat() if assistant_message.created_at else None,
        )

    def _metadata_from_record(self, record: SessionRecord) -> list[str]:
        return [
            record.gemini_cid or "",
            record.gemini_rid or "",
            record.gemini_rcid or "",
        ]

    def _log_provider_event(
        self,
        operation: str,
        session_id: str,
        account_id: str,
        result: str,
        duration_ms: float,
        error: ServiceError | None = None,
    ) -> None:
        payload = {
            "event": "provider_call",
            "request_id": get_request_id(),
            "session_id": session_id,
            "account_id": account_id,
            "operation": operation,
            "result": result,
            "duration_ms": round(duration_ms, 2),
        }
        error_code = ""
        if error is not None:
            payload["error_code"] = error.code
            payload["error_details"] = error.details
            error_code = error.code
        if self.telemetry is not None:
            self.telemetry.record_provider_call(
                account_id=account_id,
                operation=operation,
                result=result,
                error_code=error_code,
            )
        self.logger.info("provider_call", extra=payload)

    def _sse(self, event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"
