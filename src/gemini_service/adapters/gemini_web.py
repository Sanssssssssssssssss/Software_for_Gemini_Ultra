from __future__ import annotations

import asyncio

from gemini_webapi import GeminiClient
from gemini_webapi.constants import Model
from gemini_webapi.exceptions import APIError, AuthError, TemporarilyBlocked, TimeoutError

from ..core.errors import ServiceError
from ..schemas.accounts import AccountConfig
from .base import AccountProbeResult, MessageChunk, MessageResult


class GeminiWebAccountAdapter:
    def __init__(self, config: AccountConfig):
        self.config = config
        self.client = GeminiClient(
            secure_1psid=config.secure_1psid,
            secure_1psidts=config.secure_1psidts,
            proxy=config.proxy,
            verify=config.verify_ssl,
        )

    async def probe(self) -> AccountProbeResult:
        try:
            return await asyncio.wait_for(
                self._probe_impl(),
                timeout=self.config.request_timeout_seconds,
            )
        except ServiceError:
            raise
        except asyncio.TimeoutError as exc:
            raise self._provider_error("provider_timeout", "Provider probe timed out.", exc, 504) from exc
        except AuthError as exc:
            raise self._provider_error("provider_reauth_required", "Provider authentication has expired.", exc) from exc
        except TemporarilyBlocked as exc:
            raise self._provider_error("provider_blocked", "Provider temporarily blocked this account.", exc) from exc
        except TimeoutError as exc:
            raise self._provider_error("provider_timeout", "Provider probe timed out.", exc, 504) from exc
        except APIError as exc:
            raise self._provider_error("provider_unavailable", "Provider probe failed.", exc) from exc
        except Exception as exc:
            raise self._provider_error("provider_unavailable", "Unexpected provider probe failure.", exc, 502) from exc

    async def _probe_impl(self) -> AccountProbeResult:
        await self.client.init(
            timeout=self.config.request_timeout_seconds,
            auto_close=False,
            auto_refresh=True,
            refresh_interval=max(60, self.config.cooldown_seconds),
            verbose=False,
        )
        models = [model.model_name for model in (self.client.list_models() or []) if model.is_available]
        return AccountProbeResult(
            account_status=self.client.account_status.name,
            account_status_code=int(self.client.account_status),
            status_description=self.client.account_status.description,
            models=models,
        )

    async def send_message(
        self,
        prompt: str,
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ) -> MessageResult:
        try:
            return await asyncio.wait_for(
                self._send_message_impl(
                    prompt=prompt,
                    chat_metadata=chat_metadata,
                    model=model,
                    gem=gem,
                    temporary=temporary,
                ),
                timeout=self.config.request_timeout_seconds,
            )
        except ServiceError:
            raise
        except asyncio.TimeoutError as exc:
            raise self._provider_error("provider_timeout", "Provider send_message timed out.", exc, 504) from exc
        except AuthError as exc:
            raise self._provider_error("provider_reauth_required", "Provider authentication has expired.", exc) from exc
        except TemporarilyBlocked as exc:
            raise self._provider_error("provider_blocked", "Provider temporarily blocked this account.", exc) from exc
        except TimeoutError as exc:
            raise self._provider_error("provider_timeout", "Provider send_message timed out.", exc, 504) from exc
        except APIError as exc:
            raise self._provider_error("provider_unavailable", "Provider send_message failed.", exc) from exc
        except Exception as exc:
            raise self._provider_error("provider_unavailable", "Unexpected provider send_message failure.", exc, 502) from exc

    async def _send_message_impl(
        self,
        prompt: str,
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ) -> MessageResult:
        chat = self.client.start_chat(
            metadata=chat_metadata if any(chat_metadata or []) else None,
            model=model or Model.UNSPECIFIED,
            gem=gem,
        )
        output = await chat.send_message(prompt=prompt, temporary=temporary)
        return MessageResult(
            text=output.text,
            metadata=list(output.metadata[:3]),
            thoughts=output.thoughts or "",
        )

    async def stream_message(
        self,
        prompt: str,
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ):
        try:
            async with asyncio.timeout(self.config.request_timeout_seconds):
                async for chunk in self._stream_message_impl(
                    prompt=prompt,
                    chat_metadata=chat_metadata,
                    model=model,
                    gem=gem,
                    temporary=temporary,
                ):
                    yield chunk
        except ServiceError:
            raise
        except asyncio.TimeoutError as exc:
            raise self._provider_error("provider_timeout", "Provider stream_message timed out.", exc, 504) from exc
        except AuthError as exc:
            raise self._provider_error("provider_reauth_required", "Provider authentication has expired.", exc) from exc
        except TemporarilyBlocked as exc:
            raise self._provider_error("provider_blocked", "Provider temporarily blocked this account.", exc) from exc
        except TimeoutError as exc:
            raise self._provider_error("provider_timeout", "Provider stream_message timed out.", exc, 504) from exc
        except APIError as exc:
            raise self._provider_error("provider_unavailable", "Provider stream_message failed.", exc) from exc
        except Exception as exc:
            raise self._provider_error("provider_unavailable", "Unexpected provider stream_message failure.", exc, 502) from exc

    async def _stream_message_impl(
        self,
        prompt: str,
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ):
        chat = self.client.start_chat(
            metadata=chat_metadata if any(chat_metadata or []) else None,
            model=model or Model.UNSPECIFIED,
            gem=gem,
        )
        async for output in chat.send_message_stream(prompt=prompt, temporary=temporary):
            yield MessageChunk(
                text_delta=output.text_delta,
                text=output.text,
                metadata=list(output.metadata[:3]),
            )

    async def close(self) -> None:
        await self.client.close()

    def _provider_error(
        self,
        code: str,
        message: str,
        exc: Exception,
        status_code: int = 503,
    ) -> ServiceError:
        return ServiceError(
            status_code=status_code,
            code=code,
            message=message,
            details={
                "account_id": self.config.account_id,
                "provider": "gemini_web",
                "error_class": exc.__class__.__name__,
                "error_message": str(exc),
            },
        )
