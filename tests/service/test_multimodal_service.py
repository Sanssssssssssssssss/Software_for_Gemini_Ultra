from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from io import BytesIO

from fastapi import UploadFile

from gemini_service.adapters.base import (
    AccountProbeResult,
    AssetPromptPart,
    GeneratedMediaResult,
    MessageChunk,
    MessageResult,
    PromptPart,
    TextPromptPart,
)
from gemini_service.core.config import Settings
from gemini_service.core.security import AuthContext
from gemini_service.db.repository import ChatRepository
from gemini_service.schemas.common import MessageAssetPart, MessageRequest, MessageTextPart, SessionCreateRequest
from gemini_service.services.account_pool import AccountPool
from gemini_service.services.asset_service import AssetService
from gemini_service.services.chat_service import ChatService
from gemini_service.storage.local import LocalAssetStorage


ADMIN = AuthContext(subject="admin-user", role="admin", source="test")
USER = AuthContext(subject="user-a", role="user", source="test")
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDAT\x08\x99c``\x00\x00\x00\x04\x00\x01"
    b"\x0b\xe7\x02\x9d\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeMultimodalAdapter:
    def __init__(self, account_id: str):
        self.account_id = account_id
        self.last_parts: list[PromptPart] = []

    async def probe(self) -> AccountProbeResult:
        return AccountProbeResult(
            account_status="AVAILABLE",
            account_status_code=1000,
            status_description="ok",
            models=["gemini-3-pro"],
        )

    async def send_message(self, parts: list[PromptPart], **kwargs) -> MessageResult:
        self.last_parts = parts
        text = "\n".join(part.text for part in parts if isinstance(part, TextPromptPart))
        file_count = sum(isinstance(part, AssetPromptPart) for part in parts)
        return MessageResult(
            text=f"{self.account_id}:files={file_count}:{text}",
            metadata=[f"{self.account_id}-cid", f"{self.account_id}-rid", f"{self.account_id}-rcid"],
        )

    async def stream_message(self, parts: list[PromptPart], **kwargs) -> AsyncIterator[MessageChunk]:
        self.last_parts = parts
        yield MessageChunk(
            text_delta="hello",
            text="hello",
            metadata=[f"{self.account_id}-cid", f"{self.account_id}-rid", f"{self.account_id}-rcid"],
            generated_media=[
                GeneratedMediaResult(
                    media_type="image",
                    filename="generated.png",
                    mime_type="image/png",
                    content=PNG_BYTES,
                    provider_ref="provider://generated.png",
                )
            ],
        )

    async def close(self) -> None:
        return None


def _run(coro):
    return asyncio.run(coro)


def _build_settings(tmp_path, accounts, **overrides):
    account_path = tmp_path / "accounts.json"
    account_path.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    overrides.setdefault("account_probe_interval_seconds", 3600)
    return Settings(
        require_auth=False,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'service.db').as_posix()}",
        accounts_config_path=str(account_path),
        asset_root_path=str(tmp_path / "assets"),
        **overrides,
    )


def test_message_request_accepts_legacy_and_parts():
    legacy = MessageRequest(session_id="s1", message="hello")
    parts = MessageRequest(
        session_id="s1",
        parts=[MessageTextPart(type="text", text="hello"), MessageAssetPart(type="asset", asset_id="asset-1")],
    )

    assert legacy.message == "hello"
    assert len(parts.parts) == 2


def test_asset_upload_and_multimodal_history(tmp_path):
    settings = _build_settings(tmp_path, [{"account_id": "acc-1", "secure_1psid": "cookie"}])
    adapter = FakeMultimodalAdapter("acc-1")
    pool = AccountPool(settings, adapter_factory=lambda config: adapter)
    repo = ChatRepository(settings.database_url)
    asset_service = AssetService(settings=settings, repository=repo, storage=LocalAssetStorage(settings.asset_root_path))

    async def scenario():
        await repo.start()
        await pool.start()
        chat = ChatService(pool=pool, repository=repo, asset_service=asset_service)
        uploaded = await asset_service.create_upload(
            auth=USER,
            upload=UploadFile(filename="sample.png", file=BytesIO(PNG_BYTES)),
            temporary=True,
        )
        session = await chat.create_session(SessionCreateRequest(), auth=USER)
        response = await chat.send_message(
            MessageRequest(
                session_id=session.session_id,
                parts=[
                    MessageTextPart(type="text", text="summarize this"),
                    MessageAssetPart(type="asset", asset_id=uploaded.id),
                ],
            ),
            auth=USER,
        )
        history = await chat.get_history(session.session_id, auth=USER)
        await repo.close()
        await pool.close()
        return uploaded, response, history

    uploaded, response, history = _run(scenario())

    assert uploaded.mime_type == "image/png"
    assert response.content == "acc-1:files=1:summarize this"
    assert history.items[0].parts[1].asset is not None
    assert history.items[0].parts[1].asset.asset_id == uploaded.id


def test_stream_message_emits_media_event(tmp_path):
    settings = _build_settings(tmp_path, [{"account_id": "acc-1", "secure_1psid": "cookie"}])
    adapter = FakeMultimodalAdapter("acc-1")
    pool = AccountPool(settings, adapter_factory=lambda config: adapter)
    repo = ChatRepository(settings.database_url)
    asset_service = AssetService(settings=settings, repository=repo, storage=LocalAssetStorage(settings.asset_root_path))

    async def scenario():
        await repo.start()
        await pool.start()
        chat = ChatService(pool=pool, repository=repo, asset_service=asset_service)
        session = await chat.create_session(SessionCreateRequest(), auth=USER)
        events = [event async for event in chat.stream_message(MessageRequest(session_id=session.session_id, message="draw"), auth=USER)]
        history = await chat.get_history(session.session_id, auth=USER)
        await repo.close()
        await pool.close()
        return events, history

    events, history = _run(scenario())

    assert any(event.startswith("event: media") for event in events)
    assert any(event.startswith("event: done") for event in events)
    assert history.items[1].media
    assert history.items[1].media[0].mime_type == "image/png"
