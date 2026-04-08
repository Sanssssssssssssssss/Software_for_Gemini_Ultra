from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ..core.config import Settings
from ..core.telemetry import TelemetryService
from ..db.repository import ChatRepository
from ..storage.base import AssetStorage


class AssetCleanupService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: ChatRepository,
        storage: AssetStorage,
        telemetry: TelemetryService | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.storage = storage
        self.telemetry = telemetry
        self.logger = logging.getLogger("gemini_service.assets")
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="asset-cleanup")

    async def close(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def run_once(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        orphan_cutoff = now - timedelta(hours=self.settings.asset_orphan_grace_hours)
        expired_assets = await self.repository.list_expired_assets(now)
        orphan_assets = await self.repository.list_orphan_assets(orphan_cutoff)
        expired_count = 0
        deleted_count = 0

        for asset in expired_assets:
            if asset.storage_uri:
                await self.storage.delete(asset.storage_uri)
            await self.repository.mark_asset_status(
                asset_id=asset.id,
                status="expired",
                clear_storage=True,
                metadata_updates={"expired_at": now.isoformat()},
            )
            expired_count += 1
            self.logger.info(
                "asset_expired",
                extra={
                    "event": "asset_expired",
                    "asset_id": asset.id,
                    "owner_subject": asset.owner_subject,
                    "mime_type": asset.mime_type,
                },
            )

        for asset in orphan_assets:
            if asset.storage_uri:
                await self.storage.delete(asset.storage_uri)
            await self.repository.mark_asset_status(
                asset_id=asset.id,
                status="deleted",
                clear_storage=True,
                metadata_updates={"deleted_at": now.isoformat(), "reason": "orphan_cleanup"},
            )
            deleted_count += 1
            self.logger.info(
                "asset_deleted",
                extra={
                    "event": "asset_deleted",
                    "asset_id": asset.id,
                    "owner_subject": asset.owner_subject,
                    "mime_type": asset.mime_type,
                    "reason": "orphan_cleanup",
                },
            )

        if self.telemetry is not None:
            self.telemetry.record_asset_cleanup(expired_count=expired_count, deleted_count=deleted_count)

        return {
            "expired": expired_count,
            "deleted": deleted_count,
        }

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.run_once()
            except Exception as exc:
                self.logger.exception("asset_cleanup_failed", extra={"event": "asset_cleanup_failed", "error": str(exc)})
            await asyncio.sleep(max(30, self.settings.asset_cleanup_interval_seconds))
