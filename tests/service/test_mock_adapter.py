from __future__ import annotations

import asyncio

import pytest
from gemini_webapi.exceptions import APIError, AuthError, TemporarilyBlocked, TimeoutError

from gemini_service.adapters.base import TextPromptPart
from gemini_service.adapters.mock import MockAccountAdapter
from gemini_service.schemas.accounts import AccountConfig


def _config(**kwargs) -> AccountConfig:
    return AccountConfig(
        account_id="mock-1",
        provider_backend="mock",
        secure_1psid="mock-cookie",
        request_timeout_seconds=10,
        **kwargs,
    )


def test_mock_adapter_healthy_probe_and_send():
    adapter = MockAccountAdapter(_config(mock_behavior="healthy"))

    probe = asyncio.run(adapter.probe())
    result = asyncio.run(adapter.send_message(parts=[TextPromptPart(type="text", text="hello")]))

    assert probe.account_status == "AVAILABLE"
    assert result.text.startswith("[mock-1|healthy]")


def test_mock_adapter_reauth_probe():
    adapter = MockAccountAdapter(_config(mock_behavior="reauth"))

    with pytest.raises(AuthError):
        asyncio.run(adapter.probe())


def test_mock_adapter_blocked_probe():
    adapter = MockAccountAdapter(_config(mock_behavior="blocked"))

    with pytest.raises(TemporarilyBlocked):
        asyncio.run(adapter.probe())


def test_mock_adapter_timeout_send():
    adapter = MockAccountAdapter(_config(mock_behavior="send_timeout"))

    with pytest.raises(TimeoutError):
        asyncio.run(adapter.send_message(parts=[TextPromptPart(type="text", text="hello")]))


def test_mock_adapter_flaky_send():
    adapter = MockAccountAdapter(_config(mock_behavior="flaky"))

    asyncio.run(adapter.send_message(parts=[TextPromptPart(type="text", text="one")]))
    asyncio.run(adapter.send_message(parts=[TextPromptPart(type="text", text="two")]))
    with pytest.raises(APIError):
        asyncio.run(adapter.send_message(parts=[TextPromptPart(type="text", text="three")]))
