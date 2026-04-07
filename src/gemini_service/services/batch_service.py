from __future__ import annotations

import asyncio
import logging

from ..core.errors import ServiceError
from ..core.security import AuthContext
from ..db.models import BatchItemRecord, BatchRecord
from ..db.repository import ChatRepository
from ..schemas.common import BatchRequest, BatchResponse, SessionCreateRequest, MessageRequest
from .chat_service import ChatService


class BatchService:
    def __init__(self, repository: ChatRepository, chat_service: ChatService):
        self.repository = repository
        self.chat_service = chat_service
        self.logger = logging.getLogger("gemini_service.batch")
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        for batch in await self.repository.list_resumable_batches():
            self._schedule_batch(batch.id)

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def create_batch(self, auth: AuthContext, request: BatchRequest) -> BatchResponse:
        requested_account_id: str | None = None
        items: list[dict[str, str | None]] = []
        for item in request.items:
            if item.account_id and not auth.is_admin:
                raise ServiceError(
                    status_code=403,
                    code="forbidden",
                    message="Only administrators can pin batch items to a specific account.",
                )
            if item.session_id:
                await self.chat_service.get_session(item.session_id, auth=auth)
            requested_account_id = requested_account_id or item.account_id
            items.append(
                {
                    "external_id": item.external_id,
                    "prompt": item.prompt,
                    "session_id": item.session_id,
                    "requested_account_id": item.account_id,
                }
            )

        record = await self.repository.create_batch(
            owner_subject=auth.subject,
            requested_account_id=requested_account_id,
            items=items,
        )
        self._schedule_batch(record.id)
        return await self.get_batch(auth, record.id)

    async def get_batch(self, auth: AuthContext, batch_id: str) -> BatchResponse:
        record = await self.repository.get_batch(batch_id)
        if record is None:
            raise ServiceError(status_code=404, code="batch_not_found", message=f"Batch {batch_id} does not exist.")
        if not auth.is_admin and record.owner_subject != auth.subject:
            raise ServiceError(status_code=403, code="forbidden", message="You do not have access to this batch.")

        items = await self.repository.list_batch_items(batch_id)
        return self._batch_response(record, items)

    def _schedule_batch(self, batch_id: str) -> None:
        if batch_id in self._tasks and not self._tasks[batch_id].done():
            return
        task = asyncio.create_task(self._run_batch(batch_id))
        self._tasks[batch_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(batch_id, None))

    async def _run_batch(self, batch_id: str) -> None:
        await self.repository.mark_batch_running(batch_id)
        batch = await self.repository.get_batch(batch_id)
        if batch is None:
            return
        auth = AuthContext(subject=batch.owner_subject, role="admin", source="batch")

        for item in await self.repository.list_pending_batch_items(batch_id):
            await self.repository.mark_batch_item_running(item.id)
            try:
                response = await self._process_item(auth, item)
            except ServiceError as exc:
                await self.repository.mark_batch_item_failed(
                    item.id,
                    error_code=exc.code,
                    error_message=exc.message,
                )
                self.logger.warning(
                    "batch_item_failed",
                    extra={
                        "event": "batch_item_failed",
                        "batch_id": batch_id,
                        "item_id": item.id,
                        "external_id": item.external_id,
                        "error_code": exc.code,
                        "error_message": exc.message,
                    },
                )
            except Exception as exc:
                await self.repository.mark_batch_item_failed(
                    item.id,
                    error_code="batch_item_unexpected_error",
                    error_message=str(exc),
                )
            else:
                await self.repository.mark_batch_item_success(
                    item.id,
                    session_id=response.session_id,
                    account_id=response.account_id,
                    response_text=response.content,
                )

        await self.repository.mark_batch_finished(batch_id)

    async def _process_item(self, auth: AuthContext, item: BatchItemRecord):
        if item.session_id:
            return await self.chat_service.send_message(
                MessageRequest(
                    session_id=item.session_id,
                    message=item.prompt,
                    stream=False,
                    idempotency_key=f"batch:{item.id}",
                ),
                auth=auth,
            )

        session = await self.chat_service.create_session(
            SessionCreateRequest(
                account_id=item.requested_account_id,
                routing_policy="sticky",
                allow_failover=False,
                metadata={"batch_item_id": item.id},
            ),
            auth=auth,
        )
        return await self.chat_service.send_message(
            MessageRequest(
                session_id=session.session_id,
                message=item.prompt,
                stream=False,
                idempotency_key=f"batch:{item.id}",
            ),
            auth=auth,
        )

    def _batch_response(self, record: BatchRecord, items: list[BatchItemRecord]) -> BatchResponse:
        return BatchResponse(
            batch_id=record.id,
            status=record.status,
            total_items=record.total_items,
            completed_items=record.completed_items,
            failed_items=record.failed_items,
            requested_account_id=record.requested_account_id,
            created_at=record.created_at.isoformat() if record.created_at else None,
            updated_at=record.updated_at.isoformat() if record.updated_at else None,
            items=[
                {
                    "external_id": item.external_id,
                    "status": item.status,
                    "session_id": item.session_id,
                    "requested_account_id": item.requested_account_id,
                    "account_id": item.account_id,
                    "response_text": item.response_text,
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                }
                for item in items
            ],
        )
