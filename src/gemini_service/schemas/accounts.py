from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field


class AccountConfig(BaseModel):
    account_id: str
    enabled: bool = True
    provider_backend: str = "gemini_web"
    secure_1psid: str
    secure_1psidts: str | None = None
    cookie_source_browser: str | None = None
    cookie_source_browser_path: str | None = None
    cookie_source_profile_dir: str | None = None
    proxy: str | None = None
    max_concurrency: int = Field(default=1, ge=1)
    cooldown_seconds: int = Field(default=60, ge=5)
    request_timeout_seconds: int = Field(default=450, ge=10)
    verify_ssl: bool = True
    mock_behavior: str = "healthy"
    mock_delay_ms: int = Field(default=0, ge=0)
    mock_models: list[str] = Field(default_factory=lambda: ["gemini-3-pro"])
    tags: list[str] = Field(default_factory=list)
    last_recovery_at: str | None = None
    last_recovery_source: str | None = None


class AccountInventory(BaseModel):
    accounts: list[AccountConfig] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AccountInventoryIssue:
    code: str
    detail: str
    account_id: str | None = None


def sanitize_inventory_payload(payload: dict) -> tuple[dict, list[AccountInventoryIssue], bool]:
    raw_accounts = payload.get("accounts", [])
    if not isinstance(raw_accounts, list):
        raw_accounts = []

    sanitized_accounts: list[dict] = []
    issues: list[AccountInventoryIssue] = []
    seen_ids: set[str] = set()
    dirty = False

    for index, raw_account in enumerate(raw_accounts):
        if not isinstance(raw_account, dict):
            issues.append(
                AccountInventoryIssue(
                    code="invalid_account_entry",
                    detail=f"Ignored non-object account entry at index {index}.",
                )
            )
            dirty = True
            continue

        account_id = str(raw_account.get("account_id") or "").strip()
        if not account_id:
            issues.append(
                AccountInventoryIssue(
                    code="empty_account_id",
                    detail=f"Ignored account entry at index {index} because account_id is blank.",
                )
            )
            dirty = True
            continue

        if account_id in seen_ids:
            issues.append(
                AccountInventoryIssue(
                    code="duplicate_account_id",
                    detail=f"Ignored duplicate account entry for {account_id}.",
                    account_id=account_id,
                )
            )
            dirty = True
            continue

        seen_ids.add(account_id)
        sanitized_accounts.append({**raw_account, "account_id": account_id})

    sanitized = {"accounts": sanitized_accounts}
    return sanitized, issues, dirty


def load_account_inventory(path: Path, *, save_clean: bool = False) -> tuple[AccountInventory, list[AccountInventoryIssue], bool]:
    if not path.exists():
        return AccountInventory(accounts=[]), [], False

    payload = json.loads(path.read_text(encoding="utf-8"))
    sanitized, issues, dirty = sanitize_inventory_payload(payload)
    inventory = AccountInventory.model_validate(sanitized)
    if dirty and save_clean:
        save_account_inventory(path, inventory.model_dump(mode="json"))
    return inventory, issues, dirty


def save_account_inventory(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)
