from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, select, update
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import (
    Base,
    BatchItemRecord,
    BatchRecord,
    MediaAssetRecord,
    MessagePartRecord,
    MessageRecord,
    SessionRecord,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine = None
        self.session_factory: async_sessionmaker[AsyncSession] | None = None

    async def start(self) -> None:
        self._ensure_database_path()
        self.engine = create_async_engine(self.database_url, future=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(self._run_migrations)

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()

    async def ping(self) -> bool:
        if self.engine is None:
            return False
        try:
            async with self.engine.connect() as connection:
                await connection.execute(select(1))
            return True
        except Exception:
            return False

    async def create_session(
        self,
        owner_subject: str,
        account_id: str,
        routing_policy: str,
        model_name: str | None = None,
        gem_id: str | None = None,
        allow_failover: bool = False,
        metadata: dict | None = None,
    ) -> SessionRecord:
        session_record = SessionRecord(
            owner_subject=owner_subject,
            account_id=account_id,
            routing_policy=routing_policy,
            model_name=model_name,
            gem_id=gem_id,
            allow_failover=allow_failover,
            metadata_json=self._dump_metadata(metadata),
        )
        async with self._session() as db:
            db.add(session_record)
            await db.commit()
            await db.refresh(session_record)
            return session_record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        async with self._session() as db:
            return await db.get(SessionRecord, session_id)

    async def update_session_metadata(
        self,
        session_id: str,
        gemini_metadata: list[str],
    ) -> SessionRecord | None:
        async with self._session() as db:
            record = await db.get(SessionRecord, session_id)
            if record is None:
                return None
            record.gemini_cid = gemini_metadata[0] if len(gemini_metadata) > 0 else None
            record.gemini_rid = gemini_metadata[1] if len(gemini_metadata) > 1 else None
            record.gemini_rcid = gemini_metadata[2] if len(gemini_metadata) > 2 else None
            await db.commit()
            await db.refresh(record)
            return record

    async def update_session_title(
        self,
        session_id: str,
        title: str | None,
    ) -> SessionRecord | None:
        async with self._session() as db:
            record = await db.get(SessionRecord, session_id)
            if record is None:
                return None
            metadata = self._load_metadata(record.metadata_json)
            cleaned = (title or "").strip()
            if cleaned:
                metadata["title"] = cleaned
            else:
                metadata.pop("title", None)
            record.metadata_json = self._dump_metadata(metadata)
            await db.commit()
            await db.refresh(record)
            return record

    async def record_failover(
        self,
        session_id: str,
        from_account_id: str,
        to_account_id: str,
        reason: str,
    ) -> SessionRecord | None:
        async with self._session() as db:
            record = await db.get(SessionRecord, session_id)
            if record is None:
                return None

            metadata = self._load_metadata(record.metadata_json)
            events = list(metadata.get("events", []))
            events.append(
                {
                    "type": "failover",
                    "from_account_id": from_account_id,
                    "to_account_id": to_account_id,
                    "reason": reason,
                    "created_at": utc_now_iso(),
                }
            )
            metadata["events"] = events

            record.account_id = to_account_id
            record.metadata_json = self._dump_metadata(metadata)
            record.gemini_cid = None
            record.gemini_rid = None
            record.gemini_rcid = None
            await db.commit()
            await db.refresh(record)
            return record

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        idempotency_key: str | None = None,
        parts: list[dict] | None = None,
    ) -> MessageRecord:
        record = MessageRecord(
            session_id=session_id,
            role=role,
            content=content,
            idempotency_key=idempotency_key,
        )
        part_records = [
            MessagePartRecord(
                order_index=index,
                part_type=str(part.get("part_type", "text")),
                text_content=part.get("text_content"),
                asset_id=part.get("asset_id"),
                metadata_json=self._dump_metadata(part.get("metadata")),
            )
            for index, part in enumerate(parts or [])
        ]
        if part_records:
            record.parts = part_records
        async with self._session() as db:
            db.add(record)
            await db.commit()
            await db.refresh(record)
            return record

    async def create_asset(
        self,
        *,
        owner_subject: str,
        filename: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        status: str,
        storage_backend: str,
        storage_uri: str,
        provider_ref: str | None = None,
        metadata: dict | None = None,
        expires_at: datetime | None = None,
    ) -> MediaAssetRecord:
        record = MediaAssetRecord(
            owner_subject=owner_subject,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            status=status,
            storage_backend=storage_backend,
            storage_uri=storage_uri,
            provider_ref=provider_ref,
            metadata_json=self._dump_metadata(metadata),
            expires_at=expires_at,
        )
        async with self._session() as db:
            db.add(record)
            await db.commit()
            await db.refresh(record)
            return record

    async def get_asset(self, asset_id: str) -> MediaAssetRecord | None:
        async with self._session() as db:
            return await db.get(MediaAssetRecord, asset_id)

    async def mark_asset_stored(
        self,
        *,
        asset_id: str,
        storage_backend: str,
        storage_uri: str,
        status: str = "available",
    ) -> MediaAssetRecord | None:
        async with self._session() as db:
            record = await db.get(MediaAssetRecord, asset_id)
            if record is None:
                return None
            record.storage_backend = storage_backend
            record.storage_uri = storage_uri
            record.status = status
            await db.commit()
            await db.refresh(record)
            return record

    async def mark_asset_failed(self, asset_id: str, error_message: str) -> MediaAssetRecord | None:
        async with self._session() as db:
            record = await db.get(MediaAssetRecord, asset_id)
            if record is None:
                return None
            metadata = self._load_metadata(record.metadata_json)
            metadata["last_error"] = error_message
            record.metadata_json = self._dump_metadata(metadata)
            record.status = "failed"
            await db.commit()
            await db.refresh(record)
            return record

    async def bind_asset_to_message(
        self,
        *,
        asset_id: str,
        session_id: str,
        message_id: str,
    ) -> MediaAssetRecord | None:
        async with self._session() as db:
            record = await db.get(MediaAssetRecord, asset_id)
            if record is None:
                return None
            record.session_id = session_id
            record.message_id = message_id
            await db.commit()
            await db.refresh(record)
            return record

    async def list_messages(self, session_id: str) -> list[MessageRecord]:
        async with self._session() as db:
            result = await db.execute(
                select(MessageRecord)
                .where(MessageRecord.session_id == session_id)
                .order_by(MessageRecord.created_at.asc())
            )
            return list(result.scalars())

    async def list_message_parts(self, message_id: str) -> list[MessagePartRecord]:
        async with self._session() as db:
            result = await db.execute(
                select(MessagePartRecord)
                .where(MessagePartRecord.message_id == message_id)
                .order_by(MessagePartRecord.order_index.asc(), MessagePartRecord.created_at.asc())
            )
            return list(result.scalars())

    async def list_sessions(self, limit: int = 50, owner_subject: str | None = None) -> list[SessionRecord]:
        async with self._session() as db:
            query = select(SessionRecord).order_by(SessionRecord.updated_at.desc()).limit(limit)
            if owner_subject is not None:
                query = query.where(SessionRecord.owner_subject == owner_subject)
            result = await db.execute(query)
            return list(result.scalars())

    async def list_session_title_candidates(self, session_ids: list[str]) -> dict[str, str]:
        if not session_ids:
            return {}
        async with self._session() as db:
            result = await db.execute(
                select(MessageRecord)
                .where(
                    MessageRecord.session_id.in_(session_ids),
                    MessageRecord.role == "user",
                )
                .order_by(MessageRecord.session_id.asc(), MessageRecord.created_at.asc())
            )
            titles: dict[str, str] = {}
            for record in result.scalars():
                if record.session_id in titles:
                    continue
                content = (record.content or "").strip()
                if content:
                    titles[record.session_id] = content
            return titles

    async def delete_session(self, session_id: str) -> bool:
        async with self._session() as db:
            record = await db.get(SessionRecord, session_id)
            if record is None:
                return False

            await db.execute(
                update(MediaAssetRecord)
                .where(MediaAssetRecord.session_id == session_id)
                .values(session_id=None, message_id=None)
            )
            await db.delete(record)
            await db.commit()
            return True

    async def count_sessions(self) -> int:
        async with self._session() as db:
            result = await db.execute(select(SessionRecord))
            return len(list(result.scalars()))

    async def count_messages(self) -> int:
        async with self._session() as db:
            result = await db.execute(select(MessageRecord))
            return len(list(result.scalars()))

    async def count_batches(self) -> int:
        async with self._session() as db:
            result = await db.execute(select(BatchRecord))
            return len(list(result.scalars()))

    async def count_assets(self) -> int:
        async with self._session() as db:
            result = await db.execute(select(MediaAssetRecord))
            return len(list(result.scalars()))

    async def count_assets_by_status(self) -> dict[str, int]:
        async with self._session() as db:
            result = await db.execute(select(MediaAssetRecord))
            counts: dict[str, int] = {}
            for record in result.scalars():
                counts[record.status] = counts.get(record.status, 0) + 1
            return counts

    async def list_recent_assets(self, limit: int = 20) -> list[MediaAssetRecord]:
        async with self._session() as db:
            result = await db.execute(
                select(MediaAssetRecord)
                .order_by(MediaAssetRecord.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars())

    async def count_batches_by_status(self) -> dict[str, int]:
        async with self._session() as db:
            result = await db.execute(select(BatchRecord))
            counts: dict[str, int] = {}
            for record in result.scalars():
                counts[record.status] = counts.get(record.status, 0) + 1
            return counts

    async def list_expired_assets(self, now: datetime, limit: int = 100) -> list[MediaAssetRecord]:
        async with self._session() as db:
            result = await db.execute(
                select(MediaAssetRecord)
                .where(
                    MediaAssetRecord.expires_at.is_not(None),
                    MediaAssetRecord.expires_at <= now,
                    MediaAssetRecord.status.in_(("available", "failed")),
                )
                .order_by(MediaAssetRecord.expires_at.asc())
                .limit(limit)
            )
            return list(result.scalars())

    async def list_orphan_assets(self, older_than: datetime, limit: int = 100) -> list[MediaAssetRecord]:
        async with self._session() as db:
            result = await db.execute(
                select(MediaAssetRecord)
                .where(
                    MediaAssetRecord.session_id.is_(None),
                    MediaAssetRecord.message_id.is_(None),
                    MediaAssetRecord.expires_at.is_(None),
                    MediaAssetRecord.created_at <= older_than,
                    MediaAssetRecord.status == "available",
                )
                .order_by(MediaAssetRecord.created_at.asc())
                .limit(limit)
            )
            return list(result.scalars())

    async def mark_asset_status(
        self,
        *,
        asset_id: str,
        status: str,
        clear_storage: bool = False,
        metadata_updates: dict | None = None,
    ) -> MediaAssetRecord | None:
        async with self._session() as db:
            record = await db.get(MediaAssetRecord, asset_id)
            if record is None:
                return None
            record.status = status
            metadata = self._load_metadata(record.metadata_json)
            metadata.update(metadata_updates or {})
            record.metadata_json = self._dump_metadata(metadata)
            if clear_storage:
                record.storage_uri = ""
            await db.commit()
            await db.refresh(record)
            return record

    async def get_cached_assistant_message(
        self,
        session_id: str,
        idempotency_key: str,
    ) -> MessageRecord | None:
        async with self._session() as db:
            result = await db.execute(
                select(MessageRecord)
                .where(
                    MessageRecord.session_id == session_id,
                    MessageRecord.role == "assistant",
                    MessageRecord.idempotency_key == idempotency_key,
                )
                .order_by(MessageRecord.created_at.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def create_batch(
        self,
        owner_subject: str,
        requested_account_id: str | None,
        items: list[dict[str, str | None]],
    ) -> BatchRecord:
        batch = BatchRecord(
            owner_subject=owner_subject,
            requested_account_id=requested_account_id,
            total_items=len(items),
        )
        batch.items = [
            BatchItemRecord(
                external_id=str(item["external_id"]),
                prompt=str(item["prompt"]),
                session_id=item.get("session_id"),
                requested_account_id=item.get("requested_account_id"),
            )
            for item in items
        ]
        async with self._session() as db:
            db.add(batch)
            await db.commit()
            await db.refresh(batch)
            return batch

    async def list_resumable_batches(self) -> list[BatchRecord]:
        async with self._session() as db:
            result = await db.execute(
                select(BatchRecord)
                .where(BatchRecord.status.in_(("pending", "running")))
                .order_by(BatchRecord.created_at.asc())
            )
            return list(result.scalars())

    async def get_batch(self, batch_id: str) -> BatchRecord | None:
        async with self._session() as db:
            return await db.get(BatchRecord, batch_id)

    async def list_batch_items(self, batch_id: str) -> list[BatchItemRecord]:
        async with self._session() as db:
            result = await db.execute(
                select(BatchItemRecord)
                .where(BatchItemRecord.batch_id == batch_id)
                .order_by(BatchItemRecord.created_at.asc())
            )
            return list(result.scalars())

    async def mark_batch_running(self, batch_id: str) -> None:
        async with self._session() as db:
            record = await db.get(BatchRecord, batch_id)
            if record is None:
                return
            record.status = "running"
            await db.commit()

    async def list_pending_batch_items(self, batch_id: str) -> list[BatchItemRecord]:
        async with self._session() as db:
            result = await db.execute(
                select(BatchItemRecord)
                .where(
                    BatchItemRecord.batch_id == batch_id,
                    BatchItemRecord.status.in_(("pending", "running")),
                )
                .order_by(BatchItemRecord.created_at.asc())
            )
            return list(result.scalars())

    async def mark_batch_item_running(self, item_id: str) -> BatchItemRecord | None:
        async with self._session() as db:
            record = await db.get(BatchItemRecord, item_id)
            if record is None:
                return None
            record.status = "running"
            await db.commit()
            await db.refresh(record)
            return record

    async def mark_batch_item_success(
        self,
        item_id: str,
        session_id: str,
        account_id: str,
        response_text: str,
    ) -> None:
        async with self._session() as db:
            record = await db.get(BatchItemRecord, item_id)
            if record is None:
                return
            record.status = "completed"
            record.session_id = session_id
            record.account_id = account_id
            record.response_text = response_text
            record.error_code = None
            record.error_message = None
            await db.commit()
            await self._refresh_batch_status(db, record.batch_id)

    async def mark_batch_item_failed(
        self,
        item_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        async with self._session() as db:
            record = await db.get(BatchItemRecord, item_id)
            if record is None:
                return
            record.status = "failed"
            record.error_code = error_code
            record.error_message = error_message
            await db.commit()
            await self._refresh_batch_status(db, record.batch_id)

    async def mark_batch_finished(self, batch_id: str) -> None:
        async with self._session() as db:
            await self._refresh_batch_status(db, batch_id, final=True)
            await db.commit()

    def _ensure_database_path(self) -> None:
        url = make_url(self.database_url)
        if not url.drivername.startswith("sqlite"):
            return
        if not url.database or url.database == ":memory:":
            return
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def _run_migrations(self, connection: Connection) -> None:
        inspector = inspect(connection)
        if inspector.has_table("chat_sessions"):
            session_columns = {column["name"] for column in inspector.get_columns("chat_sessions")}
            if "allow_failover" not in session_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE chat_sessions ADD COLUMN allow_failover BOOLEAN NOT NULL DEFAULT 0"
                )
            if "owner_subject" not in session_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE chat_sessions ADD COLUMN owner_subject VARCHAR(255) NOT NULL DEFAULT 'system'"
                )
        if inspector.has_table("chat_messages"):
            message_columns = {column["name"] for column in inspector.get_columns("chat_messages")}
            if "content" not in message_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE chat_messages ADD COLUMN content TEXT NOT NULL DEFAULT ''"
                )

    def _load_metadata(self, raw: str) -> dict:
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}

    def _dump_metadata(self, value: dict | None) -> str:
        return json.dumps(value or {}, ensure_ascii=True)

    async def _refresh_batch_status(
        self,
        db: AsyncSession,
        batch_id: str,
        final: bool = False,
    ) -> None:
        batch = await db.get(BatchRecord, batch_id)
        if batch is None:
            return

        result = await db.execute(
            select(BatchItemRecord).where(BatchItemRecord.batch_id == batch_id)
        )
        items = list(result.scalars())
        batch.total_items = len(items)
        batch.completed_items = sum(item.status == "completed" for item in items)
        batch.failed_items = sum(item.status == "failed" for item in items)

        if not final and any(item.status in {"pending", "running"} for item in items):
            batch.status = "running"
            return

        if batch.failed_items == 0:
            batch.status = "completed"
        elif batch.completed_items == 0:
            batch.status = "failed"
        else:
            batch.status = "partial"

    def _session(self) -> async_sessionmaker[AsyncSession]:
        if self.session_factory is None:
            raise RuntimeError("ChatRepository.start() must be called before use.")
        return self.session_factory()
