from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol


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
    generated_media: list["GeneratedMediaResult"] = field(default_factory=list)


@dataclass(slots=True)
class MessageChunk:
    text_delta: str
    text: str
    metadata: list[str]
    generated_media: list["GeneratedMediaResult"] = field(default_factory=list)


@dataclass(slots=True)
class TextPromptPart:
    type: Literal["text"]
    text: str


@dataclass(slots=True)
class AssetPromptPart:
    type: Literal["asset"]
    asset_id: str
    filename: str
    mime_type: str
    absolute_path: Path


PromptPart = TextPromptPart | AssetPromptPart


@dataclass(slots=True)
class GeneratedMediaResult:
    media_type: Literal["image"]
    filename: str
    mime_type: str
    content: bytes
    provider_ref: str | None = None


class AccountAdapter(Protocol):
    async def probe(self) -> AccountProbeResult: ...

    async def send_message(
        self,
        parts: list[PromptPart],
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ) -> MessageResult: ...

    def stream_message(
        self,
        parts: list[PromptPart],
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ) -> AsyncIterator[MessageChunk]: ...

    async def close(self) -> None: ...
