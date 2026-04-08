from __future__ import annotations

import hashlib
import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import UploadFile

from ..core.config import Settings
from ..core.errors import ServiceError
from ..core.security import AuthContext
from ..db.models import MediaAssetRecord
from ..db.repository import ChatRepository
from ..storage.base import AssetStorage


@dataclass(slots=True)
class ValidatedUpload:
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    content: bytes


class AssetService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: ChatRepository,
        storage: AssetStorage,
    ):
        self.settings = settings
        self.repository = repository
        self.storage = storage
        self.logger = logging.getLogger("gemini_service.assets")

    async def create_upload(
        self,
        *,
        auth: AuthContext,
        upload: UploadFile,
        temporary: bool = True,
        metadata: dict | None = None,
    ) -> MediaAssetRecord:
        validated = await self._validate_upload(upload)
        self._log_asset_event(
            "upload_started",
            owner_subject=auth.subject,
            filename=validated.filename,
            mime_type=validated.mime_type,
            details={"temporary": temporary, "size_bytes": validated.size_bytes},
        )
        asset = await self.repository.create_asset(
            owner_subject=auth.subject,
            filename=validated.filename,
            mime_type=validated.mime_type,
            size_bytes=validated.size_bytes,
            sha256=validated.sha256,
            status="uploading",
            storage_backend=self.settings.asset_storage_backend,
            storage_uri="pending",
            metadata=metadata or {},
            expires_at=self._default_expiry() if temporary else None,
        )
        try:
            stored = await self.storage.save_bytes(
                asset_id=asset.id,
                filename=validated.filename,
                content=validated.content,
            )
            updated = await self.repository.mark_asset_stored(
                asset_id=asset.id,
                storage_backend=stored.storage_backend,
                storage_uri=stored.storage_uri,
                status="available",
            )
            if updated is None:
                raise ServiceError(
                    status_code=500,
                    code="asset_store_failed",
                    message="Asset persisted to storage but could not be finalized in metadata.",
                )
            self._log_asset_event(
                "upload_completed",
                asset_id=updated.id,
                owner_subject=updated.owner_subject,
                filename=updated.filename,
                mime_type=updated.mime_type,
                details={"temporary": temporary, "size_bytes": updated.size_bytes},
            )
            return updated
        except Exception as exc:
            await self.repository.mark_asset_failed(asset.id, str(exc))
            self._log_asset_event(
                "upload_failed",
                asset_id=asset.id,
                owner_subject=auth.subject,
                filename=validated.filename,
                mime_type=validated.mime_type,
                details={"error": str(exc)},
            )
            raise

    async def create_generated_asset(
        self,
        *,
        auth: AuthContext,
        filename: str,
        mime_type: str,
        content: bytes,
        provider_ref: str | None = None,
        metadata: dict | None = None,
        expires_at: datetime | None = None,
    ) -> MediaAssetRecord:
        sha256 = hashlib.sha256(content).hexdigest()
        asset = await self.repository.create_asset(
            owner_subject=auth.subject,
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
            sha256=sha256,
            status="uploading",
            storage_backend=self.settings.asset_storage_backend,
            storage_uri="pending",
            provider_ref=provider_ref,
            metadata=metadata or {},
            expires_at=expires_at,
        )
        try:
            stored = await self.storage.save_bytes(
                asset_id=asset.id,
                filename=filename,
                content=content,
            )
            updated = await self.repository.mark_asset_stored(
                asset_id=asset.id,
                storage_backend=stored.storage_backend,
                storage_uri=stored.storage_uri,
                status="available",
            )
            if updated is None:
                raise ServiceError(
                    status_code=500,
                    code="asset_store_failed",
                    message="Generated media persisted to storage but metadata finalization failed.",
                )
            self._log_asset_event(
                "media_generated",
                asset_id=updated.id,
                owner_subject=updated.owner_subject,
                filename=updated.filename,
                mime_type=updated.mime_type,
                details={"provider_ref": provider_ref or "", "size_bytes": updated.size_bytes},
            )
            return updated
        except Exception as exc:
            await self.repository.mark_asset_failed(asset.id, str(exc))
            raise

    async def get_asset_for_read(
        self,
        *,
        asset_id: str,
        auth: AuthContext,
    ) -> MediaAssetRecord:
        asset = await self.repository.get_asset(asset_id)
        if asset is None:
            raise ServiceError(
                status_code=404,
                code="asset_not_found",
                message=f"Asset {asset_id} does not exist.",
            )
        if not auth.is_admin and asset.owner_subject != auth.subject:
            raise ServiceError(
                status_code=403,
                code="forbidden",
                message="You do not have access to this asset.",
            )
        return asset

    def resolve_asset_path(self, asset: MediaAssetRecord) -> Path:
        if asset.storage_backend != "local":
            raise ServiceError(
                status_code=501,
                code="asset_backend_unsupported",
                message=f"Storage backend {asset.storage_backend} is not readable in this deployment.",
            )
        return self.storage.resolve(asset.storage_uri)

    async def _validate_upload(self, upload: UploadFile) -> ValidatedUpload:
        content = await upload.read()
        filename = upload.filename or "upload.bin"
        if not content:
            raise ServiceError(
                status_code=400,
                code="asset_empty",
                message="Uploaded file is empty.",
            )
        if len(content) > self.settings.asset_max_upload_bytes:
            raise ServiceError(
                status_code=413,
                code="asset_too_large",
                message="Uploaded file exceeds the configured size limit.",
                details={"max_bytes": self.settings.asset_max_upload_bytes},
            )

        mime_type = self._sniff_mime_type(filename, content)
        if mime_type not in self.settings.asset_allowed_mime_values:
            raise ServiceError(
                status_code=415,
                code="asset_type_not_supported",
                message=f"Unsupported upload type: {mime_type}.",
                details={"allowed_mime_types": self.settings.asset_allowed_mime_values},
            )

        sha256 = hashlib.sha256(content).hexdigest()
        return ValidatedUpload(
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
            sha256=sha256,
            content=content,
        )

    def _default_expiry(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(hours=self.settings.asset_default_ttl_hours)

    def _sniff_mime_type(self, filename: str, content: bytes) -> str:
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "image/webp"
        if content.startswith(b"%PDF-"):
            return "application/pdf"
        if content[:2] == b"PK":
            if self._is_pptx(content):
                return "application/vnd.openxmlformats-officedocument.presentationml.presentation"

        raise ServiceError(
            status_code=415,
            code="asset_type_not_supported",
            message=f"Unsupported or unrecognized file format for {filename}.",
        )

    def _is_pptx(self, content: bytes) -> bool:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names:
                    return False
                if not any(name.startswith("ppt/") for name in names):
                    return False
                content_types = archive.read("[Content_Types].xml")
                return b"presentationml.presentation.main+xml" in content_types
        except zipfile.BadZipFile:
            return False

    def _log_asset_event(
        self,
        event: str,
        *,
        asset_id: str | None = None,
        owner_subject: str,
        filename: str,
        mime_type: str,
        details: dict | None = None,
    ) -> None:
        self.logger.info(
            event,
            extra={
                "event": event,
                "asset_id": asset_id or "",
                "owner_subject": owner_subject,
                "asset_filename": filename,
                "mime_type": mime_type,
                "details": details or {},
            },
        )
