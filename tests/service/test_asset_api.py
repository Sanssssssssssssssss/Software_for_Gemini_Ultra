from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile
import asyncio

import pytest

from gemini_service.core.config import Settings
from gemini_service.core.errors import ServiceError
from gemini_service.db.repository import ChatRepository
from gemini_service.services.asset_service import AssetService
from gemini_service.storage.local import LocalAssetStorage


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDAT\x08\x99c``\x00\x00\x00\x04\x00\x01"
    b"\x0b\xe7\x02\x9d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def build_minimal_pptx() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", """
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
</Types>
""".strip())
        archive.writestr("ppt/presentation.xml", "<p:presentation xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main' />")
    return buffer.getvalue()


def test_upload_asset_and_enforce_permissions(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="alice|token-a|user,bob|token-b|user,admin|token-admin|admin",
    ) as client:
        response = client.post(
            "/v1/uploads",
            headers={"Authorization": "Bearer token-a"},
            files={"file": ("sample.png", PNG_BYTES, "image/png")},
            data={"temporary": "true"},
        )

        assert response.status_code == 200
        asset_id = response.json()["asset"]["asset_id"]

        owner_view = client.get(
            f"/v1/assets/{asset_id}",
            headers={"Authorization": "Bearer token-a"},
        )
        assert owner_view.status_code == 200
        assert owner_view.json()["mime_type"] == "image/png"

        forbidden_view = client.get(
            f"/v1/assets/{asset_id}",
            headers={"Authorization": "Bearer token-b"},
        )
        assert forbidden_view.status_code == 403

        content_response = client.get(
            f"/v1/assets/{asset_id}/content",
            headers={"Authorization": "Bearer token-admin"},
        )
        assert content_response.status_code == 200
        assert content_response.headers["content-type"].startswith("image/png")


def test_upload_rejects_unsupported_type(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="alice|token-a|user",
    ) as client:
        response = client.post(
            "/v1/uploads",
            headers={"Authorization": "Bearer token-a"},
            files={"file": ("notes.txt", b"hello", "text/plain")},
            data={"temporary": "true"},
        )

        assert response.status_code == 415
        assert response.json()["error"]["code"] == "asset_type_not_supported"


def test_upload_accepts_pptx(client_factory):
    with client_factory(
        GEMINI_SERVICE_REQUIRE_AUTH="true",
        GEMINI_SERVICE_API_TOKENS="alice|token-a|user",
    ) as client:
        response = client.post(
            "/v1/uploads",
            headers={"Authorization": "Bearer token-a"},
            files={
                "file": (
                    "slides.pptx",
                    build_minimal_pptx(),
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
            },
            data={"temporary": "true"},
        )

        assert response.status_code == 200
        assert response.json()["asset"]["mime_type"] == "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def test_local_asset_storage_rejects_path_escape(tmp_path):
    repo = ChatRepository(f"sqlite+aiosqlite:///{(tmp_path / 'service.db').as_posix()}")
    settings = Settings(
        require_auth=False,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'service.db').as_posix()}",
        accounts_config_path=str(tmp_path / "accounts.json"),
        asset_root_path=str(tmp_path / "assets"),
    )
    (tmp_path / "accounts.json").write_text('{"accounts":[]}', encoding="utf-8")
    storage = LocalAssetStorage(str(tmp_path / "assets"))
    service = AssetService(settings=settings, repository=repo, storage=storage)

    async def scenario():
        await repo.start()
        asset = await repo.create_asset(
            owner_subject="alice",
            filename="escape.png",
            mime_type="image/png",
            size_bytes=len(PNG_BYTES),
            sha256="abc",
            status="available",
            storage_backend="local",
            storage_uri="../escape.png",
        )
        try:
            service.resolve_asset_path(asset)
        finally:
            await repo.close()

    with pytest.raises(ServiceError) as exc:
        asyncio.run(scenario())
    assert exc.value.code == "asset_path_invalid"
