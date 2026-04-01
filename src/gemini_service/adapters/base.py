from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class AccountProbeResult:
    account_status: str
    account_status_code: int | None
    status_description: str
    models: list[str] = field(default_factory=list)


class AccountAdapter(Protocol):
    async def probe(self) -> AccountProbeResult: ...

    async def close(self) -> None: ...
