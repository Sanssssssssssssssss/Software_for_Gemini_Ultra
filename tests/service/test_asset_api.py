from __future__ import annotations


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDAT\x08\x99c``\x00\x00\x00\x04\x00\x01"
    b"\x0b\xe7\x02\x9d\x00\x00\x00\x00IEND\xaeB`\x82"
)


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
