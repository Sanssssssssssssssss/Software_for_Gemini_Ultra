from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO

from fastapi import UploadFile

from gemini_service.core.config import Settings
from gemini_service.core.security import AuthContext
from gemini_service.db.repository import ChatRepository
from gemini_service.services.asset_cleanup_service import AssetCleanupService
from gemini_service.services.asset_service import AssetService
from gemini_service.storage.local import LocalAssetStorage


USER = AuthContext(subject="user-a", role="user", source="test")
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDAT\x08\x99c``\x00\x00\x00\x04\x00\x01"
    b"\x0b\xe7\x02\x9d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _run(coro):
    return asyncio.run(coro)


def test_asset_cleanup_expires_and_deletes_orphans(tmp_path):
    settings = Settings(
        require_auth=False,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'service.db').as_posix()}",
        accounts_config_path=str(tmp_path / "accounts.json"),
        asset_root_path=str(tmp_path / "assets"),
        asset_cleanup_interval_seconds=3600,
        asset_orphan_grace_hours=0,
    )
    storage = LocalAssetStorage(settings.asset_root_path)
    repo = ChatRepository(settings.database_url)
    asset_service = AssetService(settings=settings, repository=repo, storage=storage)
    cleanup = AssetCleanupService(settings=settings, repository=repo, storage=storage)

    async def scenario():
        await repo.start()
        expired = await asset_service.create_upload(
            auth=USER,
            upload=UploadFile(filename="expired.png", file=BytesIO(PNG_BYTES)),
            temporary=True,
        )
        orphan = await asset_service.create_upload(
            auth=USER,
            upload=UploadFile(filename="orphan.png", file=BytesIO(PNG_BYTES)),
            temporary=False,
        )
        await repo.mark_asset_status(
            asset_id=expired.id,
            status="available",
            metadata_updates={},
        )
        await repo.mark_asset_status(
            asset_id=orphan.id,
            status="available",
            metadata_updates={},
        )
        async with repo._session() as db:
            expired_record = await db.get(type(expired), expired.id)
            orphan_record = await db.get(type(orphan), orphan.id)
            expired_record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            orphan_record.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            await db.commit()

        result = await cleanup.run_once()
        refreshed_expired = await repo.get_asset(expired.id)
        refreshed_orphan = await repo.get_asset(orphan.id)
        await repo.close()
        return result, refreshed_expired, refreshed_orphan

    result, refreshed_expired, refreshed_orphan = _run(scenario())

    assert result["expired"] == 1
    assert result["deleted"] == 1
    assert refreshed_expired is not None and refreshed_expired.status == "expired"
    assert refreshed_orphan is not None and refreshed_orphan.status == "deleted"
