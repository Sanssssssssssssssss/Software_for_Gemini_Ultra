from __future__ import annotations

import asyncio

from gemini_webapi.constants import AccountStatus
from gemini_webapi.exceptions import APIError, AuthError, TemporarilyBlocked, TimeoutError

from ..schemas.accounts import AccountConfig
from .base import AccountProbeResult, MessageChunk, MessageResult


class MockAccountAdapter:
    def __init__(self, config: AccountConfig):
        self.config = config
        self.behavior = config.mock_behavior
        self._send_calls = 0

    async def probe(self) -> AccountProbeResult:
        await self._delay()
        if self.behavior == "probe_timeout":
            raise TimeoutError("mock probe timeout")
        if self.behavior == "reauth":
            raise AuthError("mock reauthentication required")
        if self.behavior == "blocked":
            raise TemporarilyBlocked("mock blocked account")

        status = AccountStatus.AVAILABLE
        description = "mock account ready"
        if self.behavior == "cooldown":
            status = AccountStatus.ACCESS_TEMPORARILY_UNAVAILABLE
            description = "mock account cooling down"
        elif self.behavior == "untrusted":
            status = AccountStatus.ACCOUNT_UNTRUSTED
            description = "mock account untrusted"

        return AccountProbeResult(
            account_status=status.name,
            account_status_code=int(status),
            status_description=description,
            models=list(self.config.mock_models),
        )

    async def send_message(
        self,
        prompt: str,
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ) -> MessageResult:
        await self._delay()
        self._send_calls += 1
        self._maybe_fail_send()
        return MessageResult(
            text=self._render_text(prompt),
            metadata=[f"{self.config.account_id}-cid", f"{self.config.account_id}-rid", f"{self.config.account_id}-rcid"],
        )

    async def stream_message(
        self,
        prompt: str,
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ):
        await self._delay()
        self._send_calls += 1
        self._maybe_fail_send()
        text = self._render_text(prompt)
        midpoint = max(1, len(text) // 2)
        first = text[:midpoint]
        second = text[midpoint:]
        yield MessageChunk(
            text_delta=first,
            text=first,
            metadata=[f"{self.config.account_id}-cid", f"{self.config.account_id}-rid", f"{self.config.account_id}-rcid"],
        )
        await asyncio.sleep(0)
        yield MessageChunk(
            text_delta=second,
            text=text,
            metadata=[f"{self.config.account_id}-cid", f"{self.config.account_id}-rid", f"{self.config.account_id}-rcid"],
        )

    async def close(self) -> None:
        return None

    async def _delay(self) -> None:
        if self.config.mock_delay_ms > 0:
            await asyncio.sleep(self.config.mock_delay_ms / 1000)

    def _maybe_fail_send(self) -> None:
        if self.behavior == "send_timeout":
            raise TimeoutError("mock send timeout")
        if self.behavior == "send_api_error":
            raise APIError("mock upstream failure")
        if self.behavior == "flaky" and self._send_calls % 3 == 0:
            raise APIError("mock intermittent failure")

    def _render_text(self, prompt: str) -> str:
        return f"[{self.config.account_id}|{self.behavior}] {prompt}"
