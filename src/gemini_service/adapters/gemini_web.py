from __future__ import annotations

from gemini_webapi import GeminiClient
from gemini_webapi.constants import Model

from ..schemas.accounts import AccountConfig
from .base import AccountProbeResult, MessageChunk, MessageResult


class GeminiWebAccountAdapter:
    def __init__(self, config: AccountConfig):
        self.config = config
        self.client = GeminiClient(
            secure_1psid=config.secure_1psid,
            secure_1psidts=config.secure_1psidts,
            proxy=config.proxy,
            verify=config.verify_ssl,
        )

    async def probe(self) -> AccountProbeResult:
        await self.client.init(
            timeout=self.config.request_timeout_seconds,
            auto_close=False,
            auto_refresh=True,
            refresh_interval=max(60, self.config.cooldown_seconds),
            verbose=False,
        )
        models = [model.model_name for model in (self.client.list_models() or []) if model.is_available]
        return AccountProbeResult(
            account_status=self.client.account_status.name,
            account_status_code=int(self.client.account_status),
            status_description=self.client.account_status.description,
            models=models,
        )

    async def send_message(
        self,
        prompt: str,
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ) -> MessageResult:
        chat = self.client.start_chat(
            metadata=chat_metadata if any(chat_metadata or []) else None,
            model=model or Model.UNSPECIFIED,
            gem=gem,
        )
        output = await chat.send_message(prompt=prompt, temporary=temporary)
        return MessageResult(
            text=output.text,
            metadata=list(output.metadata[:3]),
            thoughts=output.thoughts or "",
        )

    async def stream_message(
        self,
        prompt: str,
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ):
        chat = self.client.start_chat(
            metadata=chat_metadata if any(chat_metadata or []) else None,
            model=model or Model.UNSPECIFIED,
            gem=gem,
        )
        async for output in chat.send_message_stream(prompt=prompt, temporary=temporary):
            yield MessageChunk(
                text_delta=output.text_delta,
                text=output.text,
                metadata=list(output.metadata[:3]),
            )

    async def close(self) -> None:
        await self.client.close()
