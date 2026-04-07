from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from gemini_webapi.exceptions import APIError, AuthError, TemporarilyBlocked

from gemini_service.adapters.base import AssetPromptPart, TextPromptPart
from gemini_service.adapters.gemini_web import GeminiWebAccountAdapter
from gemini_service.core.errors import ServiceError
from gemini_service.schemas.accounts import AccountConfig


def _config() -> AccountConfig:
    return AccountConfig(account_id="acc-1", secure_1psid="cookie", request_timeout_seconds=10)


def test_adapter_maps_auth_error(monkeypatch):
    adapter = GeminiWebAccountAdapter(_config())

    async def raise_auth():
        raise AuthError("expired")

    monkeypatch.setattr(adapter, "_probe_impl", raise_auth)

    with pytest.raises(ServiceError) as exc:
        asyncio.run(adapter.probe())
    assert exc.value.code == "provider_reauth_required"
    assert exc.value.details["error_class"] == "AuthError"


def test_adapter_maps_temporarily_blocked(monkeypatch):
    adapter = GeminiWebAccountAdapter(_config())

    async def raise_block(**kwargs):
        raise TemporarilyBlocked("blocked")

    monkeypatch.setattr(adapter, "_send_message_impl", raise_block)

    with pytest.raises(ServiceError) as exc:
        asyncio.run(adapter.send_message(parts=[TextPromptPart(type="text", text="hello")]))
    assert exc.value.code == "provider_blocked"
    assert exc.value.details["error_class"] == "TemporarilyBlocked"


def test_adapter_maps_timeout(monkeypatch):
    adapter = GeminiWebAccountAdapter(_config())

    async def raise_timeout(**kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(adapter, "_send_message_impl", raise_timeout)

    with pytest.raises(ServiceError) as exc:
        asyncio.run(adapter.send_message(parts=[TextPromptPart(type="text", text="hello")]))
    assert exc.value.code == "provider_timeout"


def test_adapter_maps_api_error(monkeypatch):
    adapter = GeminiWebAccountAdapter(_config())

    async def raise_api(**kwargs):
        raise APIError("bad upstream")

    monkeypatch.setattr(adapter, "_send_message_impl", raise_api)

    with pytest.raises(ServiceError) as exc:
        asyncio.run(adapter.send_message(parts=[TextPromptPart(type="text", text="hello")]))
    assert exc.value.code == "provider_unavailable"
    assert exc.value.details["error_class"] == "APIError"


def test_adapter_splits_text_and_file_parts(tmp_path):
    adapter = GeminiWebAccountAdapter(_config())
    asset_path = tmp_path / "sample.png"
    asset_path.write_bytes(b"png")

    prompt, files = adapter._split_parts(
        [
            TextPromptPart(type="text", text="hello"),
            AssetPromptPart(
                type="asset",
                asset_id="asset-1",
                filename="sample.png",
                mime_type="image/png",
                absolute_path=Path(asset_path),
            ),
            TextPromptPart(type="text", text="world"),
        ]
    )

    assert prompt == "hello\n\nworld"
    assert files == [Path(asset_path)]
