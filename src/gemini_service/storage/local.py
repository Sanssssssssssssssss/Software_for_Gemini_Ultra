from __future__ import annotations

import re
from pathlib import Path

from .base import StoredAsset


def _safe_filename(filename: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", filename).strip(".-")
    return sanitized or "asset.bin"


class LocalAssetStorage:
    def __init__(self, root_path: str):
        self.root_path = Path(root_path).expanduser().resolve()
        self.root_path.mkdir(parents=True, exist_ok=True)

    async def save_bytes(
        self,
        *,
        asset_id: str,
        filename: str,
        content: bytes,
    ) -> StoredAsset:
        safe_name = _safe_filename(filename)
        asset_dir = self.root_path / asset_id
        asset_dir.mkdir(parents=True, exist_ok=True)
        destination = asset_dir / safe_name
        destination.write_bytes(content)
        return StoredAsset(
            storage_backend="local",
            storage_uri=str(Path(asset_id) / safe_name),
            absolute_path=destination,
        )

    def resolve(self, storage_uri: str) -> Path:
        resolved = (self.root_path / storage_uri).resolve()
        try:
            resolved.relative_to(self.root_path)
        except ValueError as exc:
            raise ValueError("Resolved asset path escapes the configured storage root.") from exc
        return resolved

    async def delete(self, storage_uri: str) -> None:
        path = self.resolve(storage_uri)
        if path.exists():
            path.unlink()
        parent = path.parent
        if parent.exists():
            try:
                parent.rmdir()
            except OSError:
                return
