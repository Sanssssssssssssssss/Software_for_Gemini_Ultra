from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..core.config import Settings
from ..core.errors import ServiceError
from ..core.security import AuthContext
from ..core.telemetry import TelemetryService
from ..db.repository import ChatRepository
from ..schemas.accounts import AccountConfig, AccountInventory, load_account_inventory, save_account_inventory
from ..schemas.admin import (
    AdminAccountUpsertRequest,
    AdminDashboardResponse,
    AdminManagedAccount,
    AdminReauthJobResponse,
)
from ..schemas.common import AccountSummary
from .account_recovery_service import AccountRecoveryResult, AccountRecoveryService
from .account_pool import AccountPool, AccountRuntimeState
from .asset_service import AssetService
from .chat_service import ChatService
from .cookie_refresh_daemon import CookieRefreshDaemon
from .persistent_browser_manager import PersistentBrowserManager


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ReauthJob:
    job_id: str
    account_id: str
    status: str
    detail: str
    browser: str | None
    profile_dir: str | None
    launch_url: str
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    action_required: str | None = None
    result: dict[str, object] = field(default_factory=dict)
    recovery_source: str | None = None
    task: asyncio.Task[None] | None = None


class AdminConsoleService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: ChatRepository,
        pool: AccountPool,
        chat_service: ChatService,
        asset_service: AssetService,
        recovery_service: AccountRecoveryService,
        browser_manager: PersistentBrowserManager | None = None,
        refresh_daemon: CookieRefreshDaemon | None = None,
        telemetry: TelemetryService | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.pool = pool
        self.chat_service = chat_service
        self.asset_service = asset_service
        self.recovery_service = recovery_service
        self.browser_manager = browser_manager
        self.refresh_daemon = refresh_daemon
        self.telemetry = telemetry
        self._jobs: dict[str, ReauthJob] = {}
        self._jobs_lock = asyncio.Lock()

    @staticmethod
    def _summary_is_recovered(summary: AccountSummary | None) -> bool:
        return bool(
            summary
            and summary.account_status == "AVAILABLE"
            and summary.state in {"ready", "degraded"}
        )

    def _runtime_summary(self, account_id: str) -> AccountSummary | None:
        runtime = self.pool.get_runtime(account_id)
        return runtime.summary() if runtime is not None else None

    def _complete_job_from_summary(
        self,
        job: ReauthJob,
        *,
        summary: AccountSummary,
        source: str,
        detail: str,
    ) -> AdminReauthJobResponse:
        if job.task is not None and job.task is not asyncio.current_task():
            job.task.cancel()
        job.task = None
        job.status = "completed"
        job.detail = detail
        job.action_required = None
        job.updated_at = _now_iso()
        job.result = {
            "source": source,
            "recovery_source": "runtime",
            "cookie_count": None,
            "provider_status": summary.account_status,
            "runtime_state": summary.state,
            "account_models": summary.models,
            "updated": False,
            "code": "runtime_already_ready",
        }
        return self._job_response(job)

    async def close(self) -> None:
        async with self._jobs_lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.task is not None:
                job.task.cancel()

    async def build_dashboard(self, auth: AuthContext) -> AdminDashboardResponse:
        sessions = await self.chat_service.list_sessions(auth=auth, limit=50)
        accounts = await self.pool.list_account_summaries(force_refresh=True)
        runtime_by_id = {item.account_id: item for item in accounts}
        inventory = self._load_inventory()
        inventory_accounts = []
        for account in inventory.accounts:
            browser_health = await self.browser_manager.health(account) if self.browser_manager is not None else None
            inventory_accounts.append(
                self._managed_account_response(
                    account,
                    runtime_by_id.get(account.account_id),
                    browser_health=browser_health,
                )
            )
        assets = [
            self.chat_service._asset_response(asset)  # noqa: SLF001 - keep admin read model aligned
            for asset in await self.repository.list_recent_assets(limit=20)
        ]
        if self.telemetry is not None:
            self.telemetry.update_runtime(
                ready_accounts=self.pool.ready_account_count,
                total_accounts=self.pool.inventory_count,
                sessions=len(sessions),
                messages=await self.repository.count_messages(),
                batches=await self.repository.count_batches(),
                assets=await self.repository.count_assets(),
            )
            self.telemetry.update_account_pool(accounts)
            self.telemetry.update_asset_runtime(await self.repository.count_assets_by_status())

        telemetry_payload = {
            "total_requests": self.telemetry.total_requests if self.telemetry else 0,
            "total_errors": self.telemetry.total_errors if self.telemetry else 0,
            "active_requests": self.telemetry.active_requests if self.telemetry else 0,
            "account_ready": self.telemetry.account_ready if self.telemetry else self.pool.ready_account_count,
            "account_total": self.telemetry.account_total if self.telemetry else self.pool.inventory_count,
            "chat_sessions": self.telemetry.chat_sessions if self.telemetry else len(sessions),
            "chat_messages": self.telemetry.chat_messages if self.telemetry else 0,
            "chat_batches": self.telemetry.chat_batches if self.telemetry else 0,
            "chat_assets": self.telemetry.chat_assets if self.telemetry else len(assets),
            "batch_workers_active": self.telemetry.batch_workers_active if self.telemetry else 0,
            "session_failovers_total": self.telemetry.session_failovers_total if self.telemetry else 0,
            "account_state_counts": dict(self.telemetry.account_state_counts) if self.telemetry else {},
            "account_queue_depth": dict(self.telemetry.account_queue_depth) if self.telemetry else {},
            "account_in_flight": dict(self.telemetry.account_in_flight) if self.telemetry else {},
            "asset_status_counts": dict(self.telemetry.asset_status_counts) if self.telemetry else {},
            "asset_cleanup_runs_total": self.telemetry.asset_cleanup_runs_total if self.telemetry else 0,
            "asset_expired_total": self.telemetry.asset_expired_total if self.telemetry else 0,
            "asset_deleted_total": self.telemetry.asset_deleted_total if self.telemetry else 0,
        }
        health = {
            "ready_accounts": self.pool.ready_account_count,
            "inventory_count": self.pool.inventory_count,
            "queue_depth": self.pool.total_queue_depth,
            "reauth_required": sum(1 for item in accounts if item.state == "reauth_required"),
            "blocked": sum(1 for item in accounts if item.state == "blocked"),
            "cooling_down": sum(1 for item in accounts if item.state == "cooling_down"),
            "unavailable": sum(1 for item in accounts if item.state == "unavailable"),
        }
        return AdminDashboardResponse(
            accounts=accounts,
            inventory_accounts=inventory_accounts,
            sessions=sessions,
            assets=assets,
            reauth_jobs=self.list_reauth_jobs(),
            telemetry=telemetry_payload,
            health=health,
        )

    async def upsert_account(self, request: AdminAccountUpsertRequest) -> AdminManagedAccount:
        inventory = self._load_inventory()
        account_id = request.account_id.strip()
        if not account_id:
            raise ServiceError(
                status_code=400,
                code="account_id_required",
                message="Account ID cannot be blank.",
            )
        profile_dir = request.cookie_source_profile_dir or None
        if self.settings.browser_manager_enabled and not profile_dir:
            profile_dir = f"{Path(self.settings.browser_profile_root).as_posix().rstrip('/')}/{account_id}"

        next_account = AccountConfig(
            account_id=account_id,
            enabled=request.enabled,
            provider_backend=request.provider_backend,
            secure_1psid=(request.secure_1psid or "").strip(),
            secure_1psidts=(request.secure_1psidts or None),
            cookie_source_browser=request.cookie_source_browser or None,
            cookie_source_browser_path=request.cookie_source_browser_path or None,
            cookie_source_profile_dir=profile_dir,
            proxy=request.proxy or None,
            max_concurrency=request.max_concurrency,
            cooldown_seconds=request.cooldown_seconds,
            request_timeout_seconds=request.request_timeout_seconds,
            verify_ssl=request.verify_ssl,
            tags=request.tags,
        )

        existing = {account.account_id: account for account in inventory.accounts}
        prior = existing.get(next_account.account_id)
        if prior is not None:
            if not next_account.secure_1psid:
                next_account.secure_1psid = prior.secure_1psid
            if next_account.secure_1psidts is None:
                next_account.secure_1psidts = prior.secure_1psidts
            next_account.last_recovery_at = prior.last_recovery_at
            next_account.last_recovery_source = prior.last_recovery_source
            inventory.accounts = [
                next_account if account.account_id == next_account.account_id else account
                for account in inventory.accounts
            ]
        else:
            inventory.accounts.append(next_account)

        self._save_inventory(inventory)
        await self.pool.sync_inventory(force_refresh=True)
        runtime = self.pool.get_runtime(next_account.account_id)
        browser_health = await self.browser_manager.health(next_account) if self.browser_manager is not None else None
        return self._managed_account_response(next_account, runtime.summary() if runtime else None, browser_health=browser_health)

    async def delete_account(self, account_id: str) -> dict[str, object]:
        inventory = self._load_inventory()
        existing = next((account for account in inventory.accounts if account.account_id == account_id), None)
        if existing is None:
            raise ServiceError(
                status_code=404,
                code="account_not_found",
                message=f"Account {account_id} does not exist.",
            )

        runtime = self.pool.get_runtime(account_id)
        if runtime is not None and runtime.active_requests > 0:
            raise ServiceError(
                status_code=409,
                code="account_busy",
                message="This account is currently serving requests and cannot be deleted.",
                details={"account_id": account_id},
            )

        async with self._jobs_lock:
            jobs = [job for job in self._jobs.values() if job.account_id == account_id]
        for job in jobs:
            job.status = "cancelled"
            job.detail = "Account deleted by operator."
            job.updated_at = _now_iso()
            job.action_required = None

        inventory.accounts = [account for account in inventory.accounts if account.account_id != account_id]
        self._save_inventory(inventory)
        await self.pool.sync_inventory(force_refresh=True)
        return {"ok": True, "account_id": account_id}

    async def start_reauth_job(self, account_id: str) -> AdminReauthJobResponse:
        runtime = self.pool.get_runtime(account_id)
        inventory = self._load_inventory()
        account = next((item for item in inventory.accounts if item.account_id == account_id), None)
        if account is None:
            raise ServiceError(
                status_code=404,
                code="account_not_found",
                message=f"Account {account_id} does not exist.",
            )

        async with self._jobs_lock:
            for job in self._jobs.values():
                if job.account_id == account_id and job.status in {
                    "queued",
                    "launching_browser",
                    "awaiting_login",
                    "collecting_cookies",
                    "validating_provider",
                }:
                    return self._job_response(job)

        job = ReauthJob(
            job_id=str(uuid4()),
            account_id=account_id,
            status="queued",
            detail="Preparing account-specific Gemini recovery.",
            browser=(account.cookie_source_browser or self.settings.cookie_autosync_browser).lower(),
            profile_dir=account.cookie_source_profile_dir,
            launch_url=self.settings.cookie_autosync_start_url,
            result={
                "runtime_state": runtime.effective_state.value if runtime else None,
            },
        )
        runtime_summary = runtime.summary() if runtime is not None else None
        if self._summary_is_recovered(runtime_summary):
            response = self._complete_job_from_summary(
                job,
                summary=runtime_summary,
                source="initial_runtime",
                detail="Account is already routable; reauthentication is not required.",
            )
            async with self._jobs_lock:
                self._jobs[job.job_id] = job
            return response
        async with self._jobs_lock:
            self._jobs[job.job_id] = job
            job.task = asyncio.create_task(self._run_reauth_job(job.job_id))
        return self._job_response(job)

    async def complete_reauth_job(self, job_id: str) -> AdminReauthJobResponse:
        job = await self._require_job(job_id)
        if job.status == "completed":
            return self._job_response(job)
        runtime_summary = self._runtime_summary(job.account_id)
        if self._summary_is_recovered(runtime_summary):
            return self._complete_job_from_summary(
                job,
                summary=runtime_summary,
                source="manual_runtime",
                detail="Account is already routable; no additional reauthentication is required.",
            )
        result = await self.recovery_service.recover_account(
            job.account_id,
            allow_browser_launch=False,
            browser=job.browser,
        )
        return await self._apply_recovery_result(job, result=result, source="manual_sync")

    async def cancel_reauth_job(self, job_id: str) -> AdminReauthJobResponse:
        job = await self._require_job(job_id)
        if job.status in {"completed", "failed", "cancelled"}:
            return self._job_response(job)
        if job.task is not None:
            job.task.cancel()
            job.task = None
        job.status = "cancelled"
        job.detail = "Reauthentication was cancelled by the operator."
        job.updated_at = _now_iso()
        job.action_required = None
        return self._job_response(job)

    def list_reauth_jobs(self) -> list[AdminReauthJobResponse]:
        jobs = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
        return [self._job_response(job) for job in jobs[:20]]

    async def browser_action(self, account_id: str, action: str) -> dict[str, object]:
        account = next((item for item in self._load_inventory().accounts if item.account_id == account_id), None)
        if account is None:
            raise ServiceError(
                status_code=404,
                code="account_not_found",
                message=f"Account {account_id} does not exist.",
            )
        if self.browser_manager is None:
            raise ServiceError(
                status_code=409,
                code="browser_manager_disabled",
                message="Persistent browser management is disabled.",
            )

        if action == "start":
            entry = await self.browser_manager.focus_session(account)
            detail = "Managed browser launched or attached."
        elif action == "focus":
            entry = await self.browser_manager.focus_session(account)
            detail = "Managed browser focused on Gemini."
        elif action == "stop":
            entry = await self.browser_manager.stop_session(account_id)
            detail = "Managed browser session stopped."
        elif action == "sync-now":
            if self.refresh_daemon is not None:
                self.refresh_daemon.force_sync_now(account_id)
            result = await self.recovery_service.recover_account(account_id, allow_browser_launch=False)
            runtime_summary = self._runtime_summary(account_id)
            if self._summary_is_recovered(runtime_summary) and result.status != "completed":
                detail = "Account is already routable; no additional browser sync was required."
            else:
                detail = result.detail
            entry = self.browser_manager.get_entry(account_id)
        elif action == "pause-auto-refresh":
            entry = await self.browser_manager.set_auto_refresh(account_id, False)
            detail = "Automatic cookie refresh paused."
        elif action == "resume-auto-refresh":
            entry = await self.browser_manager.set_auto_refresh(account_id, True)
            detail = "Automatic cookie refresh resumed."
        else:
            raise ServiceError(
                status_code=404,
                code="browser_action_not_found",
                message=f"Unknown browser action: {action}",
            )

        return {
            "account_id": account_id,
            "action": action,
            "detail": detail,
            "browser_state": entry.state if entry is not None else "offline",
            "debug_port": entry.debug_port if entry is not None else None,
            "auto_refresh_enabled": entry.auto_refresh_enabled if entry is not None else False,
        }

    async def export_session_bytes(
        self,
        *,
        session_id: str,
        auth: AuthContext,
        export_format: str,
    ) -> tuple[str, bytes, str]:
        session = await self.chat_service.get_session(session_id, auth=auth)
        history = await self.chat_service.get_history(session_id, auth=auth)
        title = session.title or session.session_id
        safe_title = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in title)[:80] or session.session_id
        if export_format == "markdown":
            content = self._render_session_markdown(session=session, history=history)
            return f"{safe_title}.md", content.encode("utf-8"), "text/markdown; charset=utf-8"
        if export_format != "json":
            raise ServiceError(
                status_code=400,
                code="export_format_invalid",
                message="Unsupported export format.",
                details={"supported_formats": ["json", "markdown"]},
            )
        payload = {"session": session.model_dump(mode="json"), "history": history.model_dump(mode="json")}
        return f"{safe_title}.json", json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "application/json"

    async def export_sessions_bytes(
        self,
        *,
        auth: AuthContext,
        export_format: str,
        owner_subject: str | None,
        account_id: str | None,
        limit: int,
    ) -> tuple[str, bytes, str]:
        sessions = await self.repository.list_sessions(limit=limit, owner_subject=owner_subject)
        if account_id:
            sessions = [session for session in sessions if session.account_id == account_id]

        exports = []
        for session in sessions:
            session_response = await self.chat_service.get_session(session.id, auth=auth)
            history = await self.chat_service.get_history(session.id, auth=auth)
            exports.append({"session": session_response.model_dump(mode="json"), "history": history.model_dump(mode="json")})

        filename = f"sessions-export-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        if export_format == "markdown":
            chunks = []
            for item in exports:
                chunks.append(self._render_session_markdown(session=item["session"], history=item["history"]))
            return f"{filename}.md", "\n\n---\n\n".join(chunks).encode("utf-8"), "text/markdown; charset=utf-8"
        if export_format != "json":
            raise ServiceError(
                status_code=400,
                code="export_format_invalid",
                message="Unsupported export format.",
                details={"supported_formats": ["json", "markdown"]},
            )
        return f"{filename}.json", json.dumps({"items": exports}, ensure_ascii=False, indent=2).encode("utf-8"), "application/json"

    async def _run_reauth_job(self, job_id: str) -> None:
        job = await self._require_job(job_id)
        account = next((item for item in self._load_inventory().accounts if item.account_id == job.account_id), None)
        if account is None:
            job.status = "failed"
            job.detail = "The account no longer exists in the inventory."
            job.updated_at = _now_iso()
            job.action_required = "Recreate the account and retry reauthentication."
            job.task = None
            return

        job.status = "validating_provider"
        job.detail = "Checking whether the existing profile is already enough to recover Gemini."
        job.updated_at = _now_iso()
        initial_result = await self.recovery_service.recover_account(job.account_id, allow_browser_launch=True, browser=job.browser)
        response = await self._apply_recovery_result(job, result=initial_result, source="initial_recovery")
        if response.status != "awaiting_login":
            return

        deadline = asyncio.get_running_loop().time() + self.settings.admin_reauth_timeout_seconds
        while True:
            await asyncio.sleep(self.settings.admin_reauth_poll_interval_seconds)
            try:
                job = await self._require_job(job_id)
            except ServiceError:
                return
            if job.status in {"completed", "failed", "cancelled"}:
                return
            runtime_summary = self._runtime_summary(job.account_id)
            if self._summary_is_recovered(runtime_summary):
                self._complete_job_from_summary(
                    job,
                    summary=runtime_summary,
                    source="browser_monitor_runtime",
                    detail="Account became routable again while browser monitoring was active.",
                )
                return
            if asyncio.get_running_loop().time() >= deadline:
                job.status = "failed"
                job.detail = "Timed out waiting for Gemini login to finish in the browser."
                job.updated_at = _now_iso()
                job.action_required = "Use browser focus and make sure gemini.google.com/app can answer one message in that account profile."
                job.task = None
                return
            result = await self.recovery_service.recover_account(
                job.account_id,
                allow_browser_launch=False,
                browser=job.browser,
            )
            response = await self._apply_recovery_result(job, result=result, source="browser_monitor")
            if response.status in {"completed", "failed"}:
                return

    async def _apply_recovery_result(
        self,
        job: ReauthJob,
        *,
        result: AccountRecoveryResult,
        source: str,
    ) -> AdminReauthJobResponse:
        if result.browser:
            job.browser = result.browser
        if result.profile_dir:
            job.profile_dir = result.profile_dir
        if result.recovery_source:
            job.recovery_source = result.recovery_source
        job.updated_at = _now_iso()
        job.result = {
            "source": source,
            "recovery_source": result.recovery_source,
            "cookie_count": result.cookie_count,
            "provider_status": result.provider_status,
            "runtime_state": result.runtime_state,
            "account_models": result.account_models,
            "updated": result.updated,
            "code": result.code,
        }
        if result.status == "completed":
            if job.task is not None and job.task is not asyncio.current_task():
                job.task.cancel()
            job.task = None
            job.status = "completed"
            job.detail = result.detail
            job.action_required = None
            return self._job_response(job)
        if result.status == "awaiting_login":
            job.status = "awaiting_login" if source != "manual_sync" else "collecting_cookies"
            job.detail = result.detail
            job.action_required = result.action
            return self._job_response(job)
        job.status = "failed"
        job.detail = result.detail
        job.action_required = result.action
        if job.task is not None and job.task.done():
            job.task = None
        return self._job_response(job)

    async def _require_job(self, job_id: str) -> ReauthJob:
        async with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ServiceError(
                status_code=404,
                code="reauth_job_not_found",
                message=f"Reauthentication job {job_id} does not exist.",
            )
        return job

    def _load_inventory(self) -> AccountInventory:
        inventory, _, _ = load_account_inventory(Path(self.settings.accounts_config_path), save_clean=True)
        return inventory

    def _save_inventory(self, inventory: AccountInventory) -> None:
        save_account_inventory(Path(self.settings.accounts_config_path), inventory.model_dump(mode="json"))

    def _managed_account_response(
        self,
        account: AccountConfig,
        runtime: AccountSummary | None,
        *,
        browser_health: dict[str, object] | None = None,
    ) -> AdminManagedAccount:
        return AdminManagedAccount(
            account_id=account.account_id,
            enabled=account.enabled,
            provider_backend=account.provider_backend,
            cookie_source_browser=account.cookie_source_browser,
            cookie_source_browser_path=account.cookie_source_browser_path,
            cookie_source_profile_dir=account.cookie_source_profile_dir,
            proxy=account.proxy,
            max_concurrency=account.max_concurrency,
            cooldown_seconds=account.cooldown_seconds,
            request_timeout_seconds=account.request_timeout_seconds,
            verify_ssl=account.verify_ssl,
            tags=account.tags,
            has_cookie_bundle=bool(account.secure_1psid and account.secure_1psidts),
            last_recovery_at=account.last_recovery_at,
            last_recovery_source=account.last_recovery_source,
            browser_online=bool(browser_health.get("browser_online")) if browser_health else False,
            browser_state=str(browser_health.get("state") or "offline") if browser_health else "offline",
            browser_debug_port=int(browser_health.get("debug_port")) if browser_health and browser_health.get("debug_port") is not None else None,
            browser_auto_refresh_enabled=bool(browser_health.get("auto_refresh_enabled")) if browser_health else True,
            last_cookie_sync_at=str(browser_health.get("last_sync_at")) if browser_health and browser_health.get("last_sync_at") else None,
            last_provider_validation_at=str(browser_health.get("last_validated_at")) if browser_health and browser_health.get("last_validated_at") else None,
            last_good_cookie_at=str(browser_health.get("last_good_cookie_at")) if browser_health and browser_health.get("last_good_cookie_at") else None,
            browser_last_error=str(browser_health.get("last_error")) if browser_health and browser_health.get("last_error") else None,
            runtime=runtime,
        )

    def _job_response(self, job: ReauthJob) -> AdminReauthJobResponse:
        return AdminReauthJobResponse(
            job_id=job.job_id,
            account_id=job.account_id,
            status=job.status,
            detail=job.detail,
            browser=job.browser,
            profile_dir=job.profile_dir,
            launched=job.recovery_source == "browser_launch",
            monitoring=job.task is not None and not job.task.done(),
            can_complete=job.status not in {"completed", "cancelled", "failed"},
            is_terminal=job.status in {"completed", "failed", "cancelled"},
            created_at=job.created_at,
            updated_at=job.updated_at,
            action_required=job.action_required,
            launch_url=job.launch_url,
            recovery_source=job.recovery_source,
            result=job.result,
        )

    def _render_session_markdown(self, *, session, history) -> str:
        session_dict = session.model_dump(mode="json") if hasattr(session, "model_dump") else session
        history_dict = history.model_dump(mode="json") if hasattr(history, "model_dump") else history
        lines = [
            f"# {session_dict.get('title') or session_dict.get('session_id')}",
            "",
            f"- Session ID: `{session_dict.get('session_id')}`",
            f"- Account: `{session_dict.get('account_id')}`",
            f"- Routing: `{session_dict.get('routing_policy')}`",
            f"- Status: `{session_dict.get('status')}`",
            f"- Created: {session_dict.get('created_at') or '-'}",
            f"- Updated: {session_dict.get('updated_at') or '-'}",
            "",
        ]
        for item in history_dict.get("items", []):
            lines.append(f"## {item.get('role', 'message').title()}")
            lines.append("")
            content = item.get("content") or ""
            if content:
                lines.append(content)
                lines.append("")
            parts = item.get("parts") or []
            asset_lines = []
            for part in parts:
                if part.get("type") != "asset" or not part.get("asset"):
                    continue
                asset = part["asset"]
                asset_lines.append(
                    f"- Attachment: `{asset.get('filename')}` ({asset.get('mime_type')}, {asset.get('size_bytes')} bytes)"
                )
            if asset_lines:
                lines.extend(asset_lines)
                lines.append("")
        return "\n".join(lines).strip() + "\n"
