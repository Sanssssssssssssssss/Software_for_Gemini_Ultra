from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from functools import partial

from ..core.browser_cookie_sync import (
    BrowserLoginSession,
    CookieBundle,
    browser_debug_endpoint_available,
    cookie_bundle_hash,
    extract_cookie_bundle_from_browser_session,
    focus_browser_login_session,
    is_browser_login_session_alive,
    launch_browser_login_session,
    normalize_profile_dir,
    resolve_browser_path,
    terminate_browser_login_session,
)
from ..core.config import Settings
from ..core.errors import ServiceError
from ..schemas.accounts import AccountConfig


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_repo_path(path_value: str) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return (Path(__file__).resolve().parents[3] / path).resolve()


@dataclass(slots=True)
class BrowserSessionRegistryEntry:
    account_id: str
    profile_dir: str
    browser: str
    browser_path: str
    debug_port: int
    pid: int | None = None
    state: str = "offline"
    launched_by_service: bool = False
    auto_refresh_enabled: bool = True
    last_seen_at: str | None = None
    last_sync_at: str | None = None
    last_validated_at: str | None = None
    last_good_cookie_at: str | None = None
    last_cookie_hash: str | None = None
    last_provider_status: str | None = None
    last_runtime_state: str | None = None
    last_error: str | None = None


class PersistentBrowserManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state_path = _resolve_repo_path(settings.browser_state_path)
        self.profile_root = _resolve_repo_path(settings.browser_profile_root)
        self._registry: dict[str, BrowserSessionRegistryEntry] = {}
        self._sessions: dict[str, BrowserLoginSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    async def start(self) -> None:
        self.profile_root.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._registry = self._load_registry()

    async def close(self) -> None:
        if not self.settings.browser_keepalive_on_shutdown:
            for account_id in list(self._sessions):
                await self.stop_session(account_id)
        await self._save_registry()

    def get_entry(self, account_id: str) -> BrowserSessionRegistryEntry | None:
        return self._registry.get(account_id)

    def list_entries(self) -> list[BrowserSessionRegistryEntry]:
        return list(self._registry.values())

    async def ensure_session(self, account: AccountConfig) -> BrowserLoginSession:
        async with self._lock(account.account_id):
            attached = await self.attach_existing_session(account)
            if attached is not None:
                return attached
            return await self.launch_session(account)

    async def attach_existing_session(self, account: AccountConfig) -> BrowserLoginSession | None:
        entry = self._registry.get(account.account_id)
        session = self._sessions.get(account.account_id)
        if session is not None and is_browser_login_session_alive(session):
            await self._update_entry(
                account.account_id,
                state="online",
                pid=session.pid,
                last_seen_at=_now_iso(),
                last_error=None,
            )
            return session

        if not self.settings.browser_reuse_existing or entry is None:
            return None

        if not browser_debug_endpoint_available(entry.debug_port, timeout_seconds=2):
            await self._update_entry(account.account_id, state="stale", last_error="debug endpoint unavailable")
            return None

        session = BrowserLoginSession(
            browser=entry.browser,
            browser_path=Path(entry.browser_path),
            profile_dir=Path(entry.profile_dir),
            port=entry.debug_port,
            process=None,
            start_url=self.settings.cookie_autosync_start_url,
            pid=entry.pid,
            launched_by_service=entry.launched_by_service,
        )
        self._sessions[account.account_id] = session
        await self._update_entry(
            account.account_id,
            state="online",
            pid=entry.pid,
            last_seen_at=_now_iso(),
            last_error=None,
        )
        return session

    async def launch_session(self, account: AccountConfig) -> BrowserLoginSession:
        browser_name = (account.cookie_source_browser or self.settings.cookie_autosync_browser).lower()
        browser_path = resolve_browser_path(browser_name, account.cookie_source_browser_path)
        if browser_path is None:
            raise ServiceError(
                status_code=400,
                code="browser_not_found",
                message=f"Could not find a usable {browser_name} browser executable.",
            )
        profile_dir = self.resolve_profile_dir(account)
        debug_port = self._reserve_port(account.account_id)
        session = launch_browser_login_session(
            browser=browser_name,
            profile_dir=profile_dir,
            browser_path=browser_path,
            start_url=self.settings.cookie_autosync_start_url,
            debug_port=debug_port,
        )
        self._sessions[account.account_id] = session
        await self._update_entry(
            account.account_id,
            profile_dir=str(profile_dir),
            browser=browser_name,
            browser_path=str(browser_path),
            debug_port=session.port,
            pid=session.pid,
            state="online",
            launched_by_service=True,
            auto_refresh_enabled=True,
            last_seen_at=_now_iso(),
            last_error=None,
        )
        return session

    async def focus_session(self, account: AccountConfig) -> BrowserSessionRegistryEntry:
        session = await self.ensure_session(account)
        focus_browser_login_session(session, start_url=self.settings.cookie_autosync_start_url)
        await self._update_entry(account.account_id, state="focused", last_seen_at=_now_iso(), last_error=None)
        return self._registry[account.account_id]

    async def stop_session(self, account_id: str) -> BrowserSessionRegistryEntry | None:
        session = self._sessions.pop(account_id, None)
        if session is not None:
            terminate_browser_login_session(session)
        entry = self._registry.get(account_id)
        if entry is None:
            return None
        await self._update_entry(account_id, state="offline", pid=None, last_error=None)
        return self._registry.get(account_id)

    async def collect_cookie_bundle(self, account: AccountConfig) -> CookieBundle:
        session = await self.ensure_session(account)
        bundle = await asyncio.to_thread(
            partial(
                extract_cookie_bundle_from_browser_session,
                session,
                timeout_seconds=self.settings.cookie_autosync_timeout_seconds,
            ),
        )
        await self._update_entry(
            account.account_id,
            state="collecting_cookies",
            pid=session.pid,
            last_seen_at=_now_iso(),
            last_sync_at=_now_iso(),
            last_error=None,
        )
        return bundle

    async def health(self, account: AccountConfig) -> dict[str, object]:
        entry = self._registry.get(account.account_id)
        online = False
        if entry is not None:
            online = browser_debug_endpoint_available(entry.debug_port, timeout_seconds=2)
        return {
            "account_id": account.account_id,
            "browser_online": online,
            "profile_dir": str(self.resolve_profile_dir(account)),
            "debug_port": entry.debug_port if entry else None,
            "state": entry.state if entry else "offline",
            "auto_refresh_enabled": entry.auto_refresh_enabled if entry else True,
            "last_sync_at": entry.last_sync_at if entry else None,
            "last_validated_at": entry.last_validated_at if entry else None,
            "last_good_cookie_at": entry.last_good_cookie_at if entry else None,
            "last_error": entry.last_error if entry else None,
        }

    async def set_auto_refresh(self, account_id: str, enabled: bool) -> BrowserSessionRegistryEntry:
        await self._update_entry(account_id, auto_refresh_enabled=enabled)
        return self._registry[account_id]

    async def record_cookie_sync_result(
        self,
        account_id: str,
        *,
        bundle: CookieBundle,
        provider_status: str | None,
        runtime_state: str | None,
        success: bool,
        error: str | None = None,
    ) -> None:
        payload = {
            "last_cookie_hash": cookie_bundle_hash(bundle),
            "last_sync_at": _now_iso(),
            "last_validated_at": _now_iso(),
            "last_provider_status": provider_status,
            "last_runtime_state": runtime_state,
            "last_error": error,
            "state": "online" if success else "error",
        }
        if success:
            payload["last_good_cookie_at"] = _now_iso()
        await self._update_entry(account_id, **payload)

    async def record_error(self, account_id: str, error: str, *, state: str = "error") -> None:
        await self._update_entry(
            account_id,
            state=state,
            last_error=error,
            last_validated_at=_now_iso(),
        )

    def resolve_profile_dir(self, account: AccountConfig) -> Path:
        if account.cookie_source_profile_dir:
            return normalize_profile_dir(Path(self.settings.accounts_config_path), account.cookie_source_profile_dir)
        return (self.profile_root / account.account_id).resolve()

    def _reserve_port(self, account_id: str) -> int:
        entry = self._registry.get(account_id)
        if entry is not None and entry.debug_port and not self._port_in_use_elsewhere(entry.debug_port, account_id):
            return entry.debug_port

        base = self.settings.browser_base_debug_port
        for offset in range(0, 500):
            port = base + offset
            if self._port_in_use_elsewhere(port, account_id):
                continue
            if browser_debug_endpoint_available(port, timeout_seconds=1):
                continue
            return port
        raise RuntimeError("Could not reserve a browser debugging port for the managed session.")

    def _port_in_use_elsewhere(self, port: int, account_id: str) -> bool:
        for existing_account_id, entry in self._registry.items():
            if existing_account_id == account_id:
                continue
            if entry.debug_port == port:
                return True
        return False

    async def _update_entry(self, account_id: str, **updates: object) -> None:
        async with self._registry_lock:
            entry = self._registry.get(account_id)
            if entry is None:
                required = {
                    "account_id": account_id,
                    "profile_dir": str(self.profile_root / account_id),
                    "browser": self.settings.cookie_autosync_browser,
                    "browser_path": "",
                    "debug_port": int(updates.get("debug_port") or 0),
                }
                entry = BrowserSessionRegistryEntry(**required)
            for key, value in updates.items():
                if hasattr(entry, key):
                    setattr(entry, key, value)
            self._registry[account_id] = entry
            await self._save_registry()

    async def _save_registry(self) -> None:
        payload = {
            "sessions": [asdict(entry) for entry in sorted(self._registry.values(), key=lambda item: item.account_id)]
        }
        temp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(self.state_path)

    def _load_registry(self) -> dict[str, BrowserSessionRegistryEntry]:
        if not self.state_path.exists():
            return {}
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        sessions = payload.get("sessions", [])
        registry: dict[str, BrowserSessionRegistryEntry] = {}
        for item in sessions:
            try:
                entry = BrowserSessionRegistryEntry(**item)
            except Exception:
                continue
            registry[entry.account_id] = entry
        return registry

    def _lock(self, account_id: str) -> asyncio.Lock:
        if account_id not in self._locks:
            self._locks[account_id] = asyncio.Lock()
        return self._locks[account_id]
