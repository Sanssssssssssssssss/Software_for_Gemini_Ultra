from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from gemini_service.adapters.base import AccountProbeResult, MessageChunk, MessageResult
from gemini_service.core.config import Settings
from gemini_service.db.repository import ChatRepository
from gemini_service.schemas.common import MessageRequest, SessionCreateRequest
from gemini_service.services.account_pool import AccountPool
from gemini_service.services.chat_service import ChatService


class FakeChatAdapter:
    def __init__(self):
        self.calls = 0

    async def probe(self) -> AccountProbeResult:
        return AccountProbeResult(
            account_status="AVAILABLE",
            account_status_code=1000,
            status_description="ok",
            models=["gemini-3-pro"],
        )

    async def send_message(
        self,
        prompt: str,
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ) -> MessageResult:
        self.calls += 1
        return MessageResult(
            text=f"echo:{prompt}",
            metadata=["cid-1", "rid-1", "rcid-1"],
        )

    async def stream_message(
        self,
        prompt: str,
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ) -> AsyncIterator[MessageChunk]:
        yield MessageChunk(
            text_delta=f"echo:{prompt}",
            text=f"echo:{prompt}",
            metadata=["cid-1", "rid-1", "rcid-1"],
        )

    async def close(self) -> None:
        return None


def _run(coro):
    return asyncio.run(coro)


def test_chat_service_persists_session_and_history(tmp_path):
    account_path = tmp_path / "accounts.json"
    account_path.write_text(
        '{"accounts":[{"account_id":"acc-1","secure_1psid":"cookie"}]}',
        encoding="utf-8",
    )
    settings = Settings(
        require_auth=False,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'service.db').as_posix()}",
        accounts_config_path=str(account_path),
        account_probe_interval_seconds=0,
    )
    adapter = FakeChatAdapter()
    pool = AccountPool(settings, adapter_factory=lambda config: adapter)
    repo = ChatRepository(settings.database_url)

    async def scenario():
        await repo.start()
        await pool.start()
        service = ChatService(pool=pool, repository=repo)
        session = await service.create_session(SessionCreateRequest(account_id="acc-1"))
        response = await service.send_message(
            MessageRequest(
                session_id=session.session_id,
                message="hello",
                idempotency_key="req-1",
            )
        )
        history = await service.get_history(session.session_id)
        await repo.close()
        await pool.close()
        return session, response, history

    session, response, history = _run(scenario())

    assert session.account_id == "acc-1"
    assert response.content == "echo:hello"
    assert response.cached is False
    assert len(history.items) == 2
    assert history.items[0].role == "user"
    assert history.items[1].role == "assistant"


def test_chat_service_reuses_idempotent_response(tmp_path):
    account_path = tmp_path / "accounts.json"
    account_path.write_text(
        '{"accounts":[{"account_id":"acc-1","secure_1psid":"cookie"}]}',
        encoding="utf-8",
    )
    settings = Settings(
        require_auth=False,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'service.db').as_posix()}",
        accounts_config_path=str(account_path),
        account_probe_interval_seconds=0,
    )
    adapter = FakeChatAdapter()
    pool = AccountPool(settings, adapter_factory=lambda config: adapter)
    repo = ChatRepository(settings.database_url)

    async def scenario():
        await repo.start()
        await pool.start()
        service = ChatService(pool=pool, repository=repo)
        session = await service.create_session(SessionCreateRequest(account_id="acc-1"))
        first = await service.send_message(
            MessageRequest(
                session_id=session.session_id,
                message="hello",
                idempotency_key="req-1",
            )
        )
        second = await service.send_message(
            MessageRequest(
                session_id=session.session_id,
                message="hello",
                idempotency_key="req-1",
            )
        )
        await repo.close()
        await pool.close()
        return first, second

    first, second = _run(scenario())

    assert first.cached is False
    assert second.cached is True
    assert adapter.calls == 1
