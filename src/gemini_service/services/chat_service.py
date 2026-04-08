from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator

from ..adapters.base import AssetPromptPart, PromptPart, TextPromptPart
from ..core.errors import ServiceError
from ..core.middleware import get_request_id
from ..core.security import AuthContext
from ..core.telemetry import TelemetryService
from ..db.models import MediaAssetRecord, MessageRecord, SessionRecord
from ..db.repository import ChatRepository
from ..schemas.common import (
    AssetResponse,
    MessageAssetPart,
    MessageMedia,
    MessageRequest,
    MessageResponsePart,
    MessageTextPart,
    MessageResponse,
    SessionCreateRequest,
    SessionHistoryItem,
    SessionHistoryResponse,
    SessionResponse,
)
from .asset_service import AssetService
from .account_pool import AccountPool, AccountSelection


class ChatService:
    def __init__(
        self,
        pool: AccountPool,
        repository: ChatRepository,
        asset_service: AssetService,
        telemetry: TelemetryService | None = None,
    ):
        self.pool = pool
        self.repository = repository
        self.asset_service = asset_service
        self.telemetry = telemetry
        self.logger = logging.getLogger("gemini_service.provider")

    async def create_session(self, request: SessionCreateRequest, auth: AuthContext) -> SessionResponse:
        if request.account_id and not auth.is_admin:
            raise ServiceError(
                status_code=403,
                code="forbidden",
                message="Only administrators can pin a session to a specific account.",
            )
        runtime = await self.pool.choose_account(
            preferred_account_id=request.account_id,
            require_preferred=bool(request.account_id),
        )
        record = await self.repository.create_session(
            owner_subject=auth.subject,
            account_id=runtime.config.account_id,
            routing_policy=request.routing_policy,
            model_name=request.model,
            gem_id=request.gem,
            allow_failover=request.allow_failover if auth.is_admin else False,
            metadata=request.metadata,
        )
        return self._session_response(record)

    async def get_session(self, session_id: str, auth: AuthContext) -> SessionResponse:
        record = await self._require_session(session_id, auth)
        return self._session_response(record)

    async def get_history(self, session_id: str, auth: AuthContext) -> SessionHistoryResponse:
        record = await self._require_session(session_id, auth)
        messages = await self.repository.list_messages(session_id)
        history_items: list[SessionHistoryItem] = []
        for message in messages:
            parts, media = await self._load_message_render_parts(message)
            history_items.append(
                SessionHistoryItem(
                    role=message.role,
                    content=message.content,
                    parts=parts,
                    media=media,
                    created_at=message.created_at.isoformat() if message.created_at else None,
                    idempotency_key=message.idempotency_key,
                )
            )
        return SessionHistoryResponse(
            session_id=session_id,
            items=history_items,
        )

    async def list_sessions(self, auth: AuthContext, limit: int = 50) -> list[SessionResponse]:
        records = await self.repository.list_sessions(
            limit=limit,
            owner_subject=None if auth.is_admin else auth.subject,
        )
        return [self._session_response(record) for record in records]

    async def send_message(self, request: MessageRequest, auth: AuthContext) -> MessageResponse:
        record = await self._require_session(request.session_id, auth)
        cached = await self._maybe_get_cached_response(record, request.idempotency_key)
        if cached is not None:
            return cached

        selection, record = await self._prepare_session_runtime(record)
        prompt_parts, user_part_records, effective_temporary = await self._resolve_prompt_parts(request, record, auth)
        self.logger.info(
            "provider_submit_started",
            extra={
                "event": "provider_submit_started",
                "request_id": get_request_id(),
                "session_id": record.id,
                "owner_subject": auth.subject,
                "account_id": selection.runtime.config.account_id,
                "asset_count": sum(part.get("part_type") == "asset" for part in user_part_records),
                "temporary": effective_temporary,
            },
        )
        lease = await self.pool.acquire(
            preferred_account_id=selection.runtime.config.account_id,
            require_preferred=True,
            allow_failover=False,
        )
        started = time.perf_counter()
        async with lease as runtime:
            try:
                result = await runtime.adapter.send_message(
                    parts=prompt_parts,
                    chat_metadata=self._metadata_from_record(record),
                    model=record.model_name,
                    gem=record.gem_id,
                    temporary=effective_temporary,
                )
            except ServiceError as exc:
                self.pool.record_provider_failure(runtime, exc)
                self._log_provider_event(
                    operation="send_message",
                    session_id=record.id,
                    account_id=runtime.config.account_id,
                    result="error",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    auth=auth,
                    error=exc,
                )
                self.logger.warning(
                    "provider_submit_failed",
                    extra={
                        "event": "provider_submit_failed",
                        "request_id": get_request_id(),
                        "session_id": record.id,
                        "owner_subject": auth.subject,
                        "account_id": runtime.config.account_id,
                        "error_code": exc.code,
                    },
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
            auth=auth,
        )
        updated = await self.repository.update_session_metadata(record.id, result.metadata)
        user_message = await self.repository.add_message(
            session_id=record.id,
            role="user",
            content=self._plain_text_from_parts(request),
            idempotency_key=request.idempotency_key,
            parts=user_part_records,
        )
        await self._bind_part_assets(record.id, user_message.id, user_part_records)
        assistant_assets = await self._store_generated_media(record, auth, result.generated_media)
        assistant_parts = [{"part_type": "text", "text_content": result.text}]
        assistant_parts.extend(
            {"part_type": "asset", "asset_id": asset.id, "metadata": {"media_type": "image"}}
            for asset in assistant_assets
        )
        assistant_message = await self.repository.add_message(
            session_id=record.id,
            role="assistant",
            content=result.text,
            idempotency_key=request.idempotency_key,
            parts=assistant_parts,
        )
        await self._bind_part_assets(record.id, assistant_message.id, assistant_parts)
        return await self._message_response(
            record=updated or record,
            assistant_message=assistant_message,
            cached=False,
            user_message_id=user_message.id,
        )

    async def stream_message(self, request: MessageRequest, auth: AuthContext) -> AsyncIterator[str]:
        record = await self._require_session(request.session_id, auth)
        try:
            cached = await self._maybe_get_cached_response(record, request.idempotency_key)
            if cached is not None:
                yield self._sse(
                    "accepted",
                    {
                        "session_id": record.id,
                        "account_id": record.account_id,
                        "cached": True,
                    },
                )
                yield self._sse("chunk", {"text_delta": cached.content, "cached": True})
                yield self._sse("done", cached.model_dump(mode="json"))
                return

            selection, record = await self._prepare_session_runtime(record)
            prompt_parts, user_part_records, effective_temporary = await self._resolve_prompt_parts(request, record, auth)
            self.logger.info(
                "provider_submit_started",
                extra={
                    "event": "provider_submit_started",
                    "request_id": get_request_id(),
                    "session_id": record.id,
                    "owner_subject": auth.subject,
                    "account_id": selection.runtime.config.account_id,
                    "asset_count": sum(part.get("part_type") == "asset" for part in user_part_records),
                    "temporary": effective_temporary,
                    "stream": True,
                },
            )
            yield self._sse(
                "accepted",
                {
                    "session_id": record.id,
                    "account_id": selection.runtime.config.account_id,
                    "cached": False,
                    "failover_from": selection.failover_from,
                },
            )
            if selection.failover_from is not None:
                yield self._sse(
                    "status",
                    {
                        "session_id": record.id,
                        "account_id": selection.runtime.config.account_id,
                        "phase": "failover",
                        "message": selection.reason or "Session rerouted to a healthy account.",
                        "failover_from": selection.failover_from,
                    },
                )
            lease = await self.pool.acquire(
                preferred_account_id=selection.runtime.config.account_id,
                require_preferred=True,
                allow_failover=False,
            )
        except ServiceError as exc:
            yield self._sse(
                "error",
                {
                    "session_id": record.id,
                    "account_id": record.account_id,
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
            )
            return
        accumulated_text = ""
        final_metadata = self._metadata_from_record(record)
        streamed_media_assets: list[MediaAssetRecord] = []
        started = time.perf_counter()
        async with lease as runtime:
            chunk_seen = False
            try:
                yield self._sse(
                    "status",
                    {
                        "session_id": record.id,
                        "account_id": runtime.config.account_id,
                        "phase": "thinking",
                        "message": "Provider accepted the request and is preparing a response.",
                    },
                )
                async for chunk in runtime.adapter.stream_message(
                    parts=prompt_parts,
                    chat_metadata=final_metadata,
                    model=record.model_name,
                    gem=record.gem_id,
                    temporary=effective_temporary,
                ):
                    accumulated_text = chunk.text or (accumulated_text + chunk.text_delta)
                    if chunk.metadata:
                        final_metadata = chunk.metadata
                    if chunk.generated_media:
                        new_assets = await self._store_generated_media(record, auth, chunk.generated_media)
                        streamed_media_assets.extend(new_assets)
                        for asset in new_assets:
                            yield self._sse(
                                "media",
                                {
                                    "session_id": record.id,
                                    "account_id": runtime.config.account_id,
                                    "asset": self._asset_response(asset).model_dump(mode="json"),
                                    "media_type": "image",
                                },
                            )
                    if not chunk_seen:
                        chunk_seen = True
                        yield self._sse(
                            "status",
                            {
                                "session_id": record.id,
                                "account_id": runtime.config.account_id,
                                "phase": "streaming",
                                "message": "Streaming response chunks.",
                            },
                        )
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
                    auth=auth,
                    error=exc,
                )
                self.logger.warning(
                    "provider_submit_failed",
                    extra={
                        "event": "provider_submit_failed",
                        "request_id": get_request_id(),
                        "session_id": record.id,
                        "owner_subject": auth.subject,
                        "account_id": runtime.config.account_id,
                        "error_code": exc.code,
                        "stream": True,
                    },
                )
                yield self._sse(
                    "error",
                    {
                        "session_id": record.id,
                        "account_id": runtime.config.account_id,
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    },
                )
                return
            else:
                self.pool.record_provider_success(runtime)

        self._log_provider_event(
            operation="stream_message",
            session_id=record.id,
            account_id=record.account_id,
            result="success",
            duration_ms=(time.perf_counter() - started) * 1000,
            auth=auth,
        )
        updated = await self.repository.update_session_metadata(record.id, final_metadata)
        user_message = await self.repository.add_message(
            session_id=record.id,
            role="user",
            content=self._plain_text_from_parts(request),
            idempotency_key=request.idempotency_key,
            parts=user_part_records,
        )
        await self._bind_part_assets(record.id, user_message.id, user_part_records)
        assistant_parts = [{"part_type": "text", "text_content": accumulated_text}]
        assistant_parts.extend(
            {"part_type": "asset", "asset_id": asset.id, "metadata": {"media_type": "image"}}
            for asset in streamed_media_assets
        )
        assistant_message = await self.repository.add_message(
            session_id=record.id,
            role="assistant",
            content=accumulated_text,
            idempotency_key=request.idempotency_key,
            parts=assistant_parts,
        )
        await self._bind_part_assets(record.id, assistant_message.id, assistant_parts)
        response = await self._message_response(
            record=updated or record,
            assistant_message=assistant_message,
            cached=False,
            user_message_id=user_message.id,
        )
        yield self._sse("done", response.model_dump(mode="json"))

    async def _require_session(self, session_id: str, auth: AuthContext) -> SessionRecord:
        record = await self.repository.get_session(session_id)
        if record is None:
            raise ServiceError(
                status_code=404,
                code="session_not_found",
                message=f"Session {session_id} does not exist.",
            )
        if not auth.is_admin and record.owner_subject != auth.subject:
            raise ServiceError(
                status_code=403,
                code="forbidden",
                message="You do not have access to this session.",
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
                "owner_subject": record.owner_subject,
                "from_account_id": selection.failover_from,
                "to_account_id": selection.runtime.config.account_id,
                "reason": selection.reason or "",
            },
        )
        if self.telemetry is not None:
            self.telemetry.record_session_failover()
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
        return await self._message_response(
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

    async def _message_response(
        self,
        record: SessionRecord,
        assistant_message: MessageRecord,
        cached: bool,
        user_message_id: str | None,
    ) -> MessageResponse:
        parts, media = await self._load_message_render_parts(assistant_message)
        return MessageResponse(
            session_id=record.id,
            account_id=record.account_id,
            content=assistant_message.content,
            parts=parts,
            media=media,
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
        auth: AuthContext,
        error: ServiceError | None = None,
    ) -> None:
        payload = {
            "event": "provider_call",
            "request_id": get_request_id(),
            "session_id": session_id,
            "owner_subject": auth.subject,
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

    async def _resolve_prompt_parts(
        self,
        request: MessageRequest,
        record: SessionRecord,
        auth: AuthContext,
    ) -> tuple[list[PromptPart], list[dict], bool]:
        prompt_parts: list[PromptPart] = []
        stored_parts: list[dict] = []

        if request.message:
            prompt_parts.append(TextPromptPart(type="text", text=request.message))
            stored_parts.append({"part_type": "text", "text_content": request.message})
        else:
            for part in request.parts:
                if isinstance(part, MessageTextPart):
                    prompt_parts.append(TextPromptPart(type="text", text=part.text))
                    stored_parts.append({"part_type": "text", "text_content": part.text})
                    continue

                asset = await self.asset_service.get_asset_for_read(asset_id=part.asset_id, auth=auth)
                asset_path = self.asset_service.resolve_asset_path(asset)
                prompt_parts.append(
                    AssetPromptPart(
                        type="asset",
                        asset_id=asset.id,
                        filename=asset.filename,
                        mime_type=asset.mime_type,
                        absolute_path=asset_path,
                    )
                )
                stored_parts.append(
                    {
                        "part_type": "asset",
                        "asset_id": asset.id,
                        "metadata": {"mime_type": asset.mime_type, "filename": asset.filename},
                    }
                )

        asset_count = sum(part.get("part_type") == "asset" for part in stored_parts)
        if asset_count > self.asset_service.settings.asset_max_files_per_message:
            raise ServiceError(
                status_code=400,
                code="too_many_assets",
                message="Too many files attached to a single message.",
                details={"max_files": self.asset_service.settings.asset_max_files_per_message},
            )

        effective_temporary = request.temporary if request.temporary is not None else bool(asset_count)
        if asset_count and not effective_temporary:
            self.logger.warning(
                "file_message_persistent",
                extra={
                    "event": "file_message_persistent",
                    "request_id": get_request_id(),
                    "session_id": record.id,
                    "owner_subject": auth.subject,
                    "account_id": record.account_id,
                },
            )

        return prompt_parts, stored_parts, effective_temporary

    def _plain_text_from_parts(self, request: MessageRequest) -> str:
        if request.message:
            return request.message
        text_parts = [part.text for part in request.parts if isinstance(part, MessageTextPart) and part.text.strip()]
        return "\n\n".join(text_parts).strip() or "[Attachment message]"

    async def _bind_part_assets(self, session_id: str, message_id: str, parts: list[dict]) -> None:
        for part in parts:
            asset_id = part.get("asset_id")
            if asset_id:
                await self.repository.bind_asset_to_message(
                    asset_id=asset_id,
                    session_id=session_id,
                    message_id=message_id,
                )

    async def _store_generated_media(self, record: SessionRecord, auth: AuthContext, generated_media) -> list[MediaAssetRecord]:
        assets: list[MediaAssetRecord] = []
        for index, media in enumerate(generated_media or [], start=1):
            asset = await self.asset_service.create_generated_asset(
                auth=auth,
                filename=media.filename or f"generated-image-{index}.png",
                mime_type=media.mime_type,
                content=media.content,
                provider_ref=media.provider_ref,
                metadata={"media_type": media.media_type, "session_id": record.id},
            )
            assets.append(asset)
        return assets

    async def _load_message_render_parts(
        self,
        message: MessageRecord,
    ) -> tuple[list[MessageResponsePart], list[MessageMedia]]:
        part_records = await self.repository.list_message_parts(message.id)
        if not part_records:
            return [MessageResponsePart(type="text", text=message.content)], []
        return await self._parts_from_records(part_records)

    async def _parts_from_records(
        self,
        part_records,
    ) -> tuple[list[MessageResponsePart], list[MessageMedia]]:
        parts: list[MessageResponsePart] = []
        media: list[MessageMedia] = []
        for part in part_records:
            if part.part_type == "text":
                parts.append(MessageResponsePart(type="text", text=part.text_content or ""))
                continue
            if part.asset_id:
                asset = await self.repository.get_asset(part.asset_id)
                if asset is None:
                    continue
                asset_response = self._asset_response(asset)
                parts.append(MessageResponsePart(type="asset", asset=asset_response))
                if asset.mime_type.startswith("image/"):
                    media.append(
                        MessageMedia(
                            asset_id=asset.id,
                            mime_type=asset.mime_type,
                            filename=asset.filename,
                            media_type="image",
                            content_url=f"/v1/assets/{asset.id}/content",
                        )
                    )
        if not parts:
            return [MessageResponsePart(type="text", text="")], media
        return parts, media

    def _asset_response(self, asset: MediaAssetRecord) -> AssetResponse:
        return AssetResponse(
            asset_id=asset.id,
            owner_subject=asset.owner_subject,
            filename=asset.filename,
            mime_type=asset.mime_type,
            size_bytes=asset.size_bytes,
            sha256=asset.sha256,
            status=asset.status,
            storage_backend=asset.storage_backend,
            provider_ref=asset.provider_ref,
            created_at=asset.created_at.isoformat() if asset.created_at else None,
            expires_at=asset.expires_at.isoformat() if asset.expires_at else None,
            metadata=self.repository._load_metadata(asset.metadata_json),
        )
