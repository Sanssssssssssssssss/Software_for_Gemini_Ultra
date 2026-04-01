from __future__ import annotations

from gemini_webapi import GeminiClient

from ..schemas.accounts import AccountConfig
from .base import AccountProbeResult


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

    async def close(self) -> None:
        await self.client.close()
