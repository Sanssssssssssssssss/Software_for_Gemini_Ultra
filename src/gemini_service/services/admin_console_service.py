from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..core.browser_cookie_sync import (
    BrowserLoginSession,
    launch_browser_login_session,
    normalize_profile_dir,
    resolve_browser_path,
)
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
    session: BrowserLoginSession | None = None
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
        telemetry: TelemetryService | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.pool = pool
        self.chat_service = chat_service
        self.asset_service = asset_service
        self.recovery_service = recovery_service
        self.telemetry = telemetry
        self._jobs: dict[str, ReauthJob] = {}
        self._jobs_lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._jobs_lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.task is not None:
                job.task.cancel()
            await self._terminate_job_session(job)

    async def build_dashboard(self, auth: AuthContext) -> AdminDashboardResponse:
        sessions = await self.chat_service.list_sessions(auth=auth, limit=50)
        accounts = await self.pool.list_account_summaries(force_refresh=True)
        runtime_by_id = {item.account_id: item for item in accounts}
        inventory = self._load_inventory()
        inventory_accounts = [
            self._managed_account_response(account, runtime_by_id.get(account.account_id))
            for account in inventory.accounts
        ]
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
        next_account = AccountConfig(
            account_id=account_id,
            enabled=request.enabled,
            provider_backend=request.provider_backend,
            secure_1psid=(request.secure_1psid or "").strip(),
            secure_1psidts=(request.secure_1psidts or None),
            cookie_source_browser=request.cookie_source_browser or None,
            cookie_source_browser_path=request.cookie_source_browser_path or None,
            cookie_source_profile_dir=request.cookie_source_profile_dir or None,
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
        return self._managed_account_response(next_account, runtime.summary() if runtime else None)

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
            jobs = [job for job in self._jobs.values() if job.account_id == account_id and job.session is not None]
        for job in jobs:
            await self._terminate_job_session(job)
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
        async with self._jobs_lock:
            self._jobs[job.job_id] = job
            job.task = asyncio.create_task(self._run_reauth_job(job.job_id))
        return self._job_response(job)

    async def complete_reauth_job(self, job_id: str) -> AdminReauthJobResponse:
        job = await self._require_job(job_id)
        if job.status == "completed":
            return self._job_response(job)
        if job.session is None:
            raise ServiceError(
                status_code=409,
                code="reauth_job_inactive",
                message="This reauthentication job is no longer active.",
            )
        result = await self.recovery_service.recover_account(
            job.account_id,
            allow_browser_launch=False,
            browser_session=job.session,
            browser=job.browser,
        )
        return await self._apply_recovery_result(job, result=result, source="manual_sync")

    async def cancel_reauth_job(self, job_id: str) -> AdminReauthJobResponse:
        job = await self._require_job(job_id)
        if job.task is not None:
            job.task.cancel()
            job.task = None
        await self._terminate_job_session(job)
        job.status = "cancelled"
        job.detail = "Reauthentication was cancelled by the operator."
        job.updated_at = _now_iso()
        job.action_required = None
        return self._job_response(job)

    def list_reauth_jobs(self) -> list[AdminReauthJobResponse]:
        jobs = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
        return [self._job_response(job) for job in jobs[:20]]

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

    async def _terminate_job_session(self, job: ReauthJob) -> None:
        if job.session is None:
            return
        from ..core.browser_cookie_sync import terminate_browser_login_session

        terminate_browser_login_session(job.session)
        job.session = None

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
        initial_result = await self.recovery_service.recover_account(
            job.account_id,
            allow_browser_launch=True,
            browser=job.browser,
        )
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
            if job.status in {"completed", "failed", "cancelled"} or job.session is None:
                return
            if job.session.process.poll() is not None:
                job.status = "failed"
                job.detail = "The browser window closed before Gemini became available to the service."
                job.updated_at = _now_iso()
                job.action_required = "Restart reauthentication and keep the browser open until Gemini chat works there."
                job.task = None
                return
            if asyncio.get_running_loop().time() >= deadline:
                await self._terminate_job_session(job)
                job.status = "failed"
                job.detail = "Timed out waiting for Gemini login to finish in the browser."
                job.updated_at = _now_iso()
                job.action_required = "Restart reauthentication and complete Gemini login in the opened browser."
                job.task = None
                return
            result = await self.recovery_service.recover_account(
                job.account_id,
                allow_browser_launch=False,
                browser_session=job.session,
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
        if result.session is not None and job.session is None:
            job.session = result.session
        if result.browser:
            job.browser = result.browser
        if result.profile_dir:
            job.profile_dir = result.profile_dir
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
            await self._terminate_job_session(job)
            if job.task is not None and job.task is not asyncio.current_task():
                job.task.cancel()
            job.task = None
            job.status = "completed"
            job.detail = result.detail
            job.action_required = None
            return self._job_response(job)
        if result.status == "awaiting_login":
            job.status = "awaiting_login"
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
            launched=job.session is not None,
            monitoring=job.task is not None and not job.task.done(),
            can_complete=job.session is not None and job.status not in {"completed", "cancelled", "failed"},
            is_terminal=job.status in {"completed", "failed", "cancelled"},
            created_at=job.created_at,
            updated_at=job.updated_at,
            action_required=job.action_required,
            launch_url=job.launch_url,
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
