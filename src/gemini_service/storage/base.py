from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(slots=True)
class StoredAsset:
    storage_backend: str
    storage_uri: str
    absolute_path: Path


class AssetStorage(Protocol):
    async def save_bytes(
        self,
        *,
        asset_id: str,
        filename: str,
        content: bytes,
    ) -> StoredAsset: ...

    def resolve(self, storage_uri: str) -> Path: ...

    async def delete(self, storage_uri: str) -> None: ...
