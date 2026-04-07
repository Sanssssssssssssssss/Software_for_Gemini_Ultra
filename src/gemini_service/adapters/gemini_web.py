from __future__ import annotations

import asyncio
import mimetypes
import tempfile
from pathlib import Path

from gemini_webapi import GeminiClient
from gemini_webapi.constants import Model
from gemini_webapi.exceptions import APIError, AuthError, TemporarilyBlocked, TimeoutError

from ..core.errors import ServiceError
from ..schemas.accounts import AccountConfig
from .base import (
    AccountProbeResult,
    AssetPromptPart,
    GeneratedMediaResult,
    MessageChunk,
    MessageResult,
    PromptPart,
)


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
        parts: list[PromptPart],
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ) -> MessageResult:
        try:
            return await asyncio.wait_for(
                self._send_message_impl(
                    parts=parts,
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
        parts: list[PromptPart],
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ) -> MessageResult:
        prompt, files = self._split_parts(parts)
        chat = self.client.start_chat(
            metadata=chat_metadata if any(chat_metadata or []) else None,
            model=model or Model.UNSPECIFIED,
            gem=gem,
        )
        output = await chat.send_message(prompt=prompt, files=files or None, temporary=temporary)
        return MessageResult(
            text=output.text,
            metadata=list(output.metadata[:3]),
            thoughts=output.thoughts or "",
            generated_media=await self._extract_generated_media(output),
        )

    async def stream_message(
        self,
        parts: list[PromptPart],
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ):
        try:
            async with asyncio.timeout(self.config.request_timeout_seconds):
                async for chunk in self._stream_message_impl(
                    parts=parts,
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
        parts: list[PromptPart],
        chat_metadata: list[str] | None = None,
        model: str | None = None,
        gem: str | None = None,
        temporary: bool = False,
    ):
        prompt, files = self._split_parts(parts)
        chat = self.client.start_chat(
            metadata=chat_metadata if any(chat_metadata or []) else None,
            model=model or Model.UNSPECIFIED,
            gem=gem,
        )
        async for output in chat.send_message_stream(prompt=prompt, files=files or None, temporary=temporary):
            yield MessageChunk(
                text_delta=output.text_delta,
                text=output.text,
                metadata=list(output.metadata[:3]),
                generated_media=await self._extract_generated_media(output),
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

    def _split_parts(self, parts: list[PromptPart]) -> tuple[str, list[str | Path]]:
        text_parts: list[str] = []
        files: list[str | Path] = []
        for part in parts:
            if isinstance(part, AssetPromptPart):
                files.append(part.absolute_path)
            else:
                text_parts.append(part.text)
        prompt = "\n\n".join(item.strip() for item in text_parts if item.strip()).strip()
        if not prompt:
            prompt = "Please analyze the attached files."
        return prompt, files

    async def _extract_generated_media(self, output) -> list[GeneratedMediaResult]:
        media: list[GeneratedMediaResult] = []
        for index, image in enumerate(getattr(output, "images", []) or [], start=1):
            content = await self._download_generated_image_bytes(image)
            guessed_mime = self._guess_mime_type(image.url)
            media.append(
                GeneratedMediaResult(
                    media_type="image",
                    filename=f"generated-image-{index}{self._guess_extension(guessed_mime)}",
                    mime_type=guessed_mime,
                    content=content,
                    provider_ref=image.url,
                )
            )
        return media

    async def _download_generated_image_bytes(self, image) -> bytes:
        with tempfile.TemporaryDirectory() as temp_dir:
            saved_path = await image.save(path=temp_dir, verbose=False)
            return Path(saved_path).read_bytes()

    def _guess_mime_type(self, url: str) -> str:
        guessed = mimetypes.guess_type(url)[0]
        return guessed or "image/png"

    def _guess_extension(self, mime_type: str) -> str:
        return mimetypes.guess_extension(mime_type) or ".png"
