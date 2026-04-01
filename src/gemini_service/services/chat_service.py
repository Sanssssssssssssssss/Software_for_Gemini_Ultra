from __future__ import annotations

import json
from collections.abc import AsyncIterator

from ..core.errors import ServiceError
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
from .account_pool import AccountPool


class ChatService:
    def __init__(self, pool: AccountPool, repository: ChatRepository):
        self.pool = pool
        self.repository = repository

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
        record = await self.repository.get_session(request.session_id)
        if record is None:
            raise ServiceError(
                status_code=404,
                code="session_not_found",
                message=f"Session {request.session_id} does not exist.",
            )

        cached = await self._maybe_get_cached_response(record, request.idempotency_key)
        if cached is not None:
            return cached

        lease = await self.pool.acquire(
            preferred_account_id=record.account_id,
            require_preferred=True,
        )
        async with lease as runtime:
            result = await runtime.adapter.send_message(
                prompt=request.message,
                chat_metadata=self._metadata_from_record(record),
                model=record.model_name,
                gem=record.gem_id,
                temporary=request.temporary,
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
        record = await self.repository.get_session(request.session_id)
        if record is None:
            raise ServiceError(
                status_code=404,
                code="session_not_found",
                message=f"Session {request.session_id} does not exist.",
            )

        cached = await self._maybe_get_cached_response(record, request.idempotency_key)
        if cached is not None:
            yield self._sse("chunk", {"text_delta": cached.content, "cached": True})
            yield self._sse("done", cached.model_dump(mode="json"))
            return

        lease = await self.pool.acquire(
            preferred_account_id=record.account_id,
            require_preferred=True,
        )
        accumulated_text = ""
        final_metadata = self._metadata_from_record(record)
        async with lease as runtime:
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
                        "account_id": record.account_id,
                        "text_delta": chunk.text_delta,
                    },
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

    def _sse(self, event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"
