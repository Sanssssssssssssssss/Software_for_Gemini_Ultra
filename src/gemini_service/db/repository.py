from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base, MessageRecord, SessionRecord


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

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()

    async def create_session(
        self,
        account_id: str,
        routing_policy: str,
        model_name: str | None = None,
        gem_id: str | None = None,
        metadata: dict | None = None,
    ) -> SessionRecord:
        session_record = SessionRecord(
            account_id=account_id,
            routing_policy=routing_policy,
            model_name=model_name,
            gem_id=gem_id,
            metadata_json=json.dumps(metadata or {}, ensure_ascii=True),
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

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        idempotency_key: str | None = None,
    ) -> MessageRecord:
        record = MessageRecord(
            session_id=session_id,
            role=role,
            content=content,
            idempotency_key=idempotency_key,
        )
        async with self._session() as db:
            db.add(record)
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

    async def list_sessions(self, limit: int = 50) -> list[SessionRecord]:
        async with self._session() as db:
            result = await db.execute(
                select(SessionRecord)
                .order_by(SessionRecord.updated_at.desc())
                .limit(limit)
            )
            return list(result.scalars())

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

    def _ensure_database_path(self) -> None:
        url = make_url(self.database_url)
        if not url.drivername.startswith("sqlite"):
            return
        if not url.database or url.database == ":memory:":
            return
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def _session(self) -> async_sessionmaker[AsyncSession]:
        if self.session_factory is None:
            raise RuntimeError("ChatRepository.start() must be called before use.")
        return self.session_factory()
