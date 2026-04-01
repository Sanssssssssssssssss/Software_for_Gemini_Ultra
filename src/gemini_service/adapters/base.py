from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class AccountProbeResult:
    account_status: str
    account_status_code: int | None
    status_description: str
    models: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MessageResult:
    text: str
    metadata: list[str]
    thoughts: str = ""


@dataclass(slots=True)
class MessageChunk:
    text_delta: str
    text: str
    metadata: list[str]


class AccountAdapter(Protocol):
    async def probe(self) -> AccountProbeResult: ...

    async def send_message(
        self,
        prompt: str,
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ) -> MessageResult: ...

    def stream_message(
        self,
        prompt: str,
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ) -> AsyncIterator[MessageChunk]: ...

    async def close(self) -> None: ...
