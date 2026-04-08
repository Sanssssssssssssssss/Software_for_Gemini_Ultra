from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from gemini_service.adapters.base import AccountProbeResult, MessageChunk, MessageResult, PromptPart, TextPromptPart
from gemini_service.core.config import Settings
from gemini_service.core.errors import ServiceError
from gemini_service.core.security import AuthContext
from gemini_service.db.repository import ChatRepository
from gemini_service.schemas.common import MessageRequest, SessionCreateRequest
from gemini_service.services.account_pool import AccountPool
from gemini_service.services.asset_service import AssetService
from gemini_service.services.chat_service import ChatService
from gemini_service.storage.local import LocalAssetStorage


ADMIN = AuthContext(subject="admin-user", role="admin", source="test")
USER_A = AuthContext(subject="user-a", role="user", source="test")
USER_B = AuthContext(subject="user-b", role="user", source="test")


class FakeChatAdapter:
    def __init__(self, account_id: str, send_error: ServiceError | None = None):
        self.account_id = account_id
        self.send_error = send_error
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
        parts: list[PromptPart],
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ) -> MessageResult:
        self.calls += 1
        if self.send_error is not None:
            raise self.send_error
        prompt = "\n\n".join(part.text for part in parts if isinstance(part, TextPromptPart))
        return MessageResult(
            text=f"{self.account_id}:echo:{prompt}",
            metadata=[f"{self.account_id}-cid", f"{self.account_id}-rid", f"{self.account_id}-rcid"],
        )

    async def stream_message(
        self,
        parts: list[PromptPart],
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ) -> AsyncIterator[MessageChunk]:
        if self.send_error is not None:
            raise self.send_error
        prompt = "\n\n".join(part.text for part in parts if isinstance(part, TextPromptPart))
        yield MessageChunk(
            text_delta=f"{self.account_id}:echo:{prompt}",
            text=f"{self.account_id}:echo:{prompt}",
            metadata=[f"{self.account_id}-cid", f"{self.account_id}-rid", f"{self.account_id}-rcid"],
        )

    async def close(self) -> None:
        return None


def _run(coro):
    return asyncio.run(coro)


def _build_settings(tmp_path, accounts, **overrides):
    account_path = tmp_path / "accounts.json"
    import json

    account_path.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    overrides.setdefault("account_probe_interval_seconds", 3600)
    return Settings(
        require_auth=False,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'service.db').as_posix()}",
        accounts_config_path=str(account_path),
        **overrides,
    )


def test_chat_service_persists_session_and_history(tmp_path):
    settings = _build_settings(
        tmp_path,
        [{"account_id": "acc-1", "secure_1psid": "cookie"}],
    )
    adapter = FakeChatAdapter("acc-1")
    pool = AccountPool(settings, adapter_factory=lambda config: adapter)
    repo = ChatRepository(settings.database_url)
    asset_service = AssetService(settings=settings, repository=repo, storage=LocalAssetStorage(str(tmp_path / "assets")))

    async def scenario():
        await repo.start()
        await pool.start()
        service = ChatService(pool=pool, repository=repo, asset_service=asset_service)
        session = await service.create_session(SessionCreateRequest(account_id="acc-1"), auth=ADMIN)
        response = await service.send_message(
            MessageRequest(
                session_id=session.session_id,
                message="hello",
                idempotency_key="req-1",
            ),
            auth=ADMIN,
        )
        history = await service.get_history(session.session_id, auth=ADMIN)
        await repo.close()
        await pool.close()
        return session, response, history

    session, response, history = _run(scenario())

    assert session.account_id == "acc-1"
    assert response.content == "acc-1:echo:hello"
    assert response.cached is False
    assert len(history.items) == 2
    assert history.items[0].role == "user"
    assert history.items[1].role == "assistant"


def test_chat_service_reuses_idempotent_response(tmp_path):
    settings = _build_settings(
        tmp_path,
        [{"account_id": "acc-1", "secure_1psid": "cookie"}],
    )
    adapter = FakeChatAdapter("acc-1")
    pool = AccountPool(settings, adapter_factory=lambda config: adapter)
    repo = ChatRepository(settings.database_url)
    asset_service = AssetService(settings=settings, repository=repo, storage=LocalAssetStorage(str(tmp_path / "assets")))

    async def scenario():
        await repo.start()
        await pool.start()
        service = ChatService(pool=pool, repository=repo, asset_service=asset_service)
        session = await service.create_session(SessionCreateRequest(account_id="acc-1"), auth=ADMIN)
        first = await service.send_message(
            MessageRequest(
                session_id=session.session_id,
                message="hello",
                idempotency_key="req-1",
            ),
            auth=ADMIN,
        )
        second = await service.send_message(
            MessageRequest(
                session_id=session.session_id,
                message="hello",
                idempotency_key="req-1",
            ),
            auth=ADMIN,
        )
        await repo.close()
        await pool.close()
        return first, second

    first, second = _run(scenario())

    assert first.cached is False
    assert second.cached is True
    assert adapter.calls == 1


def test_chat_service_blocks_non_admin_account_pinning(tmp_path):
    settings = _build_settings(tmp_path, [{"account_id": "acc-1", "secure_1psid": "cookie"}])
    pool = AccountPool(settings, adapter_factory=lambda config: FakeChatAdapter("acc-1"))
    repo = ChatRepository(settings.database_url)
    asset_service = AssetService(settings=settings, repository=repo, storage=LocalAssetStorage(str(tmp_path / "assets")))

    async def scenario():
        await repo.start()
        await pool.start()
        service = ChatService(pool=pool, repository=repo, asset_service=asset_service)
        with pytest.raises(ServiceError) as exc:
            await service.create_session(SessionCreateRequest(account_id="acc-1"), auth=USER_A)
        await repo.close()
        await pool.close()
        return exc.value

    error = _run(scenario())
    assert error.code == "forbidden"


def test_chat_service_enforces_session_ownership(tmp_path):
    settings = _build_settings(tmp_path, [{"account_id": "acc-1", "secure_1psid": "cookie"}])
    pool = AccountPool(settings, adapter_factory=lambda config: FakeChatAdapter("acc-1"))
    repo = ChatRepository(settings.database_url)
    asset_service = AssetService(settings=settings, repository=repo, storage=LocalAssetStorage(str(tmp_path / "assets")))

    async def scenario():
        await repo.start()
        await pool.start()
        service = ChatService(pool=pool, repository=repo, asset_service=asset_service)
        session = await service.create_session(SessionCreateRequest(), auth=USER_A)
        with pytest.raises(ServiceError) as exc:
            await service.get_history(session.session_id, auth=USER_B)
        admin_history = await service.get_history(session.session_id, auth=ADMIN)
        await repo.close()
        await pool.close()
        return exc.value, admin_history

    error, admin_history = _run(scenario())
    assert error.code == "forbidden"
    assert admin_history.session_id


def test_chat_service_keeps_sticky_session_without_failover(tmp_path):
    settings = _build_settings(
        tmp_path,
        [
            {"account_id": "acc-1", "secure_1psid": "cookie-a"},
            {"account_id": "acc-2", "secure_1psid": "cookie-b"},
        ],
    )
    adapters = {
        "acc-1": FakeChatAdapter(
            "acc-1",
            send_error=ServiceError(status_code=503, code="provider_blocked", message="blocked"),
        ),
        "acc-2": FakeChatAdapter("acc-2"),
    }
    pool = AccountPool(settings, adapter_factory=lambda config: adapters[config.account_id])
    repo = ChatRepository(settings.database_url)
    asset_service = AssetService(settings=settings, repository=repo, storage=LocalAssetStorage(str(tmp_path / "assets")))

    async def scenario():
        await repo.start()
        await pool.start()
        service = ChatService(pool=pool, repository=repo, asset_service=asset_service)
        session = await service.create_session(SessionCreateRequest(account_id="acc-1", allow_failover=False), auth=ADMIN)

        with pytest.raises(ServiceError) as first_error:
            await service.send_message(MessageRequest(session_id=session.session_id, message="hello"), auth=ADMIN)
        assert first_error.value.code == "provider_blocked"

        with pytest.raises(ServiceError) as second_error:
            await service.send_message(MessageRequest(session_id=session.session_id, message="retry"), auth=ADMIN)
        assert second_error.value.code == "preferred_account_unavailable"

        record = await repo.get_session(session.session_id)
        await repo.close()
        await pool.close()
        return record

    record = _run(scenario())
    assert record is not None
    assert record.account_id == "acc-1"


def test_chat_service_can_fail_over_when_session_allows_it(tmp_path):
    settings = _build_settings(
        tmp_path,
        [
            {"account_id": "acc-1", "secure_1psid": "cookie-a"},
            {"account_id": "acc-2", "secure_1psid": "cookie-b"},
        ],
    )
    adapters = {
        "acc-1": FakeChatAdapter("acc-1"),
        "acc-2": FakeChatAdapter("acc-2"),
    }
    pool = AccountPool(settings, adapter_factory=lambda config: adapters[config.account_id])
    repo = ChatRepository(settings.database_url)
    asset_service = AssetService(settings=settings, repository=repo, storage=LocalAssetStorage(str(tmp_path / "assets")))

    async def scenario():
        await repo.start()
        await pool.start()
        service = ChatService(pool=pool, repository=repo, asset_service=asset_service)
        session = await service.create_session(SessionCreateRequest(account_id="acc-1", allow_failover=True), auth=ADMIN)

        runtime = pool.get_runtime("acc-1")
        assert runtime is not None
        pool.record_provider_failure(
            runtime,
            ServiceError(status_code=503, code="provider_blocked", message="blocked"),
        )

        response = await service.send_message(MessageRequest(session_id=session.session_id, message="hello"), auth=ADMIN)
        record = await repo.get_session(session.session_id)
        history = await service.get_history(session.session_id, auth=ADMIN)
        await repo.close()
        await pool.close()
        return response, record, history

    response, record, history = _run(scenario())
    assert response.account_id == "acc-2"
    assert response.content == "acc-2:echo:hello"
    assert record is not None
    assert record.account_id == "acc-2"
    assert len(history.items) == 2


def test_stream_message_returns_error_event_when_sticky_account_is_unavailable(tmp_path):
    settings = _build_settings(
        tmp_path,
        [
            {"account_id": "acc-1", "secure_1psid": "cookie-a"},
            {"account_id": "acc-2", "secure_1psid": "cookie-b"},
        ],
    )
    adapters = {
        "acc-1": FakeChatAdapter("acc-1"),
        "acc-2": FakeChatAdapter("acc-2"),
    }
    pool = AccountPool(settings, adapter_factory=lambda config: adapters[config.account_id])
    repo = ChatRepository(settings.database_url)
    asset_service = AssetService(settings=settings, repository=repo, storage=LocalAssetStorage(str(tmp_path / "assets")))

    async def scenario():
        await repo.start()
        await pool.start()
        service = ChatService(pool=pool, repository=repo, asset_service=asset_service)
        session = await service.create_session(SessionCreateRequest(account_id="acc-1", allow_failover=False), auth=ADMIN)

        runtime = pool.get_runtime("acc-1")
        assert runtime is not None
        pool.record_provider_failure(
            runtime,
            ServiceError(status_code=503, code="provider_blocked", message="blocked"),
        )

        events = [event async for event in service.stream_message(MessageRequest(session_id=session.session_id, message="hello"), auth=ADMIN)]
        await repo.close()
        await pool.close()
        return events

    events = _run(scenario())
    assert len(events) == 1
    assert "event: error" in events[0]
    assert "preferred_account_unavailable" in events[0]
