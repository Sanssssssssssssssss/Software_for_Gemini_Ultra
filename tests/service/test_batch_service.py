from __future__ import annotations

import asyncio

import pytest

from gemini_service.adapters.base import AccountProbeResult, MessageResult
from gemini_service.core.config import Settings
from gemini_service.core.errors import ServiceError
from gemini_service.core.security import AuthContext
from gemini_service.db.repository import ChatRepository
from gemini_service.schemas.common import BatchItem, BatchRequest
from gemini_service.services.account_pool import AccountPool
from gemini_service.services.batch_service import BatchService
from gemini_service.services.chat_service import ChatService


ADMIN = AuthContext(subject="admin-user", role="admin", source="test")
USER_A = AuthContext(subject="user-a", role="user", source="test")
USER_B = AuthContext(subject="user-b", role="user", source="test")


class FakeBatchAdapter:
    def __init__(self, account_id: str):
        self.account_id = account_id

    async def probe(self) -> AccountProbeResult:
        return AccountProbeResult(
            account_status="AVAILABLE",
            account_status_code=1000,
            status_description="ok",
            models=["gemini-3-pro"],
        )

    async def send_message(self, prompt: str, **kwargs) -> MessageResult:
        if "fail" in prompt:
            raise ServiceError(status_code=503, code="provider_unavailable", message="simulated failure")
        return MessageResult(
            text=f"{self.account_id}:{prompt}",
            metadata=[f"{self.account_id}-cid", f"{self.account_id}-rid", f"{self.account_id}-rcid"],
        )

    async def stream_message(self, prompt: str, **kwargs):
        raise NotImplementedError

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


def test_batch_service_executes_and_persists_results(tmp_path):
    settings = _build_settings(tmp_path, [{"account_id": "acc-1", "secure_1psid": "cookie"}])
    pool = AccountPool(settings, adapter_factory=lambda config: FakeBatchAdapter(config.account_id))
    repo = ChatRepository(settings.database_url)

    async def scenario():
        await repo.start()
        await pool.start()
        chat = ChatService(pool=pool, repository=repo)
        batches = BatchService(repository=repo, chat_service=chat)
        await batches.start()
        batch = await batches.create_batch(
            USER_A,
            BatchRequest(
                items=[
                    BatchItem(external_id="one", prompt="hello"),
                    BatchItem(external_id="two", prompt="please fail"),
                ]
            ),
        )
        result = batch
        for _ in range(20):
            result = await batches.get_batch(USER_A, batch.batch_id)
            if result.status in {"completed", "partial", "failed"}:
                break
            await asyncio.sleep(0.05)
        await batches.close()
        await repo.close()
        await pool.close()
        return result

    result = _run(scenario())
    assert result.status == "partial"
    assert result.completed_items == 1
    assert result.failed_items == 1
    assert any(item.external_id == "one" and item.status == "completed" for item in result.items)


def test_batch_service_enforces_ownership(tmp_path):
    settings = _build_settings(tmp_path, [{"account_id": "acc-1", "secure_1psid": "cookie"}])
    pool = AccountPool(settings, adapter_factory=lambda config: FakeBatchAdapter(config.account_id))
    repo = ChatRepository(settings.database_url)

    async def scenario():
        await repo.start()
        await pool.start()
        chat = ChatService(pool=pool, repository=repo)
        batches = BatchService(repository=repo, chat_service=chat)
        await batches.start()
        batch = await batches.create_batch(
            USER_A,
            BatchRequest(items=[BatchItem(external_id="one", prompt="hello")]),
        )
        with pytest.raises(ServiceError) as exc:
            await batches.get_batch(USER_B, batch.batch_id)
        admin_view = await batches.get_batch(ADMIN, batch.batch_id)
        await batches.close()
        await repo.close()
        await pool.close()
        return exc.value, admin_view

    error, admin_view = _run(scenario())
    assert error.code == "forbidden"
    assert admin_view.batch_id
